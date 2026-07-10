"""Hybrid search: fuse FTS5 (lexical) and sqlite-vec (semantic) candidate lists by
Reciprocal Rank Fusion, filter-before-rank, deterministic tie-break.

Public seam: search_nodes() (hybrid) and find_similar() (vector-only dedup primitive),
both returning plain dataclasses (SearchHit) — the tool layer (S3b) wraps these in its
own Pydantic SearchHit/NodeOut models.

BUG FOUND in design-server.md section 6 (flagged in final report): its RRF tie-break
pseudocode sorts on `_neg(meta[kv[0]].updated)` where `updated` is an ISO8601 *string*
("...Z") and `_neg` is never defined — unary `-` on a str raises TypeError in Python, so
the pseudocode as written cannot run. Fixed here by parsing to an epoch float and negating
that (numerically valid), preserving the evident intent ("tie-break: newer first, then id
(deterministic for tests)").
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from ._util import get_node_dict, rows_to_dicts
from .embeddings import EmbeddingConfig, passage_embed, query_embed
from .fts import fts_candidates
from .vector_index import get_cached_vector, knn


@dataclass
class SearchHit:
    node: dict[str, Any]
    score: float
    matched_fts: bool
    matched_vector: bool


class HybridSearchConfig(EmbeddingConfig, Protocol):
    fts_candidate_pool: int
    rrf_k: int


def load_node_meta(conn: sqlite3.Connection, ids: set[str]) -> dict[str, dict[str, Any]]:
    """nodes rows for the given id set, keyed by id. Ids with no matching row (e.g. a
    node_vectors entry that outlived its node, pending the next reindex) are simply
    absent — every caller here treats "not in meta" as "skip this candidate"."""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(f"SELECT * FROM nodes WHERE id IN ({placeholders})", tuple(ids))
    return {row["id"]: row for row in rows_to_dicts(cur)}


def _updated_desc_key(updated: str) -> float:
    """Negated epoch seconds so ascending sort ⇒ newest-first, replacing the design's
    invalid `_neg(str)` call (see module docstring)."""
    return -datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()


def vector_candidates(conn: sqlite3.Connection, config: HybridSearchConfig, query: str, limit: int) -> list[str]:
    """Node ids ranked by cosine distance, best first. [] for a blank query or limit<=0 —
    symmetric with fts_candidates so a degenerate query doesn't burn a model call for
    nothing while the FTS branch already no-ops."""
    if limit <= 0 or not query.strip():
        return []
    vector = query_embed(config, query)
    return [nid for nid, _dist in knn(conn, vector, limit)]


def search_nodes(
    conn: sqlite3.Connection,
    config: HybridSearchConfig,
    query: str,
    types: list[str] | None = None,
    status: str | None = None,
    k: int = 10,
) -> list[SearchHit]:
    """Hybrid FTS+vector search fused by Reciprocal Rank Fusion (design-server.md section 6).

    Filter-before-rank: type/status filtering happens on each branch's candidate list
    *before* RRF rank position is assigned, so a filtered-out global-rank-1 result never
    consumes the rank-1 fusion weight of a type/status it was never really competing
    within (as the design explicitly requires).
    """
    if k <= 0:
        # BUG FOUND (fixed here): n_pool is floored at config.fts_candidate_pool via
        # max(k*5, ...), so a negative k does NOT starve the candidate lists the way it
        # does in knn()/fts_candidates(). Without this guard, `sorted(...)[:k]` below
        # would slice with a *negative* k -- Python list slicing treats [:-2] as "all but
        # the last 2 elements", not "0 elements" -- silently returning a wrong, non-empty
        # result instead of the empty list a negative/zero k obviously means.
        return []
    n_pool = max(k * 5, config.fts_candidate_pool)

    fts_ids = fts_candidates(conn, query, n_pool)
    vec_ids = vector_candidates(conn, config, query, n_pool)

    meta = load_node_meta(conn, set(fts_ids) | set(vec_ids))

    def filtered(ids: list[str]) -> list[str]:
        return [
            i
            for i in ids
            if i in meta
            and (types is None or meta[i]["type"] in types)
            and (status is None or meta[i]["status"] == status)
        ]

    fts_f, vec_f = filtered(fts_ids), filtered(vec_ids)

    scores: dict[str, float] = {}
    matched_fts: set[str] = set()
    matched_vector: set[str] = set()
    for rank, nid in enumerate(fts_f, start=1):
        scores[nid] = scores.get(nid, 0.0) + 1.0 / (config.rrf_k + rank)
        matched_fts.add(nid)
    for rank, nid in enumerate(vec_f, start=1):
        scores[nid] = scores.get(nid, 0.0) + 1.0 / (config.rrf_k + rank)
        matched_vector.add(nid)

    ranked = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], _updated_desc_key(meta[kv[0]]["updated"]), kv[0]),
    )[:k]

    return [
        SearchHit(node=meta[nid], score=score, matched_fts=nid in matched_fts, matched_vector=nid in matched_vector)
        for nid, score in ranked
    ]


def find_similar(conn: sqlite3.Connection, config: HybridSearchConfig, text_or_id: str, k: int = 5) -> list[SearchHit]:
    """Vector-only near-duplicate search — the dedup primitive.

    Disambiguation (design-server.md section 6): if text_or_id exactly matches an existing
    node id, reuse its cached vector (no re-embed) and exclude it from its own results;
    otherwise treat the string as free text and query_embed() it fresh.

    Gap filled (undefined in the design): get_cached_vector can return None for a real,
    existing node that predates embedding (e.g. reindex hasn't run yet). Conservative
    choice: fall back to embedding that node's own (title, body) via passage_embed — the
    same text embed_upsert would have used — rather than raising.
    """
    if k <= 0:
        return []  # see the matching guard + explanation in search_nodes()
    existing = get_node_dict(conn, text_or_id)
    if existing is not None:
        vector = get_cached_vector(conn, text_or_id)
        if vector is None:
            text = f"{existing['title']}\n\n{existing['body']}" if existing["body"] else existing["title"]
            vector = passage_embed(config, [text])[0]
        exclude: str | None = text_or_id
    else:
        vector = query_embed(config, text_or_id)
        exclude = None

    raw = knn(conn, vector, (k + 1) if exclude else k)
    raw = [(nid, dist) for nid, dist in raw if nid != exclude][:k]

    meta = load_node_meta(conn, {nid for nid, _ in raw})
    return [
        SearchHit(node=meta[nid], score=1.0 / (1.0 + dist), matched_fts=False, matched_vector=True)
        for nid, dist in raw
        if nid in meta
    ]
