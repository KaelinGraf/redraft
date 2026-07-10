"""Report assembly + briefing — pure query layer over the derived index (docs/protocol/
report-bundle-v2.md, exactly). No MCP dependency; tools/report_tools.py wraps this for the
MCP surface, same split as retrieval/ vs tools/retrieval_tools.py.

Composes the existing traversal/query primitives rather than re-deriving them: the part_of
spine walk and every 1-hop inbound/outbound query reuse tools.read_tools.traverse /
query_edges / fetch_nodes (the same cycle-guarded recursive-CTE and edge-query helpers
get_node/neighbors/get_subgraph/resources.py already use — resources.py already imports
straight from tools.read_tools for exactly this reason, so this is an established, not a
new, layering). decision_tables/gaps reuse retrieval.integrity.decisions_without_rationale
for the "0 inbound justifies" signal instead of re-querying it.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from redraft.errors import NotFoundError
from redraft.models import (
    BriefingResult,
    ContradictionPair,
    DecisionTableGroup,
    DecisionTableRow,
    NeighborhoodEntry,
    NodeOut,
    OverviewBranch,
    OverviewRoot,
    OverviewTotals,
    ProjectOverview,
    RationaleRef,
    ReportBundle,
    ReportSection,
    SectionGaps,
    to_node_out,
    to_search_hit,
)
from redraft.retrieval import integrity
from redraft.retrieval._util import now_iso
from redraft.retrieval.hybrid_search import HybridSearchConfig, search_nodes as lib_search_nodes
from redraft.schema import LIST_EDGE_TYPES, EdgeType
from redraft.tools.read_tools import fetch_nodes, query_edges, traverse

# Every edge type `attached`/`neighborhood` can surface — everything except part_of, which
# the section tree itself already represents (report-bundle-v2.md section 1's own
# `attached: dict[str, list[NodeOut]]` never carries a "part_of" key). LIST_EDGE_TYPES
# (schema.py) is already exactly "every EdgeType except part_of" -- reused, not re-derived.
_ATTACHABLE_EDGE_TYPES: tuple[str, ...] = tuple(e.value for e in LIST_EDGE_TYPES)

# Generous, defensive cap for supersedes-chain walks (mirrors read_tools._MAX_TRAVERSAL_DEPTH;
# a chain this long would indicate a real graph problem, not a legitimate report).
_MAX_CHAIN_HOPS = 500


def root_ids(conn: sqlite3.Connection) -> list[str]:
    """Node ids with no part_of parent (the spine roots), oldest-created first. The exact
    query graph://project/root (resources.py) lists -- moved here so it is reused, not
    duplicated, by overview() below and resources.py both, and so the two surfaces can never
    disagree on what counts as a root. A fully-disconnected orphan (zero edges at all -- see
    retrieval.integrity.orphans()) has no part_of parent either, so it appears here too;
    that's correct, not a bug -- see overview()'s own docstring.
    """
    return [
        r[0]
        for r in conn.execute(
            "SELECT n.id FROM nodes n WHERE NOT EXISTS "
            "(SELECT 1 FROM edges e WHERE e.src = n.id AND e.type = 'part_of') "
            "ORDER BY n.created ASC, n.id ASC"
        ).fetchall()
    ]


def type_status_counts(conn: sqlite3.Connection) -> tuple[dict[str, int], dict[str, int]]:
    """Whole-graph {type: count} / {status: count} -- moved here (from resources.py) so
    graph://project/root, graph://project/stats, and overview()'s totals all share one copy."""
    by_type = dict(conn.execute("SELECT type, COUNT(*) FROM nodes GROUP BY type").fetchall())
    by_status = dict(
        conn.execute("SELECT status, COUNT(*) FROM nodes WHERE status IS NOT NULL GROUP BY status").fetchall()
    )
    return by_type, by_status


def _node_map(conn: sqlite3.Connection, ids: Any) -> dict[str, NodeOut]:
    return {n.id: n for n in fetch_nodes(conn, ids)}


def _inbound_src_ids(conn: sqlite3.Connection, node_id: str, edge_type: str) -> list[str]:
    return [e.src for e in query_edges(conn, anchor_col="dst", anchor_val=node_id, edge_types=[edge_type], direction="in")]


