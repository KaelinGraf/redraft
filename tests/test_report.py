"""report.overview() against a hand-built fixture graph with known answers -- same style as
test_integrity.py (insert_node/insert_edge against the `conn` fixture, no embeddings needed).

Graph shape (every node's testing purpose is single-and-explicit):
  Root A (concept)                        -- a genuine SPINE root: has 3 direct part_of
                                              children (branches), so it appears in
                                              overview()'s `roots`
    Branch A1 (concept, 2-line body)      -- a rich branch: 5 descendants, mixed tallies
      Decision With Rationale (accepted)  -- HAS inbound justifies -> not unjustified
      Its Rationale (rationale)           -- justifies -> Decision With Rationale
      Decision Without Rationale (proposed) -- no inbound justifies -> unjustified
      Open Q Under A1 (question, open)    -- the graph's one open question
      Resolved Q Under A1 (question, resolved) -- excluded from open_question_count
    Branch A2 (concept)                   -- an empty branch: zero descendants
    Dangling Source (decision, accepted)  -- a branch that is ITSELF a decision (exercises
                                              subtree_ids' self-inclusive tallying); no
                                              inbound justifies -> unjustified; references a
                                              nonexistent node -> the graph's one dangling edge
  Root B (concept)                        -- parentless AND childless: zero edges touching it
                                              at all. Off the spine entirely per overview()'s
                                              contract -- tallied in floating_by_type, not
                                              listed in `roots`. Still a root per root_ids()'s
                                              own (broader, unchanged) contract -- see
                                              test_resources.py's graph://project/root test.
  Orphan Idea (idea)                      -- same story as Root B: parentless, childless,
                                              zero edges -> floating_by_type, not `roots`
"""
from __future__ import annotations

from conftest import insert_edge, insert_node
from redraft.report import overview


def _build_graph(conn):
    insert_node(conn, "Root A", "concept")
    insert_node(conn, "Branch A1", "concept", body="First line.\nSecond line ignored.")
    insert_edge(conn, "Branch A1", "Root A", "part_of")

    insert_node(conn, "Decision With Rationale", "decision", status="accepted")
    insert_edge(conn, "Decision With Rationale", "Branch A1", "part_of")
    insert_node(conn, "Its Rationale", "rationale")
    insert_edge(conn, "Its Rationale", "Decision With Rationale", "justifies")
    insert_edge(conn, "Its Rationale", "Branch A1", "part_of")

    insert_node(conn, "Decision Without Rationale", "decision", status="proposed")
    insert_edge(conn, "Decision Without Rationale", "Branch A1", "part_of")

    insert_node(conn, "Open Q Under A1", "question", status="open")
    insert_edge(conn, "Open Q Under A1", "Branch A1", "part_of")

    insert_node(conn, "Resolved Q Under A1", "question", status="resolved")
    insert_edge(conn, "Resolved Q Under A1", "Branch A1", "part_of")

    insert_node(conn, "Branch A2", "concept")
    insert_edge(conn, "Branch A2", "Root A", "part_of")

    insert_node(conn, "Dangling Source", "decision", status="accepted")
    insert_edge(conn, "Dangling Source", "Root A", "part_of")
    insert_edge(conn, "Dangling Source", "Nonexistent Target", "references")

    insert_node(conn, "Root B", "concept")  # no edges at all -- root AND orphan simultaneously

    insert_node(conn, "Orphan Idea", "idea")  # zero edges -- also a root, per root_ids()


def test_overview_roots_excludes_childless_parentless_nodes(conn):
    """Deliberate behavior change from the old "every parentless node is a root" contract:
    Root B and Orphan Idea are both parentless AND childless (no part_of subtree of their own),
    so they are off the spine entirely -- tallied by type in floating_by_type, not listed in
    `roots` with an empty branches list. Root A is the graph's only genuine spine root."""
    _build_graph(conn)
    result = overview(conn)
    assert [r.id for r in result.roots] == ["Root A"]
    assert result.floating_by_type == {"concept": 1, "idea": 1}  # Root B, Orphan Idea


