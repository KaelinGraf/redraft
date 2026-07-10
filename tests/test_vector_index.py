"""vector_index.py -- embedding_meta/node_vectors/vec_nodes schema management,
embed_upsert/embed_delete cache semantics, knn, and the full model-swap-reembeds-all
acceptance path. Real fastembed model(s) throughout."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from conftest import insert_node
from redraft.retrieval.embeddings import query_embed
from redraft.retrieval.hybrid_search import find_similar
from redraft.retrieval.vector_index import (
    embed_delete,
    embed_upsert,
    ensure_embedding_schema,
    get_cached_vector,
    knn,
)

# A second real, small, but *different-dimension* model (512 vs bge-small's 384) so the
# model-swap test proves the vec0 table was genuinely recreated at a new width, not just
# cosmetically relabeled.
SWAP_MODEL_ID = "jinaai/jina-embeddings-v2-small-en"
SWAP_MODEL_DIMS = 512


def test_ensure_embedding_schema_bootstraps_fresh_db(conn, retrieval_config):
    changed = ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    assert changed is False
    row = conn.execute("SELECT model, dims FROM embedding_meta WHERE id=1").fetchone()
    assert row == (retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    assert conn.execute("SELECT COUNT(*) FROM vec_nodes").fetchone()[0] == 0


def test_ensure_embedding_schema_noop_when_unchanged(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    changed = ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    assert changed is False


def test_ensure_embedding_schema_invalidates_on_model_change(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    insert_node(conn, "A", "concept", body="alpha")
    embed_upsert(conn, retrieval_config, "A", "concept", "A", "alpha")
    assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == 1

    changed = ensure_embedding_schema(conn, "some-other-model-id", retrieval_config.embedding_dims)
    assert changed is True
    assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == 0
    row = conn.execute("SELECT model, dims FROM embedding_meta WHERE id=1").fetchone()
    assert row[0] == "some-other-model-id"


def test_ensure_embedding_schema_invalidates_on_dims_change(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    changed = ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims + 1)
    assert changed is True
    row = conn.execute("SELECT model, dims FROM embedding_meta WHERE id=1").fetchone()
    assert row[1] == retrieval_config.embedding_dims + 1


def test_embed_upsert_new_node_embeds(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    insert_node(conn, "A", "concept", body="alpha content")
    embedded = embed_upsert(conn, retrieval_config, "A", "concept", "A", "alpha content")
    assert embedded is True
    row = conn.execute("SELECT vec_rowid, embed_hash FROM node_vectors WHERE node_id='A'").fetchone()
    assert row is not None
    vec = get_cached_vector(conn, "A")
    assert vec is not None
    assert vec.shape == (retrieval_config.embedding_dims,)


def test_embed_upsert_cache_hit_on_unchanged_title_body(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    insert_node(conn, "A", "concept", body="alpha content")

    assert embed_upsert(conn, retrieval_config, "A", "concept", "A", "alpha content") is True
    embedded_at_1 = conn.execute("SELECT embedded_at FROM node_vectors WHERE node_id='A'").fetchone()[0]

    assert embed_upsert(conn, retrieval_config, "A", "concept", "A", "alpha content") is False
    embedded_at_2 = conn.execute("SELECT embedded_at FROM node_vectors WHERE node_id='A'").fetchone()[0]
    assert embedded_at_1 == embedded_at_2

    assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM vec_nodes").fetchone()[0] == 1


def test_embed_upsert_reembeds_on_body_change(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    insert_node(conn, "A", "concept", body="alpha content")
    assert embed_upsert(conn, retrieval_config, "A", "concept", "A", "alpha content") is True
    vec_1 = get_cached_vector(conn, "A").copy()
    rowid_1 = conn.execute("SELECT vec_rowid FROM node_vectors WHERE node_id='A'").fetchone()[0]

    changed_body = "a totally different sentence about deep sea marine biology and coral reefs"
    assert embed_upsert(conn, retrieval_config, "A", "concept", "A", changed_body) is True
    vec_2 = get_cached_vector(conn, "A")
    assert not np.allclose(vec_1, vec_2)

    # update-in-place: vec_rowid is stable across a re-embed, no orphaned vec_nodes rows
    rowid_2 = conn.execute("SELECT vec_rowid FROM node_vectors WHERE node_id='A'").fetchone()[0]
    assert rowid_1 == rowid_2
    assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM vec_nodes").fetchone()[0] == 1


def test_embed_delete_removes_vector_and_mapping(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    insert_node(conn, "A", "concept", body="alpha content")
    embed_upsert(conn, retrieval_config, "A", "concept", "A", "alpha content")

    embed_delete(conn, "A")

    assert conn.execute("SELECT COUNT(*) FROM node_vectors WHERE node_id='A'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vec_nodes").fetchone()[0] == 0
    assert get_cached_vector(conn, "A") is None


def test_embed_delete_nonexistent_node_is_noop(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    embed_delete(conn, "does-not-exist")  # must not raise


def test_embed_delete_leaves_other_nodes_intact(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    insert_node(conn, "A", "concept", body="alpha content")
    insert_node(conn, "B", "concept", body="beta content")
    embed_upsert(conn, retrieval_config, "A", "concept", "A", "alpha content")
    embed_upsert(conn, retrieval_config, "B", "concept", "B", "beta content")

    embed_delete(conn, "A")

    assert get_cached_vector(conn, "A") is None
    assert get_cached_vector(conn, "B") is not None


def test_knn_ranks_by_cosine_distance(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    fixtures = {
        "Cats": "Cats are small domesticated felines.",
        "Dogs": "Dogs are loyal domesticated canines.",
        "Rockets": "Rockets use combustion to reach orbit.",
    }
    for nid, body in fixtures.items():
        insert_node(conn, nid, "concept", body=body)
        embed_upsert(conn, retrieval_config, nid, "concept", nid, body)

    qvec = query_embed(retrieval_config, "small furry pet animal")
    results = knn(conn, qvec, limit=3)
    assert [nid for nid, _ in results][0] == "Cats"
    assert [nid for nid, _ in results][-1] == "Rockets"


def test_knn_zero_limit_returns_empty(conn, retrieval_config):
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    insert_node(conn, "A", "concept", body="alpha content")
    embed_upsert(conn, retrieval_config, "A", "concept", "A", "alpha content")
    qvec = query_embed(retrieval_config, "alpha")
    assert knn(conn, qvec, limit=0) == []


def test_model_swap_reembeds_all_and_find_similar_still_works(conn, retrieval_config):
    """Phase 2 acceptance criterion (design doc): "changing the embedding model re-embeds
    cleanly via the model column ... find_similar still functions post-swap." Uses a
    second real model with a *different* vector width (512 vs 384) so this proves genuine
    end-to-end re-embedding, not just a relabeled model string."""
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    fixtures = {
        "Cats": "Cats are small domesticated felines.",
        "Dogs": "Dogs are loyal domesticated canines.",
        "Rockets": "Rockets use combustion to reach orbit.",
    }
    for nid, body in fixtures.items():
        insert_node(conn, nid, "concept", body=body)
        embed_upsert(conn, retrieval_config, nid, "concept", nid, body)
    assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == 3

    swapped = replace(retrieval_config, embedding_model_id=SWAP_MODEL_ID, embedding_dims=SWAP_MODEL_DIMS)
    changed = ensure_embedding_schema(conn, swapped.embedding_model_id, swapped.embedding_dims)
    assert changed is True
    assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == 0

    reembedded = sum(embed_upsert(conn, swapped, nid, "concept", nid, body) for nid, body in fixtures.items())
    assert reembedded == 3
    assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == 3
    for nid in fixtures:
        assert get_cached_vector(conn, nid).shape == (SWAP_MODEL_DIMS,)

    hits = find_similar(conn, swapped, "small furry pet animal", k=3)
    assert hits[0].node["id"] == "Cats"
