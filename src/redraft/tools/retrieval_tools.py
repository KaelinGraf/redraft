"""search_nodes / find_similar (hybrid retrieval) MCP tools, plus the seven integrity/
hygiene queries as thin sync wrappers -- the S3b MCP surface over the retrieval/ library
layer (embeddings, hybrid_search, integrity).

search_nodes/find_similar open their OWN sqlite3.Connection per call (open_conn below)
rather than touching state.store.con -- unlike every write_tools.py/read_tools.py/
admin_tools.py tool (all forced to run_in_thread=False because they share GraphStore's
single, main-thread-created connection -- see write_tools.py's "CRITICAL BUG FOUND"
docstring), these two have no cross-thread sqlite3.Connection to violate, so they are left
at FastMCP's default run_in_thread=True: a cold model load or a slow vector scan then runs
on a worker thread instead of blocking the event loop.

The seven integrity tools are plain sync wrappers over integrity.py's pure-SQL queries (no
sqlite-vec, no embedding model involved). They also never touch state.store.con -- each
opens its own read_tools.index_read_conn, exactly like get_node/neighbors/get_subgraph do
-- so the same reasoning applies and run_in_thread is left at its default (True) here too.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator

import sqlite_vec
from fastmcp import Context, FastMCP

from redraft.models import (
    ContradictionPair,
    DanglingEdge,
    NodeOut,
    NodeType,
    SearchHit,
    to_node_out,
    to_search_hit,
)
from redraft.retrieval import find_similar as lib_find_similar, integrity, search_nodes as lib_search_nodes
from redraft.tools.read_tools import index_db_path, index_read_conn

if TYPE_CHECKING:
    from redraft.server import ServerState


@contextmanager
def open_conn(graph_dir: Path) -> Iterator[sqlite3.Connection]:
    """Per-call connection for search_nodes/find_similar: same on-disk index db as
    read_tools.index_read_conn, additionally with sqlite-vec loaded -- needed for
    knn()/vec_nodes, which the plain nodes/edges queries read_tools.py runs never touch.
    """
    conn = sqlite3.connect(str(index_db_path(graph_dir)))
    conn.execute("PRAGMA query_only = ON")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    try:
        yield conn
    finally:
        conn.close()


def _run_hybrid(graph_dir: Path, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> list[SearchHit]:
    """Runs a hybrid_search.py library function (lib_search_nodes or lib_find_similar)
    against a fresh open_conn and maps its results to our own Pydantic SearchHit. The whole
    open-call-map sequence must run as ONE synchronous unit inside asyncio.to_thread -- a
    sqlite3.Connection can only be used on the thread that created it, so open_conn cannot
    be entered on the event-loop thread and its connection then handed to a worker thread.
    """
    with open_conn(graph_dir) as conn:
        hits = fn(conn, *args, **kwargs)
    return [to_search_hit(h) for h in hits]


def register_search_tools(mcp: FastMCP, state: "ServerState") -> None:
    @mcp.tool
    async def search_nodes(
        query: str,
        ctx: Context,
        types: list[NodeType] | None = None,
        status: str | None = None,
        k: int = 10,
    ) -> list[SearchHit]:
        """Hybrid FTS+vector search, fused by Reciprocal Rank Fusion. types/status filter
        each branch's candidates before fusion (a filtered-out result never consumes a
        fusion rank slot it wasn't really competing within). Prefer this over grepping graph
        files for ANY lookup -- it matches paraphrase, filters by type/status, and returns
        typed nodes, which grep cannot."""
        await ctx.info(f"search_nodes: query={query!r} (embedding model may still be cold-loading)")
        return await asyncio.to_thread(
            _run_hybrid, state.config.graph_dir, lib_search_nodes, state.config.retrieval_config,
            query, types=types, status=status, k=k,
        )

    @mcp.tool
    async def find_similar(text_or_id: str, ctx: Context, k: int = 5) -> list[SearchHit]:
        """Vector-only near-duplicate search -- the dedup primitive. text_or_id may be an
        existing node's id (reuses its cached vector, excludes it from its own results) or
        free text (embedded fresh). Prefer this over grepping graph files to check "is this
        already recorded" -- it matches paraphrase across different wording, which grep
        cannot."""
        await ctx.info(f"find_similar: text_or_id={text_or_id!r} (embedding model may still be cold-loading)")
        return await asyncio.to_thread(
            _run_hybrid, state.config.graph_dir, lib_find_similar, state.config.retrieval_config, text_or_id, k=k,
        )


def register_integrity_tools(mcp: FastMCP, state: "ServerState") -> None:
    @mcp.tool
    def decisions_without_rationale() -> list[NodeOut]:
        """Decisions (any status) with no inbound `justifies` edge."""
        with index_read_conn(state.config.graph_dir) as conn:
            return [to_node_out(d) for d in integrity.decisions_without_rationale(conn)]

    @mcp.tool
    def open_questions() -> list[NodeOut]:
        """Question nodes with status='open'."""
        with index_read_conn(state.config.graph_dir) as conn:
            return [to_node_out(d) for d in integrity.open_questions(conn)]

    @mcp.tool
    def orphans() -> list[NodeOut]:
        """Nodes with zero edges in any direction/type (a legitimate root-level concept
        with no part_of parent is NOT flagged for that alone)."""
        with index_read_conn(state.config.graph_dir) as conn:
            return [to_node_out(d) for d in integrity.orphans(conn)]

    @mcp.tool
    def contradictions() -> list[ContradictionPair]:
        """{a, b} node pairs linked by a `contradicts` edge (a dangling contradicts edge is
        surfaced by dangling_edges() instead, not here)."""
        with index_read_conn(state.config.graph_dir) as conn:
            return [
                ContradictionPair(a=to_node_out(p["a"]), b=to_node_out(p["b"]))
                for p in integrity.contradictions(conn)
            ]

    @mcp.tool
    def stale(
        before_iso: str, types: list[NodeType] | None = None, statuses: list[str] | None = None
    ) -> list[NodeOut]:
        """Nodes untouched since before_iso. types/statuses omitted (None) use the default
        non-terminal question/decision/milestone set; an explicit [] matches nothing (not
        reinterpreted as 'omitted')."""
        with index_read_conn(state.config.graph_dir) as conn:
            return [to_node_out(d) for d in integrity.stale(conn, before_iso, types=types, statuses=statuses)]

    @mcp.tool
    def dangling_edges() -> list[DanglingEdge]:
        """Edges whose src and/or dst no longer resolves to a node."""
        with index_read_conn(state.config.graph_dir) as conn:
            return [DanglingEdge(**d) for d in integrity.dangling_edges(conn)]

    @mcp.tool
    def case_collisions() -> list[list[str]]:
        """Groups of >=2 node ids identical under Unicode NFC+casefold."""
        with index_read_conn(state.config.graph_dir) as conn:
            return integrity.case_collisions(conn)
