"""Closed node/edge type vocab, per-type status rules, edge cardinality, and the Node/Edge models.

Source: brief §3.1 (node types), §3.2 (edge types); design §1.1 (frontmatter schema, key order).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class NodeType(StrEnum):
    CONCEPT = "concept"
    DECISION = "decision"
    RATIONALE = "rationale"
    REQUIREMENT = "requirement"
    CONSTRAINT = "constraint"
    IDEA = "idea"
    QUESTION = "question"
    ARTIFACT = "artifact"
    OBSERVATION = "observation"
    MILESTONE = "milestone"


class EdgeType(StrEnum):
    PART_OF = "part_of"
    JUSTIFIES = "justifies"
    SUPERSEDES = "supersedes"
    ADDRESSES = "addresses"
    DEPENDS_ON = "depends_on"
    CONTRADICTS = "contradicts"
    REFERENCES = "references"
    DERIVED_FROM = "derived_from"
    RELATES_TO = "relates_to"


# Edge types other than part_of; frontmatter carries these as lists (design §1.1).
LIST_EDGE_TYPES: tuple[EdgeType, ...] = tuple(t for t in EdgeType if t is not EdgeType.PART_OF)

# Per-type status enum. A NodeType absent from this map has no `status` field at all.
STATUS_VALUES: dict[NodeType, tuple[str, ...]] = {
    NodeType.DECISION: ("proposed", "accepted", "superseded", "rejected"),
    NodeType.QUESTION: ("open", "resolved"),
    NodeType.MILESTONE: ("planned", "done"),
}

DEFAULT_STATUS: dict[NodeType, str] = {
    NodeType.DECISION: "proposed",
    NodeType.QUESTION: "open",
    NodeType.MILESTONE: "planned",
}

# Canonical frontmatter key order on write — fixed, never alphabetized (design §1.1).
FRONTMATTER_KEY_ORDER: tuple[str, ...] = (
    "type",
    "title",
    "status",
    "created",
    "updated",
    "properties",
    "part_of",
    *[t.value for t in LIST_EDGE_TYPES],
)


def has_status(node_type: NodeType) -> bool:
    return node_type in STATUS_VALUES


def status_error(node_type: NodeType, status: str | None, *, present: bool) -> str | None:
    """Load-time shape check: `present` is whether a `status` key exists in the source at all
    (distinct from its value being None/null, which is itself a violation for a status-less type).
    Returns an error message, or None if `status`/`present` are exactly valid for `node_type`.
    """
    if has_status(node_type):
        if not present or status not in STATUS_VALUES[node_type]:
            return f"{node_type} requires status in {STATUS_VALUES[node_type]}, got {status!r}"
        return None
    if present:
        return f"{node_type} nodes must not have a status key"
    return None


def validate_status(node_type: NodeType, status: str | None) -> str | None:
    """Create-time resolve+validate: applies the type's default when status is None, then
    delegates to `status_error` for the single source of truth on shape validity."""
    if status is None and has_status(node_type):
        status = DEFAULT_STATUS[node_type]
    err = status_error(node_type, status, present=status is not None)
    if err:
        raise ValueError(err)
    return status


class Node(BaseModel):
    """In-memory node. Edge fields hold bare target ids (wikilink syntax is nodefile.py's concern)."""

    id: str
    type: NodeType
    title: str
    body: str = ""
    status: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    part_of: str | None = None
    justifies: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    addresses: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    relates_to: list[str] = Field(default_factory=list)
    created: str
    updated: str
    extra: dict[str, Any] = Field(default_factory=dict)

    def edges_of(self, edge_type: EdgeType) -> list[str]:
        """The target id list for a given edge type (part_of normalized to a 0/1-item list)."""
        if edge_type is EdgeType.PART_OF:
            return [self.part_of] if self.part_of is not None else []
        return getattr(self, edge_type.value)


class Edge(BaseModel):
    id: str
    src: str
    dst: str
    type: EdgeType
    warning: str | None = None