def _resolve_titles(conn: sqlite3.Connection, ids: list[str], node_map: dict[str, NodeOut]) -> list[str]:
    """Titles for `ids`, fetching any not already cached in `node_map` (and caching them)."""
    missing = [i for i in ids if i not in node_map]
    if missing:
        node_map.update(_node_map(conn, missing))
    return [node_map[i].title for i in ids if i in node_map]


def _supersedes_chain_titles(conn: sqlite3.Connection, decision_id: str, node_map: dict[str, NodeOut]) -> list[str]:
    """Transitive outbound supersedes from `decision_id`, nearest-first (section 2.3) — the
    same cycle-guarded recursive CTE as every other traversal here, filtered to
    type='supersedes', direction='out'."""
    hops = traverse(conn, decision_id, [EdgeType.SUPERSEDES.value], _MAX_CHAIN_HOPS, "out")
    ordered_ids = [nid for nid, _hop in sorted(hops.items(), key=lambda kv: kv[1])]
    return _resolve_titles(conn, ordered_ids, node_map)


def _rationale_refs(conn: sqlite3.Connection, decision_id: str, node_map: dict[str, NodeOut]) -> list[RationaleRef]:
    ids = _inbound_src_ids(conn, decision_id, EdgeType.JUSTIFIES.value)
    missing = [i for i in ids if i not in node_map]
    if missing:
        node_map.update(_node_map(conn, missing))
    rationales = sorted((node_map[i] for i in ids if i in node_map), key=lambda n: n.created)
    return [RationaleRef(title=r.title, body=r.body, tradeoffs=r.properties.get("tradeoffs")) for r in rationales]


def _decision_table_row(conn: sqlite3.Connection, decision: NodeOut, node_map: dict[str, NodeOut]) -> DecisionTableRow:
    rationale = _rationale_refs(conn, decision.id, node_map)
    tradeoffs = next((r.tradeoffs for r in rationale if r.tradeoffs is not None), None)
    superseded_by = _resolve_titles(conn, _inbound_src_ids(conn, decision.id, EdgeType.SUPERSEDES.value), node_map)
    return DecisionTableRow(
        decision=decision,
        rationale=rationale,
        supersedes_chain=_supersedes_chain_titles(conn, decision.id, node_map),
        superseded_by=superseded_by,
        tradeoffs=tradeoffs,
    )


def _decision_table_group(conn: sqlite3.Connection, driver: NodeOut, node_map: dict[str, NodeOut]) -> DecisionTableGroup | None:
    """section 2.2: every decision (any status) with an inbound `addresses` edge to `driver`,
    regardless of whether that decision is itself in-subtree. None (not an empty group) if
    `driver` has zero addressing decisions — that's a gap (section 2.4), not an empty table.

    `addresses` may also be held by an `idea` node (graphrules.py's own edge convention:
    src type decision|idea) — the spec is explicit this table is "every *decision* node", so
    an idea addressing the same driver is filtered out here, not silently admitted as a row
    with a nonsensical DecisionTableRow.decision.status (idea carries no status at all).
    """
    addressing_ids = _inbound_src_ids(conn, driver.id, EdgeType.ADDRESSES.value)
    if not addressing_ids:
        return None
    missing = [i for i in addressing_ids if i not in node_map]
    if missing:
        node_map.update(_node_map(conn, missing))
    decisions = [node_map[i] for i in addressing_ids if i in node_map and node_map[i].type == "decision"]
    if not decisions:
        return None
    rows = [_decision_table_row(conn, d, node_map) for d in decisions]
    rows.sort(key=lambda row: (row.decision.status != "accepted", row.decision.created, row.decision.title))
    return DecisionTableGroup(driver=driver, rows=rows)


