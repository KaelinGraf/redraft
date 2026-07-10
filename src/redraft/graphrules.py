"""part_of cycle detection and cross-type edge convention warnings (design §5.4, brief §3.2/§3.6)."""

from __future__ import annotations

import sqlite3
from typing import Callable

from redraft.schema import EdgeType, NodeType


def _db_parent(con: sqlite3.Connection, node_id: str) -> str | None:
    """node_id's current part_of parent per committed state, or None. Default lookup for
    would_create_cycle's `parent_of`; also called directly by GraphStore.create_edges' own
    batch-augmented lookup (store.py) so this query lives in exactly one place.
    """
    row = con.execute(
        "SELECT dst FROM edges WHERE src = ? AND type = ?", (node_id, EdgeType.PART_OF.value)
    ).fetchone()
    return row[0] if row else None


def would_create_cycle(
    con: sqlite3.Connection,
    node_id: str,
    new_parent_id: str,
    parent_of: Callable[[str], str | None] | None = None,
) -> bool:
    """part_of is schema-constrained to at most one value per node, so its graph is a forest
    if acyclic and cycle detection is a single upward parent-pointer walk — not a general graph
    algorithm, so networkx is not used here. O(depth).

    `parent_of` overrides the per-node parent lookup (default: current committed state, via
    _db_parent). GraphStore.create_edges (batch edge creation) passes one that checks its own
    in-flight batch of proposed part_of edges first, falling back to _db_parent -- so a cycle
    spread across more than one edge of the SAME batch (e.g. A->B and B->A in one call, neither
    of which alone would cycle against pre-batch state) is still caught before anything is
    written. Every other caller (create_node, create_edge, merge_nodes, mutations.reparent_node)
    omits it and gets the unchanged pure-DB-state walk.
    """
    lookup = parent_of or (lambda nid: _db_parent(con, nid))
    seen = {node_id}
    current: str | None = new_parent_id
    while current is not None:
        if current in seen:
            return True
        seen.add(current)
        current = lookup(current)
    return False


# Soft src/dst-type conventions per brief §3.2's edge table. None means "no constraint on that
# side". Edge types absent from this map (depends_on, contradicts, relates_to, part_of) are
# unconstrained node->node links by design.
_EDGE_CONVENTIONS: dict[EdgeType, tuple[tuple[NodeType, ...] | None, tuple[NodeType, ...] | None]] = {
    EdgeType.JUSTIFIES: ((NodeType.RATIONALE,), (NodeType.DECISION,)),
    EdgeType.SUPERSEDES: ((NodeType.DECISION,), (NodeType.DECISION,)),
    EdgeType.ADDRESSES: ((NodeType.DECISION, NodeType.IDEA), (NodeType.QUESTION, NodeType.REQUIREMENT)),
    EdgeType.REFERENCES: (None, (NodeType.ARTIFACT,)),
    EdgeType.DERIVED_FROM: ((NodeType.OBSERVATION, NodeType.ARTIFACT), None),
}


def check_edge_convention(src_type: NodeType, dst_type: NodeType, edge_type: EdgeType) -> str | None:
    """Pure convention check; returns a warning string or None. Never raised as an exception —
    a mis-typed edge is a real boundary-level user error worth surfacing, not worth blocking a
    write over (brief §3.6).
    """
    convention = _EDGE_CONVENTIONS.get(edge_type)
    if convention is None:
        return None
    allowed_src, allowed_dst = convention
    if allowed_src is not None and src_type not in allowed_src:
        options = "/".join(t.value for t in allowed_src)
        return f"{edge_type.value} conventionally has a {options} source, not {src_type.value}"
    if allowed_dst is not None and dst_type not in allowed_dst:
        options = "/".join(t.value for t in allowed_dst)
        return f"{edge_type.value} conventionally has a {options} target, not {dst_type.value}"
    return None
