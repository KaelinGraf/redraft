"""New Pydantic request/response models for the UI's REST surface (s6-ui.md §9). Every
shape NOT defined here is an existing redraft.models Pydantic model, reused verbatim (that
is the whole point of building on GraphStore/report/retrieval directly instead of a second
storage layer) -- these eleven are thin wrappers with no business logic of their own;
everything they carry is computed by an existing library function (queries.py/mutations.py
document which one, per model). One file, mirroring how redraft.models itself holds every
wire shape for the MCP tool surface in one place -- so there is exactly one place a router
file looks for "what does the wire shape for X look like."
"""
from __future__ import annotations

import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from redraft.models import STATUS_BY_TYPE, DanglingEdge, EdgeType, NodeOut, NodeType, SearchHit


class OutlineNode(BaseModel):
    id: str
    type: NodeType
    title: str
    status: str | None = None


class OutlineEdge(BaseModel):
    src: str
    dst: str
    type: EdgeType


class OutlineOut(BaseModel):
    """GET /api/outline -- backs both the Outline tree and the Map tab from one query."""

    nodes: list[OutlineNode] = Field(default_factory=list)
    edges: list[OutlineEdge] = Field(default_factory=list)


class AttentionOut(BaseModel):
    """GET /api/attention -- the RIGHT-hand hygiene strip: four retrieval.integrity queries
    composed into one round trip (open_questions, decisions_without_rationale [renamed
    unjustified_decisions here for the UI-facing name], dangling_edges, stale)."""

    open_questions: list[NodeOut] = Field(default_factory=list)
    unjustified_decisions: list[NodeOut] = Field(default_factory=list)
    dangling_edges: list[DanglingEdge] = Field(default_factory=list)
    stale: list[NodeOut] = Field(default_factory=list)


class TimelineItem(BaseModel):
    """One row for the Timeline tab, per the Planning dates convention
    (organizing-protocol.md §2, `properties.start`/`properties.due`): either a SCHEDULED item
    (start and/or due set) or an UNSCHEDULED milestone (both null -- the frontend's
    unscheduled tray). The two groups are fully recoverable from start/due alone, so this is
    one flat item shape, not two.

    start/due are passed through exactly as stored in `properties` -- never date-parsed or
    ISO-validated here. A malformed/non-ISO value still round-trips to the frontend rather
    than 500ing the endpoint; rendering or flagging a bad date is the frontend's job."""

    id: str
    title: str
    type: NodeType
    status: str | None = None
    start: str | None = None
    due: str | None = None
    part_of: str | None = None  # swimlane/component; null = the frontend's "ungrouped" lane
    depends_on: list[str] = Field(default_factory=list)  # outbound depends_on targets, for arrows


class TimelineOut(BaseModel):
    """GET /api/timeline -- every node with a scheduled start/due, plus every dateless
    milestone (the unscheduled tray). Wrapped in a container, matching OutlineOut/
    AttentionOut's own "dump everything for a tab" shape rather than a bare list."""

    items: list[TimelineItem] = Field(default_factory=list)


class SchemaOut(BaseModel):
    """GET /api/schema -- schema.NodeType/EdgeType + models.STATUS_BY_TYPE, serialized."""

    node_types: list[str]
    edge_types: list[str]
    status_by_type: dict[str, list[str] | None]


class DedupHintsOut(BaseModel):
    """GET /api/dedup-hints -- hybrid_search.find_similar when the embedder is warm, an
    FTS-only fallback (degraded=True) otherwise (s6-ui.md §5.2)."""

    hits: list[SearchHit] = Field(default_factory=list)
    degraded: bool


class ReportFile(BaseModel):
    filename: str
    size: int
    modified_at: str


class ReportContent(BaseModel):
    """GET /api/reports/{filename} -> {filename, content} (s6-ui.md §9)."""

    filename: str
    content: str


class StatusOut(BaseModel):
    generation: int
    last_reindex_at: str | None
    embedder_ready: bool


class GitStatusOut(BaseModel):
    dirty: bool
    changed_paths: list[str] = Field(default_factory=list)


