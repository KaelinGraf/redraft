"""Pydantic request/response models for the redraft MCP tool surface.

Mirrors design-server.md section 3, amended per the orchestrator's binding
reconciliation pins:
  - R5: NodePatch is dropped entirely. update_node takes flat params that
    match redraft.store.GraphStore.update_node byte-for-byte.
  - R2/R3: EdgeOut/MergeResult reflect strict create_edge (no dangling-dst
    creation, second part_of parent is a collision) and mechanical-merge
    semantics (outbound edges migrated+deduped, part_of conflict is a
    warning, body never auto-merged).

ReindexStats and SnapshotResult intentionally mirror the field names of
redraft.store.GraphStore's own ReindexStats/CommitResult return types
(design-storage.md 5.2 and 7.2) rather than design-server.md's richer,
embedding-aware versions, because admin_tools.py is a thin pass-through over
GraphStore and GraphStore owns those shapes (pin R1). Embedding-related
fields (nodes_embedded, model_changed, ...) belong to S3b's retrieval layer,
which can extend these models when it lands.

S3b adds SearchHit, ContradictionPair, DanglingEdge below for its search_nodes/
find_similar/integrity tool surface (retrieval_tools.py).

S4b adds RationaleRef..BriefingResult below, per docs/protocol/report-bundle-v2.md
section 1 verbatim (reusing NodeOut/NeighborEdge/SearchHit/ContradictionPair
unchanged, exactly as that spec requires -- "no new node-shape is invented
anywhere below").
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NodeType = Literal[
    "concept", "decision", "rationale", "requirement", "constraint",
    "idea", "question", "artifact", "observation", "milestone",
]
EdgeType = Literal[
    "part_of", "justifies", "supersedes", "addresses", "depends_on",
    "contradicts", "references", "derived_from", "relates_to",
]
Direction = Literal["out", "in", "both"]

# Per-type legal status values (brief section 3.1). None = type carries no
# status at all. Used only for cheap, stateless pre-validation in the tool
# layer (see write_tools.py) -- GraphStore is the authority and re-validates
# independently (pin R1); this is a fast-fail nicety, never a source of
# behavioral divergence, since the values come straight from the brief.
STATUS_BY_TYPE: dict[str, frozenset[str] | None] = {
    "decision": frozenset({"proposed", "accepted", "superseded", "rejected"}),
    "question": frozenset({"open", "resolved"}),
    "milestone": frozenset({"planned", "done"}),
    "concept": None,
    "rationale": None,
    "requirement": None,
    "constraint": None,
    "idea": None,
    "artifact": None,
    "observation": None,
}


class NodeOut(BaseModel):
    id: str
    type: NodeType
    title: str
    body: str
    status: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    created: str
    updated: str


class EdgeOut(BaseModel):
    src: str
    dst: str
    type: EdgeType
    dst_exists: bool = True
    warnings: list[str] = Field(default_factory=list)


class EdgeIn(BaseModel):
    """One edge in a create_edges batch call (write_tools.py) -- the same {src, dst, type}
    shape as create_edge's own params, wrapped in a model purely so FastMCP can generate a
    proper list[EdgeIn] input schema for the batch tool."""

    src: str
    dst: str
    type: EdgeType


class NeighborEdge(BaseModel):
    """An edge, tagged with its direction relative to a queried node."""

    src: str
    dst: str
    type: EdgeType
    direction: Literal["out", "in"]


class NodeWithNeighbors(BaseModel):
    node: NodeOut
    neighbors: list[NodeOut] = Field(default_factory=list)
    # Only edges directly touching `node` (well-defined direction). See
    # read_tools.py module docstring for why depth>1 doesn't extend this list.
    edges: list[NeighborEdge] = Field(default_factory=list)


class SubgraphOut(BaseModel):
    nodes: list[NodeOut] = Field(default_factory=list)
    edges: list[EdgeOut] = Field(default_factory=list)


class DeleteResult(BaseModel):
    ok: bool = True
    existed: bool = True
    orphaned_inbound_edges: list[NeighborEdge] = Field(default_factory=list)


class RenameResult(BaseModel):
    old_id: str
    new_id: str
    relinked: list[NeighborEdge] = Field(default_factory=list)
    body_references_not_updated: list[str] = Field(default_factory=list)


class MergeResult(BaseModel):
    """Mirrors GraphStore.merge_nodes' MergeOutcome exactly (design amendment A2, I4) --
    kept/warnings/dropped_body_preview are GraphStore's own observed decision; dropped_id is
    the tool's own call argument. No relinked_inbound/migrated_outbound: GraphStore's
    MergeOutcome doesn't report which specific edges moved, only the net semantic result
    (see write_tools.py module docstring's DESIGN GAP note) -- a caller who needs that
    detail can diff neighbors()/get_node() before and after the call.
    """

    kept: NodeOut
    dropped_id: str
    dropped_body_preview: str
    warnings: list[str] = Field(default_factory=list)


class ReindexStats(BaseModel):
    """Mirrors GraphStore.reindex()'s ReindexStats exactly (design-storage 5.2)."""

    scanned: int
    upserted: int
    deleted: int
    malformed: list[tuple[str, str]] = Field(default_factory=list)


class SnapshotResult(BaseModel):
    """Mirrors GraphStore.snapshot()'s CommitResult exactly (design-storage 7.2)."""

    committed: bool
    sha: str | None = None
    pushed: bool = False
    initialized_repo: bool = False
    message: str | None = None


class SearchHit(BaseModel):
    """Wraps redraft.retrieval.hybrid_search.SearchHit (a plain dataclass whose `node`
    is a raw dict, per that module's own docstring: 'the tool layer (S3b) wraps these in its
    own Pydantic SearchHit/NodeOut models') -- see to_search_hit() below for the conversion.
    """

    node: NodeOut
    score: float
    matched_fts: bool
    matched_vector: bool


class ContradictionPair(BaseModel):
    """Wraps one {a, b} pair from redraft.retrieval.integrity.contradictions()."""

    a: NodeOut
    b: NodeOut


class DanglingEdge(BaseModel):
    """Wraps one row from redraft.retrieval.integrity.dangling_edges(); field names
    match that function's dict keys exactly (id, src, dst, type, src_dangling, dst_dangling),
    so retrieval_tools.py can construct this via a plain **kwargs unpack, no adapter needed."""

    id: str
    src: str
    dst: str
    type: EdgeType
    src_dangling: bool
    dst_dangling: bool


class RationaleRef(BaseModel):
    """report-bundle-v2.md section 1. tradeoffs is verbatim `rationale.properties["tradeoffs"]`
    when set, else None -- never a summary/excerpt of `body` (section 2.3)."""

    title: str
    body: str
    tradeoffs: str | None = None


class DecisionTableRow(BaseModel):
    decision: NodeOut  # .status carries proposed|accepted|superseded|rejected
    rationale: list[RationaleRef] = Field(default_factory=list)  # [] IS the "lacks rationale" signal (section 2.4)
    supersedes_chain: list[str] = Field(default_factory=list)  # titles, nearest-first
    superseded_by: list[str] = Field(default_factory=list)  # usually 0 or 1 entries
    tradeoffs: str | None = None  # first non-null RationaleRef.tradeoffs in `rationale` order, else None


class DecisionTableGroup(BaseModel):
    driver: NodeOut  # a question or requirement node
    rows: list[DecisionTableRow] = Field(default_factory=list)


class SectionGaps(BaseModel):
    open_questions: list[NodeOut] = Field(default_factory=list)  # status='open', part_of == this section's node
    decisions_without_rationale: list[NodeOut] = Field(default_factory=list)  # part_of == this section's node, 0 inbound justifies


class ReportSection(BaseModel):
    node: NodeOut
    depth: int
    children: list["ReportSection"] = Field(default_factory=list)
    attached: dict[str, list[NodeOut]] = Field(default_factory=dict)
    gaps: SectionGaps = Field(default_factory=SectionGaps)


class ReportBundle(BaseModel):
    root_id: str
    generated_at: str
    sections: list[ReportSection]
    decision_tables: list[DecisionTableGroup] = Field(default_factory=list)
    open_questions: list[NodeOut] = Field(default_factory=list)  # flat, bundle-wide -- section 2.5
    contradictions: list[ContradictionPair] = Field(default_factory=list)


class NeighborhoodEntry(BaseModel):
    anchor: str  # title of the hit this neighborhood is around
    neighbors: list[NeighborEdge] = Field(default_factory=list)  # 1-hop, both directions, all edge types


class BriefingResult(BaseModel):
    """report-bundle-v2.md section 6. scope := hits ids unioned with every src/dst appearing
    in any NeighborhoodEntry.neighbors; open_questions/unjustified_decisions filter that scope."""

    query: str
    generated_at: str
    hits: list[SearchHit] = Field(default_factory=list)
    neighborhood: list[NeighborhoodEntry] = Field(default_factory=list)
    open_questions: list[NodeOut] = Field(default_factory=list)
    unjustified_decisions: list[NodeOut] = Field(default_factory=list)


# Session-start overview: cheap, shallow project map (redraft.report.overview()), exposed
# verbatim as the `overview` MCP tool, the graph://project/overview resource, and the data
# `redraft overview` renders to markdown (redraft.overview) -- see overview()'s own
# docstring for the exact composition. Deliberately leaner than NodeOut throughout (no
# body/properties/timestamps beyond a truncated one-line excerpt): this rides inside a
# SessionStart hook's injected context on every single session, so its shape stays cheap by
# construction, not by convention.
class OverviewBranch(BaseModel):
    """One of a root's direct `part_of` children -- a "major branch/component" in
    overview()'s one-hop-below-root map. Tallies are scoped to this branch's own part_of
    subtree (self-inclusive except descendant_count -- see overview()'s docstring)."""

    id: str
    title: str
    type: NodeType
    status: str | None = None
    excerpt: str  # first line of body, truncated -- never the full body
    descendant_count: int  # nodes in the subtree, branch itself excluded
    open_question_count: int  # status='open' questions in {branch} + its subtree
    decision_counts_by_status: dict[str, int] = Field(default_factory=dict)  # decisions in {branch} + its subtree
    unjustified_decision_count: int  # decisions in {branch} + its subtree with no inbound justifies


class OverviewRoot(BaseModel):
    """A spine root: no part_of parent, AND >=1 part_of child of its own (see
    ProjectOverview.floating_by_type for parentless nodes that don't clear that second bar)
    -- plus its direct branches. Every OverviewRoot here therefore has a non-empty `branches`."""

    id: str
    title: str
    type: NodeType
    branches: list[OverviewBranch] = Field(default_factory=list)


class OverviewTotals(BaseModel):
    """Whole-graph counts -- mirrors graph://project/stats' own fields exactly."""

    counts_by_type: dict[str, int] = Field(default_factory=dict)
    counts_by_status: dict[str, int] = Field(default_factory=dict)
    orphan_count: int = 0
    dangling_edge_count: int = 0


class ProjectOverview(BaseModel):
    roots: list[OverviewRoot] = Field(default_factory=list)
    # Parentless nodes EXCLUDED from `roots` because they anchor no part_of subtree of their
    # own -- {type: count}, never a per-node list. rationale/observation/artifact nodes are
    # parentless BY DESIGN (they attach via justifies/references/derived_from, not part_of), so
    # this keeps them acknowledged without cluttering roots with a "no branches yet" section
    # per node. A well-attached rationale (inbound justifies present) is correct-by-design and
    # never appears here; retrieval.integrity.orphans() (via totals.orphan_count) remains the
    # actual "zero edges at all" integrity signal -- this field is a shape note, not a warning.
    floating_by_type: dict[str, int] = Field(default_factory=dict)
    totals: OverviewTotals = Field(default_factory=OverviewTotals)
    top_open_questions: list[NodeOut] = Field(default_factory=list)  # capped -- see overview()


def model_from(model_cls: type[BaseModel], obj: Any) -> Any:
    """Convert a redraft.store return value (pydantic model, dataclass,
    plain object, or dict) into one of our own response models.

    Tolerant of extra attributes/keys the source may carry that the target
    model doesn't declare (e.g. a Node's own outbound edges) --
    from_attributes=True reads exactly the target's declared fields off the
    source object and lets pydantic fill in defaults for anything the target
    allows to be absent (verified empirically against the installed
    pydantic==2.13.4: model_validate(..., from_attributes=True) does not
    require the source to carry a field that has a default).
    """
    if isinstance(obj, dict):
        return model_cls.model_validate(obj)
    return model_cls.model_validate(obj, from_attributes=True)


def to_node_out(node: Any) -> NodeOut:
    return model_from(NodeOut, node)


def to_edge_out(edge: Any) -> EdgeOut:
    """INTEGRATION BUG FOUND AND FIXED: real GraphStore.create_edge returns schema.Edge,
    whose `warning` field is a single `str | None` (design-storage.md section 8) -- EdgeOut's
    `warnings` is a `list[str]` (design-server.md section 3). model_from()'s generic
    from_attributes=True can't bridge a name+shape mismatch like that: `getattr(edge,
    "warnings")` simply doesn't exist on schema.Edge, so pydantic silently falls back to
    EdgeOut.warnings' default `[]` -- a real convention-violation warning from GraphStore
    would never reach the MCP client. (Masked during S2's stub-based development because
    tests/_stub_store.py's own Edge dataclass used `warnings: list[str]`, matching EdgeOut
    exactly.) Adapts explicitly instead of delegating to model_from().
    """
    if isinstance(edge, dict):
        return EdgeOut.model_validate(edge)
    warning = getattr(edge, "warning", None)
    return EdgeOut(src=edge.src, dst=edge.dst, type=edge.type, warnings=[warning] if warning else [])


def to_search_hit(hit: Any) -> SearchHit:
    """Converts a redraft.retrieval.hybrid_search.SearchHit dataclass (node: dict) into
    our own Pydantic SearchHit (node: NodeOut). hit.node's dict keys already match NodeOut's
    fields exactly (it comes from retrieval._util.rows_to_dicts, the same shape integrity.py's
    functions return), so to_node_out(dict) validates it directly, no from_attributes needed.
    """
    return SearchHit(node=to_node_out(hit.node), score=hit.score, matched_fts=hit.matched_fts, matched_vector=hit.matched_vector)
