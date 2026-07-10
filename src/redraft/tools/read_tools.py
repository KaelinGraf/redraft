"""Read tools: get_node, neighbors, get_subgraph.

Per design-storage.md section 6.2 ("reads never acquire [the write lock];
get_node, neighbors, search_nodes, integrity queries all read the SQLite
index directly, lock-free"), these tools do NOT go through GraphStore for
traversal -- GraphStore's own pinned public surface (design-storage.md
section 8) exposes only a single-node get_node(id), no neighbor/subgraph
querying. Instead, each opens its own short-lived, read-only connection
straight to the derived index DB at <graph_dir>/index/graph.sqlite3
(design-storage.md section 5.1/5.3) and runs the recursive-CTE traversal
from design-server.md section 7 directly. PRAGMA query_only=ON (verified
against the installed sqlite3 module) makes accidental writes from this
module impossible at the connection level, standing in for the URI
mode=ro approach design-server.md sketched -- simpler, no path-escaping
edge cases, equally effective since this layer never issues writes.

INTEGRATION BUG FOUND AND FIXED (I7, surfaced only against the real GraphStore): every
tool below is now registered with run_in_thread=False too, for the same reason as every
write_tools.py tool (see that module's "CRITICAL BUG FOUND" docstring) -- despite each
tool's OWN traversal running through its own index_read_conn (immune by construction, as
originally claimed), get_node (via node_with_neighbors) and the existence checks in
neighbors/get_subgraph all call state.store.get_node(id), which touches GraphStore's
single shared, main-thread-created sqlite3.Connection. Under FastMCP's default
run_in_thread=True these bodies run on a worker thread, raising "SQLite objects created in
a thread can only be used in that same thread" on the very first call. This was invisible
in S2's stub-based tests because tests/_stub_store.py's GraphStore opened its connection
with check_same_thread=False specifically as a defensive stopgap (see that file's own
comment) -- the real GraphStore (design-storage.md section 6.2) does not, and should not:
that flag would silently paper over genuine concurrent-access bugs the write path already
works hard to avoid via its single write lock.

search_nodes/find_similar (retrieval, S3b scope) are NOT implemented here.

DESIGN GAP FLAGGED (see final report): design-server.md's NeighborEdge model
carries `direction: Literal["out","in"]` "relative to the queried node" --
well-defined for a single-hop neighbor, but the model doesn't say what
direction means for an edge between two non-root nodes discovered at
neighbor_depth > 1. Conservative resolution taken here: NodeWithNeighbors's
`.edges` list is always just the direct (single-hop) edges of the queried
node itself, regardless of neighbor_depth -- `.neighbors` (full NodeOut
list) still reflects the full multi-hop reachable set. get_subgraph has no
such ambiguity: SubgraphOut.edges uses EdgeOut (no `direction` field), so it
freely returns every edge among the induced node set at any depth.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from fastmcp import FastMCP

from redraft.tool_errors import InvalidArgumentError, translate_store_errors
from redraft.models import (
    Direction,
    EdgeOut,
    EdgeType,
    NeighborEdge,
    NodeOut,
    NodeWithNeighbors,
    SubgraphOut,
    to_node_out,
)

if TYPE_CHECKING:
    from redraft.server import ServerState

# Runaway-query guard, independent of the caller's requested depth
# (design-server.md section 7: "cap max_depth at a generous ... guard (e.g.
# 500), purely defensive").
_MAX_TRAVERSAL_DEPTH = 500


def _is_empty_filter(edge_types: list[str] | None) -> bool:
    """True iff edge_types was explicitly passed as [] (as opposed to
    omitted/None). BUG FOUND AND FIXED (see final report): design-server.md
    section 6's own hybrid_search filter uses `types is None or x in types`
    -- under that established pattern, an explicit empty list must match
    NOTHING (`x in []` is always False), distinct from None ("no filter").
    query_edges/traverse/get_subgraph all short-circuit on this rather than
    building `... IN ()`, which is invalid SQL (a syntax error, not merely
    "matches nothing") in SQLite for a zero-element IN-list.
    """
    return edge_types is not None and len(edge_types) == 0


def index_db_path(graph_dir: Path) -> Path:
    """<graph_dir>/index/graph.sqlite3, per design-storage.md section 5.3."""
    return graph_dir / "index" / "graph.sqlite3"


@contextmanager
def index_read_conn(graph_dir: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(index_db_path(graph_dir)))
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def _row_to_node_out(row: tuple[Any, ...]) -> NodeOut:
    id_, type_, title, body, status, properties_json, created, updated = row
    return NodeOut(
        id=id_, type=type_, title=title, body=body, status=status,
        properties=json.loads(properties_json) if properties_json else {},
        created=created, updated=updated,
    )


def fetch_nodes(conn: sqlite3.Connection, ids: Any) -> list[NodeOut]:
    """Full NodeOut rows for a set/iterable of node ids, straight from the
    index's `nodes` table (design-storage.md section 5.1 column list)."""
    ids = list(dict.fromkeys(ids))  # de-dup, preserve order
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT id, type, title, body, status, properties, created, updated "
        f"FROM nodes WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    return [_row_to_node_out(r) for r in rows]


