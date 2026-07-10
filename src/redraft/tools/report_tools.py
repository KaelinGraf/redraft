"""assemble_report / briefing / overview MCP tools — the report-assembly, topic-briefing, and
session-start-overview surface over report.py's pure query layer.

All three open their OWN per-call connection and never touch state.store.con, so — same
threading reasoning as retrieval_tools.py's search_nodes/find_similar/integrity tools — none
needs run_in_thread=False; FastMCP's default (True) runs each body on a worker thread, which
is all "own connection, no shared-connection cross-thread risk" requires. briefing
additionally needs sqlite-vec loaded (its search_nodes call touches vec_nodes), so it reuses
retrieval_tools.open_conn rather than read_tools.index_read_conn, exactly like
retrieval_tools.py's own search_nodes/find_similar do; overview needs only plain nodes/edges
SQL (embedding-free by design — see report.overview()'s own docstring), so it uses
read_tools.index_read_conn like assemble_report does.

report.py is imported INSIDE register_report_tools(), not at module level, to break a real
circular import (confirmed live, not theoretical): report.py itself imports
tools.read_tools, which -- as an import of any submodule of the `tools` package always does
-- forces `tools/__init__.py` to run to completion first, and that `__init__.py` imports
THIS module. A module-level `from redraft.report import ...` here would then try to bind
names off a `redraft.report` that is still mid-execution, whenever anything imports
`redraft.report` before `redraft.tools` has been touched by anything else (resources.py does
exactly this now, composing report.py's overview()/root_ids()/type_status_counts() for the
graph://project/overview resource). Deferring to call time (register_report_tools() only
ever runs once, at server startup, long after every module here has finished loading) costs
nothing and needs no other module's import order to cooperate.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import FastMCP

from redraft.models import BriefingResult, EdgeType, ProjectOverview, ReportBundle
from redraft.tool_errors import InvalidArgumentError, translate_store_errors
from redraft.tools.read_tools import index_read_conn
from redraft.tools.retrieval_tools import open_conn

if TYPE_CHECKING:
    from redraft.server import ServerState


def register_report_tools(mcp: FastMCP, state: "ServerState") -> None:
    from redraft.report import assemble_report as lib_assemble_report, briefing as lib_briefing, overview as lib_overview

    @mcp.tool
    def assemble_report(root_id: str, include_edge_types: list[EdgeType] | None = None, depth: int = 4) -> ReportBundle:
        """Collapse root_id's part_of subtree into a report bundle: nested sections with
        attached cross-links, decision_tables grouped by driver question/requirement (any
        status, cross-branch — see report-bundle-v2.md), and per-section gaps (open
        questions, decisions lacking rationale). The agent writes the narrative; this never
        summarizes."""
        if depth < 0:
            raise InvalidArgumentError("depth must be >= 0", depth=depth)
        with index_read_conn(state.config.graph_dir) as conn:
            with translate_store_errors():
                return lib_assemble_report(conn, root_id, include_edge_types=include_edge_types, depth=depth)

    @mcp.tool
    def briefing(query: str, k: int = 5) -> BriefingResult:
        """One-call topic guidance: hybrid search hits, their 1-hop neighborhood, open
        questions, and unjustified decisions scoped to the topic."""
        with open_conn(state.config.graph_dir) as conn:
            return lib_briefing(conn, state.config.retrieval_config, query, k=k)

    @mcp.tool
    def overview() -> ProjectOverview:
        """Cheap, shallow map of the project's shape: every spine root, each root's direct
        part_of children as branches with per-subtree tallies (descendants, open questions,
        decisions by status, unjustified decisions), whole-graph totals, and the top open
        questions. Call this (or read graph://project/overview) at the start of a session,
        before anything else, to load the project's shape and make your first real call
        targeted instead of blind."""
        with index_read_conn(state.config.graph_dir) as conn:
            return lib_overview(conn)
