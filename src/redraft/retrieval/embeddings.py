"""Lazy, thread-safe fastembed singleton(s) + asymmetric passage/query embedding helpers.

bge-family models are asymmetric: index-time text must go through passage_embed() and
query-time text through query_embed() — mixing them up materially degrades ranking
quality (design-server.md section 4). Callers pass a `config` duck-typed with at least
`embedding_model_id: str` and `cache_dir: str`; RetrievalConfig below is the concrete
default and is structurally compatible with the eventual top-level ServerConfig (same
field names) so this module works unchanged once that lands.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
from fastembed import TextEmbedding

_DEFAULT_CACHE_DIR = str(Path("~/.cache/fastembed").expanduser())


class EmbeddingConfig(Protocol):
    embedding_model_id: str
    cache_dir: str


@dataclass
class RetrievalConfig:
    """Self-contained default config for this slice. R6: cache_dir defaults to the
    absolute, expanded ~/.cache/fastembed (shared across projects, not colocated under
    a per-project index/ dir as design-server.md's ServerConfig sketch had it) —
    overridable per instance.
    """

    embedding_model_id: str = "BAAI/bge-small-en-v1.5"
    embedding_dims: int = 384
    cache_dir: str = field(default_factory=lambda: _DEFAULT_CACHE_DIR)
    fts_candidate_pool: int = 50
    rrf_k: int = 60


_lock = threading.Lock()
_embedders: dict[tuple[str, str], TextEmbedding] = {}


def get_embedder(config: EmbeddingConfig) -> TextEmbedding:
    """Double-checked-locking singleton, keyed by (model_id, cache_dir) rather than a
    single global — a bare single-slot global (as design-server.md's pseudocode has it)
    would keep serving the first-loaded model forever and silently ignore a later
    embedding_model_id change, defeating the model-swap re-embed acceptance test.
    """
    key = (config.embedding_model_id, config.cache_dir)
    embedder = _embedders.get(key)
    if embedder is not None:
        return embedder
    with _lock:
        embedder = _embedders.get(key)
        if embedder is None:
            embedder = TextEmbedding(model_name=config.embedding_model_id, cache_dir=config.cache_dir)
            _embedders[key] = embedder
    return embedder


def passage_embed(config: EmbeddingConfig, texts: list[str]) -> list[np.ndarray]:
    """Index-time embedding. Empty input short-circuits without touching the model."""
    if not texts:
        return []
    return list(get_embedder(config).passage_embed(texts))


def query_embed(config: EmbeddingConfig, text: str) -> np.ndarray:
    """Query-time embedding for a single query string."""
    return next(iter(get_embedder(config).query_embed([text])))
