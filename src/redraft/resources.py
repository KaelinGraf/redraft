"""The four graph:// MCP resources (design-server.md section 11).

Read-only, lock-free, same direct-index-DB-access pattern as
tools/read_tools.py. The node resource reuses read_tools.node_with_neighbors
(neighbor_depth=0) rather than re-querying -- composing the existing piece
instead of duplicating "fetch a node" logic a second time. The root/stats
resources' own root-listing and type/status-count logic now lives in
report.root_ids()/type_status_counts() (moved there so overview() -- and any
future caller -- reuses the exact same queries instead of a second copy);
this module just calls back into them.

CONFIRMED LIVE (fastmcp==3.4.3): the node resource lets
node_with_neighbors's translate_store_errors()-mapped NotFoundError (a
ToolError subclass) propagate out of an @mcp.resource handler, and unlike a
tool call (which raises fastmcp.exceptions.ToolError client-side), a
resource read surfaces it to the client as mcp.shared.exceptions.McpError --
a different exception type. The message text is preserved verbatim either
way ("not_found: node 'X' does not exist"), so callers that grep the error
code string (per errors.py's whole design) still work; callers that
`except ToolError` specifically around a read_resource() call will not
catch it. Tests assert against McpError for resource reads, ToolError for
tool calls -- see test_resources.py.

The stats resource's orphan/dangling-edge counts used to inline the counting SQL
design-server.md section 8 / design-storage.md section 5.1 specify for
orphans()/dangling_edges(), from before S3b's retrieval.integrity module existed to call
into. Now that it does, this module reuses integrity.orphans()/dangling_edges() (len() of
each) directly -- the two copies of that SQL consolidated onto one, as flagged here in a
prior slice, as part of adding overview() (report.py), which needed the same two counts.

The overview resource returns the exact same structure as the `overview` MCP tool
(tools/report_tools.py) and `redraft overview`'s markdown source -- see
report.overview()'s own docstring for the full composition.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from redraft.report import overview as lib_overview, root_ids, type_status_counts
from redraft.retrieval import integrity
from redraft.tools.read_tools import fetch_nodes, index_read_conn, node_with_neighbors

if TYPE_CHECKING:
    from redraft.server import ServerState


def register_resources(mcp: FastMCP, state: "ServerState") -> None:
    @mcp.resource("graph://project/root", name="ProjectGraphRoot", mime_type="application/json")
    def graph_root_resource() -> str:
        """Root-level nodes (no part_of parent) plus type/status counts."""
        with index_read_conn(state.config.graph_dir) as conn:
            by_type, by_status = type_status_counts(conn)
            roots = [n.model_dump() for n in fetch_nodes(conn, root_ids(conn))]  # one batched query, not N get_node calls
        return json.dumps({"roots": roots, "counts_by_type": by_type, "counts_by_status": by_status})

    @mcp.resource("graph://project/node/{node_id}", mime_type="application/json")
    def graph_node_resource(node_id: str) -> str:
        """Mirrors get_node(node_id, neighbor_depth=0)."""
        with index_read_conn(state.config.graph_dir) as conn:
            result = node_with_neighbors(state, conn, node_id, 0)
        return result.model_dump_json()

    @mcp.resource("graph://project/stats", mime_type="application/json")
    def graph_stats_resource() -> str:
        """Counts by type/status, orphan count, dangling-edge count."""
        with index_read_conn(state.config.graph_dir) as conn:
            by_type, by_status = type_status_counts(conn)
            orphan_count = len(integrity.orphans(conn))
            dangling_count = len(integrity.dangling_edges(conn))
        return json.dumps(
            {
                "counts_by_type": by_type,
                "counts_by_status": by_status,
                "orphan_count": orphan_count,
                "dangling_edge_count": dangling_count,
            }
        )

    @mcp.resource("graph://project/overview", name="ProjectOverview", mime_type="application/json")
    def graph_overview_resource() -> str:
        """Spine roots, each root's direct branches with per-subtree tallies, whole-graph
        totals, and the top open questions -- see report.overview()."""
        with index_read_conn(state.config.graph_dir) as conn:
            result = lib_overview(conn)
        return result.model_dump_json()