def query_edges(
    conn: sqlite3.Connection,
    *,
    anchor_col: str,
    anchor_val: str,
    edge_types: list[str] | None,
    direction: str,
) -> list[NeighborEdge]:
    """Edges touching one node on `anchor_col` ('src' or 'dst' -- always
    Python-controlled, never interpolated from tool-call input), tagged with
    `direction` (design-server.md section 7: "direction filter applied in
    Python by issuing 1 or 2 of these and tagging 'direction'")."""
    if _is_empty_filter(edge_types):
        return []
    sql = f"SELECT src, dst, type FROM edges WHERE {anchor_col} = ?"
    params: list[str] = [anchor_val]
    if edge_types:
        placeholders = ",".join("?" for _ in edge_types)
        sql += f" AND type IN ({placeholders})"
        params.extend(edge_types)
    rows = conn.execute(sql, params).fetchall()
    return [NeighborEdge(src=r[0], dst=r[1], type=r[2], direction=direction) for r in rows]


def traverse(
    conn: sqlite3.Connection,
    root: str,
    edge_types: list[str] | None,
    max_depth: int,
    direction: str,
) -> dict[str, int]:
    """{node_id: min_hop_count} reachable from root within max_depth hops,
    root itself excluded. Cycle-safe path-guarded recursive CTE (design-
    server.md section 7): UNION ALL (not UNION) plus an explicit
    `path NOT LIKE` guard, since UNION's row-level distinctness alone
    wouldn't prevent revisits (path differs per row). Defensive even though
    part_of cycles are rejected at write time -- a hand-edited file or a bad
    git merge could still transiently introduce one; this must terminate at
    max_depth, not hang.
    """
    if _is_empty_filter(edge_types):
        return {}
    max_depth = min(max_depth, _MAX_TRAVERSAL_DEPTH)
    dir_clause = {
        "out": "e.src = f.node_id",
        "in": "e.dst = f.node_id",
        "both": "(e.src = f.node_id OR e.dst = f.node_id)",
    }[direction]
    type_clause = ""
    extra_params: list[str] = []
    if edge_types:
        placeholders = ",".join("?" for _ in edge_types)
        type_clause = f"AND e.type IN ({placeholders})"
        extra_params = list(edge_types)
    sql = f"""
    WITH RECURSIVE frontier(node_id, depth, path) AS (
      SELECT ?, 0, ',' || ? || ','
      UNION ALL
      SELECT
        CASE WHEN e.src = f.node_id THEN e.dst ELSE e.src END,
        f.depth + 1,
        f.path || CASE WHEN e.src = f.node_id THEN e.dst ELSE e.src END || ','
      FROM edges e
      JOIN frontier f ON {dir_clause}
      WHERE f.depth < ?
        {type_clause}
        AND f.path NOT LIKE '%,' || CASE WHEN e.src = f.node_id THEN e.dst ELSE e.src END || ',%'
    )
    SELECT node_id, MIN(depth) FROM frontier WHERE node_id != ? GROUP BY node_id
    """
    bind = [root, root, max_depth, *extra_params, root]
    return dict(conn.execute(sql, bind).fetchall())


