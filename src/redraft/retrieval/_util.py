"""Internal helpers shared across the retrieval package. Not part of the public seam."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

_UNIT_SEP = "\x1f"


def now_iso() -> str:
    """UTC timestamp, second precision, always 'Z' suffix — matches the storage layer's convention."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_embed_hash(node_type: str, title: str, body: str) -> str:
    """R6: embed_hash = sha256(type + unit-separator + title + unit-separator + body).

    Deliberately independent of storage's whole-file content_hash — pure edge/property/
    frontmatter churn (e.g. a rename touching an unrelated node's inbound link) must never
    force a re-embed. See design-server.md Risk 1.
    """
    raw = f"{node_type}{_UNIT_SEP}{title}{_UNIT_SEP}{body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    """Column-name-keyed dicts from a cursor, independent of the connection's row_factory
    (callers may share a connection we don't own — we never mutate row_factory as a side effect).
    """
    cols = [d[0] for d in cursor.description]
    return [_parse_node_row(dict(zip(cols, row))) for row in cursor.fetchall()]


def _parse_node_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse the nodes.properties JSON column when present; drop storage-internal content_hash
    (retrieval doesn't own it and it isn't part of any Pydantic NodeOut the tool layer builds).
    No-op for rows that aren't from `nodes` (e.g. edges/dangling_edges rows).
    """
    if "properties" in row and isinstance(row["properties"], str):
        row["properties"] = json.loads(row["properties"]) if row["properties"] else {}
    row.pop("content_hash", None)
    return row


def get_node_dict(conn: sqlite3.Connection, node_id: str) -> dict[str, Any] | None:
    """Single-node fetch by id, or None if it doesn't exist. Used by integrity.contradictions()
    and by hybrid_search's find_similar() exact-id disambiguation.
    """
    cur = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
    rows = rows_to_dicts(cur)
    return rows[0] if rows else None
