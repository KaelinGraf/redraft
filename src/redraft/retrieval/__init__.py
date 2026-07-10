"""Retrieval + integrity library layer: embeddings, vector index, FTS, hybrid search, integrity queries.

Pure library layer — no MCP tool registration (that is the S3b slice) and no dependency on
redraft.store. Every function here takes an already-open sqlite3.Connection as its
first argument; the caller (tool layer or tests) owns connection lifecycle, including
loading the sqlite-vec extension before calling anything that touches vec_nodes
(vector_index.py, hybrid_search.py — integrity.py and fts.py do not need it).

Public seam re-exported here for the tool layer (S3b) and the reindex loop:
  - embed_upsert(conn, config, node_id, node_type, title, body) -> bool
  - embed_delete(conn, node_id) -> None
  - ensure_embedding_schema(conn, model_id, dims) -> bool
  - search_nodes(conn, config, query, types=None, status=None, k=10) -> list[SearchHit]
  - find_similar(conn, config, text_or_id, k=5) -> list[SearchHit]
  - the seven integrity queries (module `integrity`, imported whole since they are always
    called by name in a hygiene-report context, e.g. `integrity.orphans(conn)`)
"""

from . import integrity
from .embeddings import EmbeddingConfig, RetrievalConfig, passage_embed, query_embed
from .hybrid_search import SearchHit, find_similar, search_nodes
from .vector_index import embed_delete, embed_upsert, ensure_embedding_schema, get_cached_vector, knn

__all__ = [
    "integrity",
    "EmbeddingConfig",
    "RetrievalConfig",
    "passage_embed",
    "query_embed",
    "SearchHit",
    "find_similar",
    "search_nodes",
    "embed_delete",
    "embed_upsert",
    "ensure_embedding_schema",
    "get_cached_vector",
    "knn",
]