def assemble_report(
    conn: sqlite3.Connection,
    root_id: str,
    include_edge_types: list[str] | None = None,
    depth: int = 4,
) -> ReportBundle:
    """report-bundle-v2.md end to end. Raises redraft.errors.NotFoundError if root_id
    doesn't exist (tools/report_tools.py maps this via the standard translate_store_errors
    seam, same as every other read tool)."""
    if not fetch_nodes(conn, [root_id]):
        raise NotFoundError(root_id)

    # section 2.1 "in-subtree": root_id plus everything reachable by a part_of chain of
    # length <= depth, direction='in' (part_of points child -> parent, so an edge *into*
    # root_id is a child) -- the exact traverse() call the spec names.
    hops = traverse(conn, root_id, [EdgeType.PART_OF.value], depth, "in")
    node_map = _node_map(conn, {root_id, *hops})

    attach_types = list(include_edge_types) if include_edge_types is not None else list(_ATTACHABLE_EDGE_TYPES)

    children_of: dict[str, list[str]] = defaultdict(list)
    if hops:
        placeholders = ",".join("?" for _ in hops)
        rows = conn.execute(
            f"SELECT src, dst FROM edges WHERE type = ? AND src IN ({placeholders})",
            (EdgeType.PART_OF.value, *hops),
        ).fetchall()
        for src, dst in rows:
            children_of[dst].append(src)
    for kids in children_of.values():
        kids.sort(key=lambda cid: (node_map[cid].created, cid))  # deterministic sibling order: created ascending

    without_rationale_ids = {d["id"] for d in integrity.decisions_without_rationale(conn)}

    def build_section(node_id: str, hop_depth: int) -> ReportSection:
        attached: dict[str, list[NodeOut]] = {}
        for edge_type in attach_types:
            src_ids = _inbound_src_ids(conn, node_id, edge_type)
            if src_ids:
                attached[edge_type] = fetch_nodes(conn, src_ids)  # section 2.2 note: may reach outside in-subtree

        kids = children_of.get(node_id, [])
        gaps = SectionGaps(
            open_questions=[
                node_map[c] for c in kids if node_map[c].type == "question" and node_map[c].status == "open"
            ],
            decisions_without_rationale=[
                node_map[c] for c in kids if node_map[c].type == "decision" and c in without_rationale_ids
            ],
        )
        return ReportSection(
            node=node_map[node_id],
            depth=hop_depth,
            attached=attached,
            gaps=gaps,
            children=[build_section(c, hop_depth + 1) for c in kids],
        )

    root_section = build_section(root_id, 0)

    flat: list[ReportSection] = []

    def _flatten(section: ReportSection) -> None:
        flat.append(section)
        for child in section.children:
            _flatten(child)

    _flatten(root_section)

    bundle_open_questions: list[NodeOut] = []
    seen_open: set[str] = set()
    for section in flat:
        for q in section.gaps.open_questions:
            if q.id not in seen_open:
                seen_open.add(q.id)
                bundle_open_questions.append(q)

    decision_tables: list[DecisionTableGroup] = []
    for section in flat:
        if section.node.type in ("question", "requirement"):
            group = _decision_table_group(conn, section.node, node_map)
            if group is not None:
                decision_tables.append(group)

    in_subtree_ids = set(node_map)
    contradictions = [
        ContradictionPair(a=to_node_out(pair["a"]), b=to_node_out(pair["b"]))
        for pair in integrity.contradictions(conn)
        if pair["a"]["id"] in in_subtree_ids or pair["b"]["id"] in in_subtree_ids
    ]

    return ReportBundle(
        root_id=root_id,
        generated_at=now_iso(),
        sections=[root_section],
        decision_tables=decision_tables,
        open_questions=bundle_open_questions,
        contradictions=contradictions,
    )