def node_with_neighbors(state: "ServerState", conn: sqlite3.Connection, id: str, neighbor_depth: int) -> NodeWithNeighbors:
    """Shared by the get_node tool and the graph://project/node/{id} resource."""
    with translate_store_errors():
        node = state.store.get_node(id)
    node_out = to_node_out(node)
    if neighbor_depth <= 0:
        return NodeWithNeighbors(node=node_out, neighbors=[], edges=[])
    depths = traverse(conn, id, None, neighbor_depth, "both")
    neighbor_nodes = fetch_nodes(conn, depths.keys())
    edges = (
        query_edges(conn, anchor_col="src", anchor_val=id, edge_types=None, direction="out")
        + query_edges(conn, anchor_col="dst", anchor_val=id, edge_types=None, direction="in")
    )
    return NodeWithNeighbors(node=node_out, neighbors=neighbor_nodes, edges=edges)


def register_read_tools(mcp: FastMCP, state: "ServerState") -> None:
    @mcp.tool(run_in_thread=False)
    def get_node(id: str, neighbor_depth: int = 0) -> NodeWithNeighbors:
        """Fetch a node. neighbor_depth > 0 also returns nodes reachable within
        that many hops (any edge type, any direction) and the node's own
        direct edges."""
        if neighbor_depth < 0:
            raise InvalidArgumentError("neighbor_depth must be >= 0", neighbor_depth=neighbor_depth)
        with index_read_conn(state.config.graph_dir) as conn:
            return node_with_neighbors(state, conn, id, neighbor_depth)

    @mcp.tool(run_in_thread=False)
    def neighbors(
        id: str,
        edge_types: list[EdgeType] | None = None,
        direction: Direction = "both",
    ) -> list[NeighborEdge]:
        """Single-hop edges touching `id`, optionally filtered by type/direction."""
        with translate_store_errors():
            state.store.get_node(id)  # existence check; raises not_found
        with index_read_conn(state.config.graph_dir) as conn:
            out: list[NeighborEdge] = []
            if direction in ("out", "both"):
                out += query_edges(conn, anchor_col="src", anchor_val=id, edge_types=edge_types, direction="out")
            if direction in ("in", "both"):
                out += query_edges(conn, anchor_col="dst", anchor_val=id, edge_types=edge_types, direction="in")
            return out

    @mcp.tool(run_in_thread=False)
    def get_subgraph(
        root_id: str,
        edge_types: list[EdgeType] | None = None,
        depth: int = 3,
    ) -> SubgraphOut:
        """Everything connected to root_id within `depth` hops, any direction."""
        if depth < 0:
            raise InvalidArgumentError("depth must be >= 0", depth=depth)
        with translate_store_errors():
            state.store.get_node(root_id)  # existence check; raises not_found
        with index_read_conn(state.config.graph_dir) as conn:
            depths = traverse(conn, root_id, edge_types, depth, "both")
            node_ids = [root_id, *depths.keys()]
            nodes = fetch_nodes(conn, node_ids)

            if _is_empty_filter(edge_types):
                edges: list[EdgeOut] = []
            else:
                placeholders = ",".join("?" for _ in node_ids)
                type_clause = ""
                params: list[str] = [*node_ids, *node_ids]
                if edge_types:
                    type_ph = ",".join("?" for _ in edge_types)
                    type_clause = f"AND type IN ({type_ph})"
                    params.extend(edge_types)
                rows = conn.execute(
                    f"SELECT src, dst, type FROM edges WHERE src IN ({placeholders}) "
                    f"AND dst IN ({placeholders}) {type_clause}",
                    params,
                ).fetchall()
                edges = [EdgeOut(src=r[0], dst=r[1], type=r[2]) for r in rows]
        return SubgraphOut(nodes=nodes, edges=edges)