def test_overview_every_kept_root_has_at_least_one_branch(conn):
    """OverviewRoot's own contract (models.py): a childless parentless node never appears in
    `roots` -- every element that does has a non-empty branches list."""
    _build_graph(conn)
    result = overview(conn)
    assert result.roots  # sanity: the fixture does have a genuine spine root
    assert all(r.branches for r in result.roots)


def test_overview_well_attached_rationale_with_no_part_of_is_floating_not_a_root(conn):
    """A rationale that justifies a real decision -- well-attached, correct-by-design per
    organizing-protocol.md -- but carries no part_of edge at all still lands in
    floating_by_type exactly like a genuinely disconnected node would: the field is a
    structural tally (parentless + childless), not an integrity verdict. Distinguishing
    "well-attached via justifies" from "actually orphaned" is retrieval.integrity.orphans()'s
    job, not overview()'s."""
    insert_node(conn, "Root", "concept")
    insert_node(conn, "Branch", "concept")
    insert_edge(conn, "Branch", "Root", "part_of")
    insert_node(conn, "Decision", "decision", status="accepted")
    insert_edge(conn, "Decision", "Branch", "part_of")
    insert_node(conn, "Well-Attached Rationale", "rationale")
    insert_edge(conn, "Well-Attached Rationale", "Decision", "justifies")  # attached, not via part_of

    result = overview(conn)
    assert [r.id for r in result.roots] == ["Root"]
    assert result.floating_by_type == {"rationale": 1}


def test_overview_nascent_graph_all_parentless_nodes_childless(conn):
    """Brand-new graph: a lone top-level concept with nothing under it yet (plus another
    parentless node). No genuine spine exists yet, so `roots` stays empty and
    floating_by_type carries the whole tally instead of hiding it."""
    insert_node(conn, "Lone Concept", "concept")
    insert_node(conn, "Lone Idea", "idea")

    result = overview(conn)
    assert result.roots == []
    assert result.floating_by_type == {"concept": 1, "idea": 1}


def test_overview_roots_sorted_by_subtree_size_desc_then_created_asc(conn):
    """Kept roots order: bigger part_of subtrees (branch count + all descendants) first;
    equal-size roots keep created-ascending order (root_id_list already arrives created-ASC
    from root_ids(), and the size sort is stable, so ties fall through to that pre-existing
    order -- this pins the full contract, not just the DESC half)."""
    insert_node(conn, "Root Big", "concept", created="2026-01-01T00:00:00Z")
    insert_node(conn, "Big Branch", "concept")
    insert_edge(conn, "Big Branch", "Root Big", "part_of")
    insert_node(conn, "Big Child 1", "concept")
    insert_edge(conn, "Big Child 1", "Big Branch", "part_of")
    insert_node(conn, "Big Child 2", "concept")
    insert_edge(conn, "Big Child 2", "Big Branch", "part_of")
    # Root Big subtree size = 1 branch + 2 descendants = 3

    insert_node(conn, "Root Medium", "concept", created="2026-01-04T00:00:00Z")
    insert_node(conn, "Medium Branch", "concept")
    insert_edge(conn, "Medium Branch", "Root Medium", "part_of")
    insert_node(conn, "Medium Child", "concept")
    insert_edge(conn, "Medium Child", "Medium Branch", "part_of")
    # Root Medium subtree size = 1 branch + 1 descendant = 2

    insert_node(conn, "Root Tie B", "concept", created="2026-01-02T00:00:00Z")
    insert_node(conn, "Tie B Branch", "concept")
    insert_edge(conn, "Tie B Branch", "Root Tie B", "part_of")
    # Root Tie B subtree size = 1 branch + 0 descendants = 1

    insert_node(conn, "Root Tie A", "concept", created="2026-01-03T00:00:00Z")
    insert_node(conn, "Tie A Branch", "concept")
    insert_edge(conn, "Tie A Branch", "Root Tie A", "part_of")
    # Root Tie A subtree size = 1 branch + 0 descendants = 1

    result = overview(conn)
    assert [r.id for r in result.roots] == ["Root Big", "Root Medium", "Root Tie B", "Root Tie A"]


def test_overview_branch_ordering_and_identity(conn):
    _build_graph(conn)
    result = overview(conn)
    root_a = next(r for r in result.roots if r.id == "Root A")
    assert [b.id for b in root_a.branches] == ["Branch A1", "Branch A2", "Dangling Source"]