class CreateNodeRequest(BaseModel):
    """POST /api/nodes -> GraphStore.create_node, direct pass-through including part_of/edges
    (which GraphStore already supports natively, unlike the MCP create_node tool's
    deliberately narrower 5-param signature) -- better ergonomics for a form: "create under
    this parent" in one submission (s6-ui.md §9). `edges` is always list-shaped here (a form
    naturally submits one or more targets per edge-type group) even though
    GraphStore.create_node's own signature also accepts a bare str per edge type -- passing a
    1-element list is equivalent, GraphStore normalizes either shape identically."""

    type: NodeType
    title: str
    body: str = ""
    status: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    part_of: str | None = None
    edges: dict[EdgeType, list[str]] | None = None

    @model_validator(mode="after")
    def _reject_known_bare_valueerror_cases(self) -> "CreateNodeRequest":
        """BUG FOUND AND FIXED: GraphStore.create_node has THREE bare `ValueError` raise
        sites reachable from this request (empty/whitespace title via ids.sanitize_title_to_id;
        a status illegal for `type` via schema.validate_status; `part_of` passed inside
        `edges` instead of the dedicated field) -- none of them are among the six
        redraft.errors types ui/errors.py's _HTTP_STATUS table maps, so left unguarded they
        would surface as an opaque 500 for an easy, common client mistake, exactly the same
        failure class write_tools.py's own "BUG FOUND AND FIXED" docstring already documents
        (and partially worked around with its own _validate_title/_validate_status) for the
        MCP layer. Raising ValueError from a pydantic model_validator(mode="after") is
        automatically wrapped into a 422 response by FastAPI's own RequestValidationError
        handling (verified live) -- no custom exception type needed. The one gap this does
        NOT close -- a title that is non-empty/non-whitespace but sanitizes to "" anyway
        (entirely illegal characters, e.g. "////") -- is the same RESIDUAL GAP
        write_tools.py's own docstring names and leaves open (closing it here would mean
        duplicating ids.ILLEGAL/ids.CONTROL by hand, which is exactly the "duplicate logic"
        risk that module's docstring itself warns against); routers/nodes.py's create_node
        handler catches it with a narrow ValueError->422 safety net around the actual
        GraphStore.create_node call instead, so it still 422s cleanly rather than 500ing.
        """
        if not unicodedata.normalize("NFC", self.title).strip():
            raise ValueError("title must contain at least one non-whitespace character")
        legal = STATUS_BY_TYPE.get(self.type)
        if legal is None:
            if self.status is not None:
                raise ValueError(f"type {self.type!r} does not carry a status")
        elif self.status is not None and self.status not in legal:
            raise ValueError(f"{self.status!r} is not a legal status for type {self.type!r} (legal: {sorted(legal)})")
        if self.edges and "part_of" in self.edges:
            raise ValueError("use the part_of field for part_of, not edges")
        return self


class UpdateNodeRequest(BaseModel):
    """PATCH /api/nodes/{id} -> GraphStore.update_node, field-for-field. `properties` merges
    onto the existing dict (cannot delete a key); `remove_properties` deletes those keys after
    the merge (removal wins on overlap, an absent key is a no-op) -- the only way to clear a
    properties key."""

    body: str | None = None
    mode: Literal["append", "replace"] = "append"
    status: str | None = None
    properties: dict[str, Any] | None = None
    remove_properties: list[str] | None = None


class EdgeRequest(BaseModel):
    """Shared body shape for POST /api/edges (-> GraphStore.create_edge) AND DELETE
    /api/edges (-> GraphStore.delete_edge, s6-ui.md §9) -- one model for the one wire shape
    used at both endpoints, rather than repeating three individually-Body()-annotated
    parameters in each router function."""

    src: str
    dst: str
    type: EdgeType


class EdgeBatchRequest(BaseModel):
    """POST /api/edges/batch -> GraphStore.create_edges: N edges created in one atomic call.
    Each item is EdgeRequest's own {src, dst, type} shape, reused verbatim rather than
    repeated -- a batch is just a list of the same wire shape the single-edge endpoint uses."""

    edges: list[EdgeRequest]