def briefing(conn: sqlite3.Connection, config: HybridSearchConfig, query: str, k: int = 5) -> BriefingResult:
    """report-bundle-v2.md section 6: hybrid search hits anchor a scope (hits + their 1-hop
    neighbors); open_questions/unjustified_decisions filter that scope, same mechanical
    pattern as assemble_report's per-section gaps (section 2.4) applied to a search-anchored
    scope instead of a part_of subtree."""
    hits = lib_search_nodes(conn, config, query, k=k)

    neighborhood: list[NeighborhoodEntry] = []
    scope: set[str] = {h.node["id"] for h in hits}
    for hit in hits:
        node_id = hit.node["id"]
        neighbors = query_edges(
            conn, anchor_col="src", anchor_val=node_id, edge_types=None, direction="out"
        ) + query_edges(conn, anchor_col="dst", anchor_val=node_id, edge_types=None, direction="in")
        neighborhood.append(NeighborhoodEntry(anchor=hit.node["title"], neighbors=neighbors))
        scope.update(e.src for e in neighbors)
        scope.update(e.dst for e in neighbors)

    scoped = _node_map(conn, scope)
    decision_ids = [n.id for n in scoped.values() if n.type == "decision"]
    justified: set[str] = set()
    if decision_ids:
        placeholders = ",".join("?" for _ in decision_ids)
        justified = {
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT dst FROM edges WHERE type = ? AND dst IN ({placeholders})",
                (EdgeType.JUSTIFIES.value, *decision_ids),
            ).fetchall()
        }

    return BriefingResult(
        query=query,
        generated_at=now_iso(),
        hits=[to_search_hit(h) for h in hits],
        neighborhood=neighborhood,
        open_questions=sorted(
            (n for n in scoped.values() if n.type == "question" and n.status == "open"), key=lambda n: n.id
        ),
        unjustified_decisions=sorted(
            (n for n in scoped.values() if n.type == "decision" and n.id not in justified), key=lambda n: n.id
        ),
    )


_EXCERPT_MAX_LEN = 140
_TOP_OPEN_QUESTIONS_LIMIT = 10


def _excerpt(body: str) -> str:
    """First line of `body`, defensively truncated. Bodies are conventionally 1-3 sentences
    (organizing-protocol.md 3.7) but the server never enforces that -- a pasted multi-
    paragraph body must not blow overview()'s one-screen budget."""
    first_line = body.strip().split("\n", 1)[0].strip()
    if len(first_line) > _EXCERPT_MAX_LEN:
        return first_line[:_EXCERPT_MAX_LEN].rstrip() + "…"
    return first_line


def _decision_status_counts(conn: sqlite3.Connection, ids: set[str]) -> dict[str, int]:
    """{status: count} for `decision`-typed nodes in `ids` -- the per-branch analogue of
    type_status_counts' whole-graph by_status, scoped to one branch's subtree."""
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT status, COUNT(*) FROM nodes WHERE type = 'decision' AND id IN ({placeholders}) GROUP BY status",
        list(ids),
    ).fetchall()
    return dict(rows)


