"""Vector index: embedding_meta (model/dims singleton) + node_vectors (TEXT id -> INTEGER
vec_rowid surrogate mapping, keyed by our own embed_hash cache) + vec_nodes (the sqlite-vec
vec0 virtual table holding the actual FLOAT[dims] vectors, cosine metric).

Schema per design-server.md section 6, additive to the storage layer's nodes/edges/nodes_fts.

DESIGN DEFECT, RESOLVED AT INTEGRATION (I2): design-storage.md section 5.1 originally also
defined a table named `node_vectors` (node_id, embedding BLOB, model) — a different,
incompatible schema for the same name. This module implements the design-server.md shape
per this slice's explicit pinned contract (INTEGER surrogate vec_rowid mapping); the
collision is resolved at the source -- src/redraft/index.py's real DDL no longer
defines a node_vectors table at all, this module's shape wins outright. See
tests/fixtures_ddl.py for the historical receipts (its own frozen verbatim copy of the
original design-storage.md text still shows the superseded table).

Every function here requires the sqlite-vec extension already loaded on `conn`
(`sqlite_vec.load(conn)`) — that is the caller's responsibility (design-server.md's
"one connection per tool call" pattern owns this), not this module's.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np
from sqlite_vec import serialize_float32

from ._util import compute_embed_hash, now_iso
from .embeddings import EmbeddingConfig, passage_embed

_EMBEDDING_META_DDL = """
CREATE TABLE IF NOT EXISTS embedding_meta (
  id    INTEGER PRIMARY KEY CHECK (id = 1),
  model TEXT NOT NULL,
  dims  INTEGER NOT NULL
)
"""

_NODE_VECTORS_DDL = """
CREATE TABLE IF NOT EXISTS node_vectors (
  node_id     TEXT PRIMARY KEY,
  vec_rowid   INTEGER NOT NULL UNIQUE,
  embed_hash  TEXT NOT NULL,
  embedded_at TEXT NOT NULL
)
"""


def _create_vec_table(conn: sqlite3.Connection, dims: int) -> None:
    conn.execute("DROP TABLE IF EXISTS vec_nodes")
    conn.execute(
        "CREATE VIRTUAL TABLE vec_nodes USING vec0("
        f"vec_rowid INTEGER PRIMARY KEY, embedding FLOAT[{int(dims)}] distance_metric=cosine)"
    )


def ensure_embedding_schema(conn: sqlite3.Connection, model_id: str, dims: int) -> bool:
    """Idempotent schema bootstrap + model/dims-change invalidation.

    Returns True iff a model/dims change triggered a full invalidation (vec_nodes dropped
    and recreated, node_vectors cache cleared — every node needs re-embedding). Cheap
    (single-row check) — call unconditionally at the start of every reindex and lazily
    before the first embed-touching call each process lifetime.
    """
    conn.execute(_EMBEDDING_META_DDL)
    conn.execute(_NODE_VECTORS_DDL)
    row = conn.execute("SELECT model, dims FROM embedding_meta WHERE id = 1").fetchone()

    if row is None:
        conn.execute("INSERT INTO embedding_meta(id, model, dims) VALUES (1, ?, ?)", (model_id, dims))
        _create_vec_table(conn, dims)
        conn.commit()
        return False

    stored_model, stored_dims = row
    if stored_model != model_id or stored_dims != dims:
        conn.execute("DELETE FROM node_vectors")
        _create_vec_table(conn, dims)
        conn.execute("UPDATE embedding_meta SET model = ?, dims = ? WHERE id = 1", (model_id, dims))
        conn.commit()
        return True

    return False


def _next_vec_rowid(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(vec_rowid), 0) + 1 FROM node_vectors").fetchone()[0]


def embed_upsert(
    conn: sqlite3.Connection,
    config: EmbeddingConfig,
    node_id: str,
    node_type: str,
    title: str,
    body: str,
) -> bool:
    """Embed (title, body) for node_id iff its (type, title, body) embed_hash changed since
    the last embed. Returns True if it embedded (new node or changed content), False on a
    cache hit (skipped — unchanged title/body, e.g. only status/properties/edges changed).

    The embedded passage text is title + blank-line + body (falls back to title alone when
    body is empty) — the design pins the *cache key* (embed_hash) exactly but not the
    passage text composition; this mirrors nodes_fts, which also indexes both columns.
    """
    embed_hash = compute_embed_hash(node_type, title, body)
    existing = conn.execute(
        "SELECT vec_rowid, embed_hash FROM node_vectors WHERE node_id = ?", (node_id,)
    ).fetchone()
    if existing is not None and existing[1] == embed_hash:
        return False

    text = f"{title}\n\n{body}" if body else title
    vector = passage_embed(config, [text])[0]
    blob = serialize_float32(vector)
    embedded_at = now_iso()

    if existing is not None:
        vec_rowid = existing[0]
        conn.execute("UPDATE vec_nodes SET embedding = ? WHERE vec_rowid = ?", (blob, vec_rowid))
        conn.execute(
            "UPDATE node_vectors SET embed_hash = ?, embedded_at = ? WHERE node_id = ?",
            (embed_hash, embedded_at, node_id),
        )
    else:
        vec_rowid = _next_vec_rowid(conn)
        conn.execute("INSERT INTO vec_nodes(vec_rowid, embedding) VALUES (?, ?)", (vec_rowid, blob))
        conn.execute(
            "INSERT INTO node_vectors(node_id, vec_rowid, embed_hash, embedded_at) VALUES (?, ?, ?, ?)",
            (node_id, vec_rowid, embed_hash, embedded_at),
        )
    conn.commit()
    return True


def embed_delete(conn: sqlite3.Connection, node_id: str) -> None:
    """Remove node_id's vector (both the mapping row and its vec0 row), if any. No-op if
    the node was never embedded (idempotent — safe on retry / on a node that predates
    retrieval, e.g. a dangling reference)."""
    row = conn.execute("SELECT vec_rowid FROM node_vectors WHERE node_id = ?", (node_id,)).fetchone()
    if row is None:
        return
    conn.execute("DELETE FROM vec_nodes WHERE vec_rowid = ?", (row[0],))
    conn.execute("DELETE FROM node_vectors WHERE node_id = ?", (node_id,))
    conn.commit()


def get_cached_vector(conn: sqlite3.Connection, node_id: str) -> Any | None:
    """The already-computed embedding for node_id, or None if it has never been embedded.
    Used by find_similar's exact-id disambiguation to avoid a redundant re-embed."""
    row = conn.execute(
        "SELECT v.embedding FROM node_vectors nv JOIN vec_nodes v ON v.vec_rowid = nv.vec_rowid "
        "WHERE nv.node_id = ?",
        (node_id,),
    ).fetchone()
    if row is None:
        return None
    return np.frombuffer(row[0], dtype="<f4")


def knn(conn: sqlite3.Connection, vector: Any, limit: int) -> list[tuple[str, float]]:
    """k-nearest-neighbor node ids by cosine distance, ascending (best first). Empty list
    for limit <= 0 or an empty vector table — vec0 itself already returns [] for both
    (verified empirically against 0.1.9), this is just a cheap short-circuit."""
    if limit <= 0:
        return []
    blob = serialize_float32(vector)
    rows = conn.execute(
        "SELECT nv.node_id, v.distance FROM vec_nodes v "
        "JOIN node_vectors nv ON nv.vec_rowid = v.vec_rowid "
        "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
        (blob, limit),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]
