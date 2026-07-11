"""Lane-B read compositions (s6-ui.md §3.3/§3.4): outline_nodes, node_detail,
node_neighbors, attention_summary, dedup_hints, list_reports, read_report.

Every function here takes an already-open sqlite3.Connection (read_tools.index_read_conn)
as its first argument and NEVER touches StoreWorker -- existence checks that read_tools.py's
own MCP-facing helpers do via `state.store.get_node(id)` are done here instead via a plain
SELECT against the SAME connection already open for the real query (see node_detail's
docstring). This means no read-only UI endpoint ever contends with the write-serializing
Lane-A queue: a human reading a node never blocks behind a concurrent edit or a background
reindex poll tick, and vice versa (s6-ui.md §3.4/§3.5).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from redraft.errors import NotFoundError
from redraft.models import DanglingEdge, NeighborEdge, NodeWithNeighbors, SearchHit, to_node_out, to_search_hit
from redraft.retrieval import find_similar as lib_find_similar, integrity
from redraft.retrieval.embeddings import RetrievalConfig
from redraft.retrieval.fts import fts_candidates
from redraft.schema import EdgeType, NodeType
from redraft.tools.read_tools import fetch_nodes, query_edges, traverse
from redraft.ui.models import (
    AttentionOut,
    DedupHintsOut,
    OutlineEdge,
    OutlineNode,
    OutlineOut,
    ReportFile,
    TimelineItem,
    TimelineOut,
)

# UI-only default cutoff for GET /api/attention's "stale" signal: a fixed policy choice, not
# derived from a stated requirement (module constant, no config knob -- same posture as
# mutations.MAX_ATTACHMENT_BYTES, s6-ui.md ruling §14.2). The MCP `stale` tool takes an
# explicit before_iso from the calling agent; GET /api/attention has no request params at all
# (s6-ui.md §9's row), so this picks a fixed "hasn't been touched in a month" hygiene window.
_STALE_AFTER_DAYS = 30


def outline_nodes(conn: sqlite3.Connection) -> OutlineOut:
    """GET /api/outline: every node + every edge in one shot -- backs both the Outline tree
    and the Map tab from one cached query (s6-ui.md §9). Nothing existing returns this shape;
    the MCP tool surface deliberately has no "dump everything" primitive (agent-oriented:
    traverse from a root, not dump-everything)."""
    node_rows = conn.execute("SELECT id, type, title, status FROM nodes").fetchall()
    edge_rows = conn.execute("SELECT src, dst, type FROM edges").fetchall()
    return OutlineOut(
        nodes=[OutlineNode(id=r[0], type=r[1], title=r[2], status=r[3]) for r in node_rows],
        edges=[OutlineEdge(src=r[0], dst=r[1], type=r[2]) for r in edge_rows],
    )


def attention_summary(conn: sqlite3.Connection) -> AttentionOut:
    """GET /api/attention: a tiny aggregator composing FOUR existing retrieval.integrity
    functions into one round trip for the RIGHT-hand hygiene strip (open_questions,
    decisions_without_rationale, dangling_edges, stale) -- same "compose several existing
    functions into one payload" pattern resources.py's graph_stats_resource already uses
    (s6-ui.md §9)."""
    before_iso = (datetime.now(timezone.utc) - timedelta(days=_STALE_AFTER_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return AttentionOut(
        open_questions=[to_node_out(n) for n in integrity.open_questions(conn)],
        unjustified_decisions=[to_node_out(n) for n in integrity.decisions_without_rationale(conn)],
        dangling_edges=[DanglingEdge(**d) for d in integrity.dangling_edges(conn)],
        stale=[to_node_out(n) for n in integrity.stale(conn, before_iso)],
    )


def timeline_items(conn: sqlite3.Connection) -> TimelineOut:
    """GET /api/timeline: every node carrying a scheduled `properties.start`/`.due` (any
    type) plus every `milestone`-type node carrying NEITHER (the unscheduled tray) -- the
    Planning dates convention (organizing-protocol.md §2). `properties` IS an index column
    (index.py's DDL; `_upsert_node_index` writes `json.dumps(node.properties, ...)` into it),
    so this reads id/type/title/status/properties straight off `nodes` in one shot -- the
    same one-shot "dump everything" shape `outline_nodes` already uses above -- rather than
    routing through `fetch_nodes` (which would additionally pull body/created/updated this
    endpoint never uses) or re-reading node files off disk.

    start/due are taken AS-IS from `properties`, never date-parsed/ISO-validated here: a
    malformed or non-ISO string must still round-trip to the frontend rather than 500 the
    endpoint -- date validity is the frontend's concern to render or flag, not this
    function's. A start/due value that isn't even a string (off-convention: the protocol
    documents an ISO string, not an arbitrary JSON type) is treated as absent rather than
    handed to TimelineItem -- pydantic would otherwise reject a non-str value at construction
    time, which would turn one node's odd properties into a 500 for the whole tab.

    part_of/depends_on both come from ONE query_edges call per matching item (edge_types=
    ["part_of", "depends_on"], the same helper node_detail below uses for its own edge
    reads), split by `.type` afterward: part_of is scalar by protocol (0 or 1 results),
    depends_on is a list. A milestone with no part_of parent surfaces as part_of=None -- the
    frontend's "ungrouped" lane, not an error."""
    rows = conn.execute("SELECT id, type, title, status, properties FROM nodes").fetchall()
    items: list[TimelineItem] = []
    for node_id, node_type, title, status, properties_json in rows:
        properties = json.loads(properties_json) if properties_json else {}
        start = properties.get("start")
        due = properties.get("due")
        start = start if isinstance(start, str) and start else None
        due = due if isinstance(due, str) and due else None
        if start is None and due is None and node_type != NodeType.MILESTONE:
            continue  # unscheduled non-milestone: not a Timeline item at all
        edges = query_edges(
            conn, anchor_col="src", anchor_val=node_id, edge_types=["part_of", "depends_on"], direction="out"
        )
        part_of = next((e.dst for e in edges if e.type == EdgeType.PART_OF), None)
        depends_on = [e.dst for e in edges if e.type == EdgeType.DEPENDS_ON]
        items.append(
            TimelineItem(
                id=node_id, title=title, type=node_type, status=status,
                start=start, due=due, part_of=part_of, depends_on=depends_on,
            )
        )
    return TimelineOut(items=items)


def node_detail(conn: sqlite3.Connection, node_id: str, neighbor_depth: int) -> NodeWithNeighbors:
    """Mirrors read_tools.node_with_neighbors's output shape exactly, but never touches
    StoreWorker -- existence and node data both come from fetch_nodes(conn, [id]) against the
    connection already open for this call (s6-ui.md §3.4)."""
    rows = fetch_nodes(conn, [node_id])
    if not rows:
        raise NotFoundError(node_id)
    node_out = rows[0]
    if neighbor_depth <= 0:
        return NodeWithNeighbors(node=node_out, neighbors=[], edges=[])
    depths = traverse(conn, node_id, None, neighbor_depth, "both")
    neighbor_nodes = fetch_nodes(conn, depths.keys())
    edges = query_edges(
        conn, anchor_col="src", anchor_val=node_id, edge_types=None, direction="out"
    ) + query_edges(conn, anchor_col="dst", anchor_val=node_id, edge_types=None, direction="in")
    return NodeWithNeighbors(node=node_out, neighbors=neighbor_nodes, edges=edges)


def node_neighbors(
    conn: sqlite3.Connection, node_id: str, edge_types: list[str] | None, direction: str
) -> list[NeighborEdge]:
    """GET /api/nodes/{id}/neighbors -- same own-connection existence check as node_detail,
    never read_tools.query_edges' MCP-facing sibling (which checks via state.store.get_node)."""
    if not fetch_nodes(conn, [node_id]):
        raise NotFoundError(node_id)
    out: list[NeighborEdge] = []
    if direction in ("out", "both"):
        out += query_edges(conn, anchor_col="src", anchor_val=node_id, edge_types=edge_types, direction="out")
    if direction in ("in", "both"):
        out += query_edges(conn, anchor_col="dst", anchor_val=node_id, edge_types=edge_types, direction="in")
    return out


def dedup_hints(
    conn: sqlite3.Connection, config: RetrievalConfig, title: str, k: int, embedder_ready: bool
) -> DedupHintsOut:
    """GET /api/dedup-hints (s6-ui.md §5.2). Warm: hybrid_search.find_similar, unchanged.
    Degraded (embedder not warm yet): FTS-only, NEVER touches the embedding model, never
    blocks a form on a cold model load. `score` in degraded mode is a synthetic rank-position
    value (1/(1+i)), not a calibrated bm25 number -- fts_candidates only returns an ordered id
    list. Both branches run on Lane B unconditionally -- even once warm, find_similar never
    goes through StoreWorker, so a slow vector scan can never head-of-line-block a concurrent
    node edit."""
    if embedder_ready:
        hits = lib_find_similar(conn, config, title, k=k)
        return DedupHintsOut(hits=[to_search_hit(h) for h in hits], degraded=False)
    ids = fts_candidates(conn, title, k)
    nodes = fetch_nodes(conn, ids)
    hits = [
        SearchHit(node=n, score=1.0 / (1 + i), matched_fts=True, matched_vector=False)
        for i, n in enumerate(nodes)
    ]
    return DedupHintsOut(hits=hits, degraded=True)


def list_reports(reports_dir: Path) -> list[ReportFile]:
    """GET /api/reports: Path.glob("*.md") + Path.glob("*.tex") + stat() over reports/ --
    nothing existing lists this directory (s6-ui.md §9). Formal technical reports are authored
    as LaTeX (.tex, rendered in the operator UI); lightweight summaries may stay markdown --
    both are first-class report files (organizing-protocol.md §7). [] (not an error) if
    reports/ doesn't exist yet -- no report has ever been saved, which is a legitimate, common
    state, not a fault."""
    if not reports_dir.is_dir():
        return []
    out = []
    paths = sorted({*reports_dir.glob("*.md"), *reports_dir.glob("*.tex")}, key=lambda p: p.name)
    for path in paths:
        st = path.stat()
        out.append(
            ReportFile(
                filename=path.name,
                size=st.st_size,
                modified_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
    return out


def read_report(reports_dir: Path, filename: str) -> str:
    """GET /api/reports/{filename}. Path-traversal guard, belt-and-braces: PurePosixPath(...)
    .name strips directory components, but a BARE ".." survives that unchanged (verified
    empirically -- PurePosixPath("..").name == ".." exactly, unlike "." which reduces to "").
    The independent is_relative_to() re-check on the RESOLVED path is what actually closes
    this: for a plain "..", reports_dir's own parent is virtually always a directory (so
    .is_file() alone would incidentally reject it too), but for a SYMLINK planted inside
    reports_dir pointing outside it (e.g. by a malicious git merge) -- a perfectly
    normal-looking filename with no dots/slashes of its own -- .is_file() alone would happily
    follow the symlink and return True for the escaped target; is_relative_to() is what
    actually refuses that case (verified empirically, see test_ui_reports.py).
    """
    safe_name = PurePosixPath(filename).name
    if not safe_name:
        raise NotFoundError(filename)
    resolved_dir = reports_dir.resolve()
    resolved_path = (resolved_dir / safe_name).resolve()
    if not resolved_path.is_relative_to(resolved_dir) or not resolved_path.is_file():
        raise NotFoundError(filename)
    return resolved_path.read_text(encoding="utf-8")