def overview(conn: sqlite3.Connection) -> ProjectOverview:
    """Cheap, shallow project map for session start: every SPINE root, each root's direct
    part_of children as "branches" (the major components) with per-subtree tallies,
    whole-graph totals, and the top open questions. One hop below the roots -- NOT the full
    recursive assemble_report bundle -- and embedding-free: no retrieval model is touched
    anywhere in this call, only nodes/edges SQL.

    `roots` is SPINE roots only -- a parentless node that itself anchors a part_of subtree,
    i.e. has >=1 inbound part_of child -- NOT "every parentless node" (that broader contract is
    root_ids()'s own, and stays exactly as-is for graph://project/root, a raw listing rather
    than a curated map). rationale/observation/artifact nodes are parentless BY DESIGN -- they
    attach via justifies/references/derived_from, never part_of (organizing-protocol.md) -- so
    admitting every parentless node as a "root" buried the real spine (e.g. a lone top-level
    concept) under a dozen one-line "## X -- _no branches yet_" sections on any graph with real
    rationale density. A parentless node with zero inbound part_of children is EXCLUDED from
    `roots` and tallied by type in `floating_by_type` instead -- a count, not a per-node list,
    because a well-attached rationale (inbound justifies present) is correct-by-design and must
    not read as an error; only totals-level integrity checks (retrieval.integrity.orphans, the
    stricter "zero edges at all" signal) claim to flag an actual problem. If EVERY parentless
    node turns out childless (a brand-new graph: a lone concept with nothing under it yet),
    `roots` is legitimately empty and `floating_by_type` carries the whole tally --
    render_markdown (overview.py) prints a one-line fallback for that case instead of nothing.

    Kept roots are sorted by their own part_of subtree size (branch count + all of their
    descendants) DESC, then created ASC: root_id_list already arrives created-ASC/id-ASC from
    root_ids(), so a stable sort on size alone reproduces that tiebreak with one sort key.

    Composes existing pieces throughout rather than re-deriving them: root_ids/
    type_status_counts just above (moved here from resources.py, which now calls back into
    them, so the root-listing and stats logic is defined exactly once); read_tools.traverse
    for each branch's subtree (the same cycle-guarded recursive CTE assemble_report uses for
    its own part_of walk); retrieval.integrity.open_questions/decisions_without_rationale,
    each called ONCE up front and then intersected in Python against every branch's
    subtree_ids, exactly mirroring assemble_report's own `without_rationale_ids` pattern.

    subtree_ids per branch is SELF-INCLUSIVE ({branch_id} | descendants), matching
    assemble_report's own "in-subtree" convention -- so a branch that is itself, say, an
    unjustified decision counts toward its own unjustified_decision_count. descendant_count
    is the one exception: it is self-EXCLUSIVE (traverse()'s own "root itself excluded"
    contract), since a node is not its own descendant.
    """
    root_id_list = root_ids(conn)
    by_type, by_status = type_status_counts(conn)
    open_question_rows = integrity.open_questions(conn)
    open_question_ids = {q["id"] for q in open_question_rows}
    unjustified_ids = {d["id"] for d in integrity.decisions_without_rationale(conn)}

    root_node_map = _node_map(conn, root_id_list)

    roots_with_size: list[tuple[OverviewRoot, int]] = []
    floating_by_type: dict[str, int] = defaultdict(int)
    for rid in root_id_list:
        branch_ids = _inbound_src_ids(conn, rid, EdgeType.PART_OF.value)
        if not branch_ids:  # parentless AND childless: off the spine -- tally, don't list as a root
            floating_by_type[root_node_map[rid].type] += 1
            continue

        branch_node_map = _node_map(conn, branch_ids)
        branch_ids.sort(key=lambda cid: (branch_node_map[cid].created, cid))  # deterministic: created ascending

        branches: list[OverviewBranch] = []
        for bid in branch_ids:
            bnode = branch_node_map[bid]
            hops = traverse(conn, bid, [EdgeType.PART_OF.value], _MAX_CHAIN_HOPS, "in")
            subtree_ids = {bid, *hops}  # self-inclusive -- see docstring
            branches.append(
                OverviewBranch(
                    id=bnode.id, title=bnode.title, type=bnode.type, status=bnode.status,
                    excerpt=_excerpt(bnode.body),
                    descendant_count=len(hops),  # self-EXCLUSIVE -- see docstring
                    open_question_count=len(subtree_ids & open_question_ids),
                    decision_counts_by_status=_decision_status_counts(conn, subtree_ids),
                    unjustified_decision_count=len(subtree_ids & unjustified_ids),
                )
            )
        root_node = root_node_map[rid]
        # branches + their descendants == root_node's own subtree size, self-exclusive -- free
        # to derive from the branch tallies already computed above (no extra traverse() call):
        # a node has at most one part_of parent (create_edge's own collision rule), so the
        # branches partition root_node's subtree with no double-counting.
        subtree_size = len(branches) + sum(b.descendant_count for b in branches)
        root_out = OverviewRoot(id=root_node.id, title=root_node.title, type=root_node.type, branches=branches)
        roots_with_size.append((root_out, subtree_size))

    roots_with_size.sort(key=lambda pair: pair[1], reverse=True)  # stable: ties keep created-ASC order
    roots = [r for r, _size in roots_with_size]

    return ProjectOverview(
        roots=roots,
        floating_by_type=dict(floating_by_type),
        totals=OverviewTotals(
            counts_by_type=by_type,
            counts_by_status=by_status,
            orphan_count=len(integrity.orphans(conn)),
            dangling_edge_count=len(integrity.dangling_edges(conn)),
        ),
        top_open_questions=[to_node_out(q) for q in open_question_rows[:_TOP_OPEN_QUESTIONS_LIMIT]],
    )
