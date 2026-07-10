"""fts.py — tokenizer + FTS5 candidate retrieval. No sqlite-vec / embeddings needed."""

from __future__ import annotations

from conftest import insert_node
from redraft.retrieval.fts import build_match_expr, fts_candidates, tokenize_query


def test_tokenize_query_preserves_hyphen_dot_underscore():
    assert tokenize_query("find sm_120 near RTX-5090 v1.5") == ["find", "sm_120", "near", "RTX-5090", "v1.5"]


def test_tokenize_query_empty_or_punctuation_only():
    assert tokenize_query("") == []
    assert tokenize_query("   ") == []
    assert tokenize_query("???!!!") == []


def test_build_match_expr_or_joins_quoted_tokens():
    assert build_match_expr("sm_120 apples") == '"sm_120" OR "apples"'


def test_build_match_expr_none_for_unmatchable_query():
    assert build_match_expr("   ") is None
    assert build_match_expr("!!!") is None


def test_fts_candidates_finds_exact_identifier_in_body(conn):
    insert_node(conn, "Backbone Choice", "decision", body="We picked sm_120 as the minimum compute capability target.")
    insert_node(conn, "Unrelated", "concept", body="Some other text about apples and oranges.")
    ids = fts_candidates(conn, "sm_120", limit=10)
    assert ids == ["Backbone Choice"]


def test_fts_candidates_or_joins_across_terms(conn):
    insert_node(conn, "A", "concept", body="alpha content")
    insert_node(conn, "B", "concept", body="beta content")
    insert_node(conn, "C", "concept", title="gamma", body="unrelated filler text")
    ids = set(fts_candidates(conn, "alpha beta", limit=10))
    assert ids == {"A", "B"}


def test_fts_candidates_empty_or_unmatchable_query_returns_empty(conn):
    insert_node(conn, "A", "concept", body="alpha")
    assert fts_candidates(conn, "", limit=10) == []
    assert fts_candidates(conn, "!!!", limit=10) == []


def test_fts_candidates_zero_limit_returns_empty(conn):
    insert_node(conn, "A", "concept", body="alpha")
    assert fts_candidates(conn, "alpha", limit=0) == []


def test_fts_candidates_limit_respected(conn):
    for i in range(5):
        insert_node(conn, f"Node {i}", "concept", body="shared keyword content")
    ids = fts_candidates(conn, "shared", limit=2)
    assert len(ids) == 2


def test_fts_candidates_ranks_best_match_first(conn):
    insert_node(conn, "Weak", "concept", body="mentions robot once in passing")
    insert_node(conn, "Strong", "concept", title="robot robot robot", body="robot everything about robots")
    ids = fts_candidates(conn, "robot", limit=10)
    assert ids[0] == "Strong"
