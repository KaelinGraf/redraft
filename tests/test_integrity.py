"""Integrity queries against one hand-built fixture graph with known answers. Pure
nodes/edges -- no embeddings, no sqlite-vec needed.

Graph shape (every node's testing purpose is single-and-explicit):
  Root Concept                          -- part_of target for everything below; has
                                            inbound edges so it is not itself an orphan
  Decision With Rationale (decision)    -- HAS inbound `justifies` -> excluded from
  Rationale One (rationale)             --   decisions_without_rationale()
  Decision Without Rationale (decision) -- no inbound justifies -> INCLUDED
  Open Question (question, open)        -- INCLUDED in open_questions()
  Resolved Question (question, resolved)-- excluded from open_questions()
  Orphan Idea (idea)                    -- deliberately zero edges -> INCLUDED in orphans()
  Contradiction A/B (decision)          -- linked by a `contradicts` edge
  Stale Open Question (question, open, updated=2020) -- INCLUDED in stale(before=2025)
  Fresh Open Question (question, open, updated=2026-06) -- excluded from that stale() call
  Dangling Source (decision)            -- references a nonexistent node -> dangling_edges()
  CaseCollide / casecollide (concept)   -- identical under NFC+casefold -> case_collisions()
  Unique No Collision (concept)         -- control: must NOT appear in any collision group
"""

from __future__ import annotations

from conftest import insert_edge, insert_node
from redraft.retrieval import integrity


def _build_graph(conn):
    insert_node(conn, "Root Concept", "concept")

    insert_node(conn, "Decision With Rationale", "decision", status="accepted")
    insert_node(conn, "Rationale One", "rationale")
    insert_edge(conn, "Rationale One", "Decision With Rationale", "justifies")
    insert_edge(conn, "Rationale One", "Root Concept", "part_of")
    insert_edge(conn, "Decision With Rationale", "Root Concept", "part_of")

    insert_node(conn, "Decision Without Rationale", "decision", status="proposed")
    insert_edge(conn, "Decision Without Rationale", "Root Concept", "part_of")

    insert_node(conn, "Open Question", "question", status="open")
    insert_edge(conn, "Open Question", "Root Concept", "part_of")

    insert_node(conn, "Resolved Question", "question", status="resolved")
    insert_edge(conn, "Resolved Question", "Root Concept", "part_of")

    insert_node(conn, "Orphan Idea", "idea")  # deliberately zero edges

    insert_node(conn, "Contradiction A", "decision", status="accepted")
    insert_node(conn, "Contradiction B", "decision", status="accepted")
    insert_edge(conn, "Contradiction A", "Contradiction B", "contradicts")
    insert_edge(conn, "Contradiction A", "Root Concept", "part_of")
    insert_edge(conn, "Contradiction B", "Root Concept", "part_of")

    insert_node(conn, "Stale Open Question", "question", status="open", updated="2020-01-01T00:00:00Z")
    insert_edge(conn, "Stale Open Question", "Root Concept", "part_of")

    insert_node(conn, "Fresh Open Question", "question", status="open", updated="2026-06-01T00:00:00Z")
    insert_edge(conn, "Fresh Open Question", "Root Concept", "part_of")

    insert_node(conn, "Dangling Source", "decision", status="accepted")
    insert_edge(conn, "Dangling Source", "Root Concept", "part_of")
    insert_edge(conn, "Dangling Source", "Nonexistent Target", "references")

    insert_node(conn, "CaseCollide", "concept")
    insert_node(conn, "casecollide", "concept")
    insert_edge(conn, "CaseCollide", "Root Concept", "part_of")
    insert_edge(conn, "casecollide", "Root Concept", "part_of")

    insert_node(conn, "Unique No Collision", "concept")
    insert_edge(conn, "Unique No Collision", "Root Concept", "part_of")


def test_decisions_without_rationale(conn):
    _build_graph(conn)
    ids = {n["id"] for n in integrity.decisions_without_rationale(conn)}
    assert ids == {"Decision Without Rationale", "Contradiction A", "Contradiction B", "Dangling Source"}


def test_open_questions(conn):
    _build_graph(conn)
    ids = {n["id"] for n in integrity.open_questions(conn)}
    assert ids == {"Open Question", "Stale Open Question", "Fresh Open Question"}


def test_orphans(conn):
    _build_graph(conn)
    ids = {n["id"] for n in integrity.orphans(conn)}
    assert ids == {"Orphan Idea"}


def test_contradictions(conn):
    _build_graph(conn)
    pairs = integrity.contradictions(conn)
    assert len(pairs) == 1
    assert pairs[0]["a"]["id"] == "Contradiction A"
    assert pairs[0]["b"]["id"] == "Contradiction B"


def test_stale_default_params(conn):
    _build_graph(conn)
    ids = {n["id"] for n in integrity.stale(conn, before_iso="2025-01-01T00:00:00Z")}
    assert ids == {"Stale Open Question"}


def test_stale_custom_types_and_statuses(conn):
    _build_graph(conn)
    ids = {
        n["id"]
        for n in integrity.stale(conn, before_iso="2027-01-01T00:00:00Z", types=["decision"], statuses=["accepted"])
    }
    assert ids == {"Decision With Rationale", "Contradiction A", "Contradiction B", "Dangling Source"}


def test_stale_explicit_empty_list_means_no_types_not_default(conn):
    """Regression guard: types=[]/statuses=[] must be honored literally (empty result),
    not silently reinterpreted as "omitted -> use the default set" the way a plain
    `types or DEFAULT` truthiness check would (`[]` is falsy in Python)."""
    _build_graph(conn)
    assert integrity.stale(conn, before_iso="2027-01-01T00:00:00Z", types=[]) == []
    assert integrity.stale(conn, before_iso="2027-01-01T00:00:00Z", statuses=[]) == []


def test_dangling_edges(conn):
    _build_graph(conn)
    rows = integrity.dangling_edges(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["src"] == "Dangling Source"
    assert r["dst"] == "Nonexistent Target"
    assert r["type"] == "references"
    assert r["src_dangling"] is False
    assert r["dst_dangling"] is True


def test_case_collisions(conn):
    _build_graph(conn)
    groups = integrity.case_collisions(conn)
    normalized = {frozenset(g) for g in groups}
    assert frozenset({"CaseCollide", "casecollide"}) in normalized
    assert not any("Unique No Collision" in g for g in normalized)
    assert not any("Root Concept" in g for g in normalized)


def test_case_collisions_handles_comma_in_id_safely(conn):
    """Regression guard: GROUP_CONCAT must use a separator that can't appear in a
    legitimate id/title (titles are not forbidden from containing a comma) -- a naive
    default-comma GROUP_CONCAT would misparse this pair back apart."""
    insert_node(conn, "Alpha, Beta", "concept")
    insert_node(conn, "alpha, beta", "concept")
    groups = integrity.case_collisions(conn)
    normalized = {frozenset(g) for g in groups}
    assert frozenset({"Alpha, Beta", "alpha, beta"}) in normalized