def test_overview_branch_tallies_rich_subtree(conn):
    """Branch A1: 5 descendants, one justified decision (excluded from unjustified), one
    unjustified, one open question, one resolved question (excluded), decisions grouped by
    status, and a first-line excerpt (second body line dropped)."""
    _build_graph(conn)
    result = overview(conn)
    root_a = next(r for r in result.roots if r.id == "Root A")
    a1 = next(b for b in root_a.branches if b.id == "Branch A1")

    assert a1.descendant_count == 5
    assert a1.open_question_count == 1
    assert a1.decision_counts_by_status == {"accepted": 1, "proposed": 1}
    assert a1.unjustified_decision_count == 1
    assert a1.excerpt == "First line."
    assert a1.status is None


def test_overview_branch_tallies_empty_subtree(conn):
    _build_graph(conn)
    result = overview(conn)
    root_a = next(r for r in result.roots if r.id == "Root A")
    a2 = next(b for b in root_a.branches if b.id == "Branch A2")

    assert a2.descendant_count == 0
    assert a2.open_question_count == 0
    assert a2.decision_counts_by_status == {}
    assert a2.unjustified_decision_count == 0
    assert a2.excerpt == ""


def test_overview_branch_that_is_itself_a_decision_is_self_inclusive(conn):
    """Regression guard for the self-inclusive-vs-exclusive distinction overview()'s own
    docstring calls out: a branch that is ITSELF a decision must count toward its own
    decision_counts_by_status / unjustified_decision_count, even though it has zero actual
    descendants (descendant_count stays 0, self-EXCLUSIVE, per traverse()'s own contract)."""
    _build_graph(conn)
    result = overview(conn)
    root_a = next(r for r in result.roots if r.id == "Root A")
    dangling = next(b for b in root_a.branches if b.id == "Dangling Source")

    assert dangling.descendant_count == 0
    assert dangling.status == "accepted"
    assert dangling.decision_counts_by_status == {"accepted": 1}
    assert dangling.unjustified_decision_count == 1
    assert dangling.open_question_count == 0


def test_overview_totals(conn):
    _build_graph(conn)
    result = overview(conn)
    t = result.totals
    assert t.counts_by_type == {"concept": 4, "decision": 3, "rationale": 1, "question": 2, "idea": 1}
    assert t.counts_by_status == {"accepted": 2, "proposed": 1, "open": 1, "resolved": 1}
    assert t.orphan_count == 2  # Root B (branchless root) and Orphan Idea both touch zero edges
    assert t.dangling_edge_count == 1  # Dangling Source -[references]-> Nonexistent Target


def test_overview_top_open_questions(conn):
    _build_graph(conn)
    result = overview(conn)
    assert [q.id for q in result.top_open_questions] == ["Open Q Under A1"]


def test_overview_empty_graph(conn):
    result = overview(conn)
    assert result.roots == []
    assert result.floating_by_type == {}
    assert result.totals.counts_by_type == {}
    assert result.totals.counts_by_status == {}
    assert result.totals.orphan_count == 0
    assert result.totals.dangling_edge_count == 0
    assert result.top_open_questions == []


def test_overview_top_open_questions_capped_at_ten(conn):
    insert_node(conn, "Root", "concept")
    insert_node(conn, "Branch", "concept")
    insert_edge(conn, "Branch", "Root", "part_of")
    for i in range(12):
        insert_node(conn, f"Question {i:02d}", "question", status="open")
        insert_edge(conn, f"Question {i:02d}", "Branch", "part_of")

    result = overview(conn)
    assert len(result.top_open_questions) == 10  # the whole-graph list is capped
    branch = result.roots[0].branches[0]
    assert branch.open_question_count == 12  # the per-branch tally itself is NOT capped


def test_overview_branch_excerpt_truncates_long_first_line(conn):
    insert_node(conn, "Root", "concept")
    insert_node(conn, "Branch", "concept", body="x" * 200)
    insert_edge(conn, "Branch", "Root", "part_of")

    result = overview(conn)
    excerpt = result.roots[0].branches[0].excerpt
    assert len(excerpt) == 141  # 140 chars + one ellipsis character
    assert excerpt.endswith("…")
