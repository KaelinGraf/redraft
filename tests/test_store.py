"""design-storage.md §6 (write path) and §6.2 (lock design)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from redraft import index
from redraft import store as store_module
from redraft.errors import CollisionError, CycleError, NotFoundError
from redraft.locking import write_lock
from redraft.retrieval.embeddings import RetrievalConfig
from redraft.schema import EdgeType, NodeType
from redraft.store import GraphStore


# -- create_node ------------------------------------------------------------------------------


def test_create_node_rejects_same_title_collision(store):
    store.create_node(type=NodeType.CONCEPT, title="Duplicate Title")
    with pytest.raises(CollisionError):
        store.create_node(type=NodeType.CONCEPT, title="Duplicate Title")


def test_create_node_rejects_case_insensitive_collision(store):
    store.create_node(type=NodeType.CONCEPT, title="Case Test")
    with pytest.raises(CollisionError):
        store.create_node(type=NodeType.CONCEPT, title="CASE TEST")
    with pytest.raises(CollisionError):
        store.create_node(type=NodeType.CONCEPT, title="case test")


def test_create_node_rejects_part_of_passed_via_edges_dict(store):
    # part_of has its own top-level kwarg (scalar, cycle-checked); letting it through the
    # generic `edges` dict too would collide when constructing Node(part_of=..., **edge_lists)
    # and previously crashed with a confusing "multiple values for keyword argument" TypeError.
    a = store.create_node(type=NodeType.CONCEPT, title="Edges Dict Parent")
    with pytest.raises(ValueError):
        store.create_node(type=NodeType.CONCEPT, title="Edges Dict Child", edges={EdgeType.PART_OF: a.id})


def test_create_node_default_status_by_type(store):
    decision = store.create_node(type=NodeType.DECISION, title="A Decision")
    question = store.create_node(type=NodeType.QUESTION, title="A Question")
    milestone = store.create_node(type=NodeType.MILESTONE, title="A Milestone")
    concept = store.create_node(type=NodeType.CONCEPT, title="A Concept")
    assert decision.status == "proposed"
    assert question.status == "open"
    assert milestone.status == "planned"
    assert concept.status is None


# -- size caps (Batch-B hardening: unbounded body/title) ---------------------------------------


def test_create_node_rejects_oversized_body(store):
    from redraft.store import MAX_BODY_BYTES

    with pytest.raises(ValueError, match="body exceeds"):
        store.create_node(type=NodeType.CONCEPT, title="Too Big", body="x" * (MAX_BODY_BYTES + 1))
    assert store.con.execute("SELECT 1 FROM nodes WHERE id = ?", ("Too Big",)).fetchone() is None


def test_create_node_normal_sized_body_still_works(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Normal Size", body="x" * 1000)
    assert len(node.body) == 1000


def test_create_node_rejects_oversized_title(store):
    from redraft.store import MAX_TITLE_CHARS

    with pytest.raises(ValueError, match="title exceeds"):
        store.create_node(type=NodeType.CONCEPT, title="x" * (MAX_TITLE_CHARS + 1))


def test_update_node_rejects_oversized_replace_body(store):
    from redraft.store import MAX_BODY_BYTES

    node = store.create_node(type=NodeType.CONCEPT, title="Update Too Big")
    with pytest.raises(ValueError, match="body exceeds"):
        store.update_node(node.id, body="x" * (MAX_BODY_BYTES + 1), mode="replace")


def test_update_node_rejects_append_that_crosses_cap_via_accumulation(store):
    from redraft.store import MAX_BODY_BYTES

    node = store.create_node(type=NodeType.CONCEPT, title="Grows Via Append", body="x" * (MAX_BODY_BYTES - 10))
    with pytest.raises(ValueError, match="body exceeds"):
        store.update_node(node.id, body="y" * 100)  # neither chunk alone is oversized
    assert store.get_node(node.id).body == "x" * (MAX_BODY_BYTES - 10)  # untouched, no partial write


def test_update_node_normal_sized_body_still_works(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Update Normal Size")
    updated = store.update_node(node.id, body="y" * 1000, mode="replace")
    assert len(updated.body) == 1000


# -- control characters (Batch-C hardening: NUL etc. round-tripping into YAML invisibly) -------


def test_create_node_rejects_nul_in_title(store):
    with pytest.raises(ValueError, match="control character U\\+0000"):
        store.create_node(type=NodeType.CONCEPT, title="bad\x00title")
    assert store.con.execute("SELECT 1 FROM nodes").fetchone() is None


def test_create_node_rejects_newline_in_title(store):
    # titles are single-line labels -- even \n/\t are wrong there, unlike in a body
    with pytest.raises(ValueError, match="control character U\\+000A"):
        store.create_node(type=NodeType.CONCEPT, title="two\nlines")


def test_create_node_rejects_nul_in_body(store):
    with pytest.raises(ValueError, match="control character U\\+0000"):
        store.create_node(type=NodeType.CONCEPT, title="Nul Body", body="bad\x00body")


def test_create_node_accepts_newline_and_tab_in_body(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Normal Prose Body", body="line one\n\tindented line two")
    assert node.body == "line one\n\tindented line two"


def test_update_node_rejects_nul_in_appended_body(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Update Nul Target")
    with pytest.raises(ValueError, match="control character U\\+0000"):
        store.update_node(node.id, body="bad\x00chunk")
    assert store.get_node(node.id).body == ""  # untouched, no partial write


def test_update_node_accepts_newline_and_tab_in_body(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Update Normal Prose")
    updated = store.update_node(node.id, body="a line\n\ta tabbed line", mode="replace")
    assert updated.body == "a line\n\ta tabbed line"


def test_rename_node_rejects_nul_in_new_title(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Rename Nul Target")
    with pytest.raises(ValueError, match="control character U\\+0000"):
        store.rename_node(node.id, "bad\x00new title")
    assert store.get_node(node.id).title == "Rename Nul Target"  # untouched


# -- part_of cycles / single-parent -----------------------------------------------------------


def test_part_of_direct_and_transitive_cycle_rejected_and_no_file_modified(store):
    a = store.create_node(type=NodeType.CONCEPT, title="A Root")
    b = store.create_node(type=NodeType.CONCEPT, title="B Child", part_of=a.id)
    c = store.create_node(type=NodeType.CONCEPT, title="C Grandchild", part_of=b.id)

    a_path = store.paths.nodes_dir / f"{a.id}.md"
    before = a_path.read_bytes()

    with pytest.raises(CycleError):
        store.create_edge(a.id, a.id, EdgeType.PART_OF)  # direct self-cycle
    with pytest.raises(CycleError):
        store.create_edge(a.id, c.id, EdgeType.PART_OF)  # transitive: c is already a's grandchild

    assert a_path.read_bytes() == before
    assert store.get_node(a.id).part_of is None


def test_part_of_enforces_single_primary_parent(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Parent A")
    b = store.create_node(type=NodeType.CONCEPT, title="Parent B")
    d = store.create_node(type=NodeType.CONCEPT, title="Child D", part_of=a.id)
    assert store.get_node(d.id).part_of == a.id

    # A1: create_edge no longer silently overwrites a different existing parent — the caller
    # must delete_edge the old part_of first, then create_edge the new one (reconciliation R2).
    store.delete_edge(d.id, a.id, EdgeType.PART_OF)
    store.create_edge(d.id, b.id, EdgeType.PART_OF)
    reparented = store.get_node(d.id)
    assert reparented.part_of == b.id

    text = (store.paths.nodes_dir / f"{d.id}.md").read_text()
    assert text.count("part_of:") == 1  # never a list, never duplicated


def test_create_edge_part_of_rejects_silent_reparent(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Silent Reparent Old")
    b = store.create_node(type=NodeType.CONCEPT, title="Silent Reparent New")
    d = store.create_node(type=NodeType.CONCEPT, title="Silent Reparent Child", part_of=a.id)
    path = store.paths.nodes_dir / f"{d.id}.md"
    before = path.read_bytes()

    with pytest.raises(CollisionError) as excinfo:
        store.create_edge(d.id, b.id, EdgeType.PART_OF)

    assert a.id in str(excinfo.value)  # names the existing parent
    assert "delete_edge" in str(excinfo.value)  # directs the caller to clear it first
    assert path.read_bytes() == before  # nothing written
    assert store.get_node(d.id).part_of == a.id  # unchanged


def test_create_edge_part_of_same_parent_is_idempotent_no_op(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Idempotent Parent")
    d = store.create_node(type=NodeType.CONCEPT, title="Idempotent Child", part_of=a.id)
    path = store.paths.nodes_dir / f"{d.id}.md"
    mtime_after_create = path.stat().st_mtime_ns

    store.create_edge(d.id, a.id, EdgeType.PART_OF)  # same parent again: no-op, not a collision

    assert store.get_node(d.id).part_of == a.id
    assert path.stat().st_mtime_ns == mtime_after_create  # true no-op: no rewrite at all


# -- create_edge / delete_edge ------------------------------------------------------------------


def test_create_edge_idempotent_on_duplicate_call(store):
    a = store.create_node(type=NodeType.OBSERVATION, title="Obs A")
    b = store.create_node(type=NodeType.ARTIFACT, title="Art B")
    store.create_edge(a.id, b.id, EdgeType.REFERENCES)
    path = store.paths.nodes_dir / f"{a.id}.md"
    mtime_after_first = path.stat().st_mtime_ns

    store.create_edge(a.id, b.id, EdgeType.REFERENCES)
    assert store.get_node(a.id).references == [b.id]  # not duplicated
    assert path.stat().st_mtime_ns == mtime_after_first  # true no-op: no second write at all


def test_create_edge_cross_type_violation_is_warning_not_error(store):
    concept = store.create_node(type=NodeType.CONCEPT, title="Some Concept")
    artifact = store.create_node(type=NodeType.ARTIFACT, title="Some Artifact")
    # justifies conventionally wants a rationale -> decision edge; concept -> artifact violates it
    edge = store.create_edge(concept.id, artifact.id, EdgeType.JUSTIFIES)
    assert edge.warning is not None
    assert store.get_node(concept.id).justifies == [artifact.id]  # still created despite the warning


# -- create_edges (batch) ------------------------------------------------------------------------


def test_create_edges_happy_path_mixed_types_in_input_order(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Batch Parent A")
    b = store.create_node(type=NodeType.CONCEPT, title="Batch Ref B")
    c = store.create_node(type=NodeType.IDEA, title="Batch Child C")

    edges = store.create_edges([(c.id, a.id, "part_of"), (c.id, b.id, "references")])

    assert [e.type for e in edges] == [EdgeType.PART_OF, EdgeType.REFERENCES]
    assert [(e.src, e.dst) for e in edges] == [(c.id, a.id), (c.id, b.id)]
    node = store.get_node(c.id)
    assert node.part_of == a.id
    assert node.references == [b.id]


def test_create_edges_shared_source_loaded_and_written_exactly_once(store, monkeypatch):
    a = store.create_node(type=NodeType.CONCEPT, title="Shared Src Parent A")
    b = store.create_node(type=NodeType.CONCEPT, title="Shared Src Ref B")
    c = store.create_node(type=NodeType.IDEA, title="Shared Src Child C")

    real_atomic_write = store_module._atomic_write
    write_calls: list[Path] = []

    def counting_atomic_write(path, text):
        write_calls.append(path)
        real_atomic_write(path, text)

    monkeypatch.setattr(store_module, "_atomic_write", counting_atomic_write)

    store.create_edges([(c.id, a.id, "part_of"), (c.id, b.id, "references")])

    # two edges from the SAME source (c) collapse into exactly one file write, not two
    assert write_calls == [store.paths.nodes_dir / f"{c.id}.md"]
    node = store.get_node(c.id)
    assert node.part_of == a.id
    assert node.references == [b.id]


def test_create_edges_within_batch_cycle_rejected_and_nothing_written(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Batch Cycle A")
    b = store.create_node(type=NodeType.CONCEPT, title="Batch Cycle B")
    a_before = (store.paths.nodes_dir / f"{a.id}.md").read_bytes()
    b_before = (store.paths.nodes_dir / f"{b.id}.md").read_bytes()

    # Neither edge alone cycles against pre-batch state (neither a nor b has a parent yet) --
    # only together do they form a 2-cycle. Must be caught considering the OTHER edge in this
    # same batch, and must leave BOTH files untouched.
    with pytest.raises(CycleError):
        store.create_edges([(a.id, b.id, "part_of"), (b.id, a.id, "part_of")])

    assert (store.paths.nodes_dir / f"{a.id}.md").read_bytes() == a_before
    assert (store.paths.nodes_dir / f"{b.id}.md").read_bytes() == b_before
    assert store.get_node(a.id).part_of is None
    assert store.get_node(b.id).part_of is None


def test_create_edges_within_batch_collision_rejected_and_nothing_written(store):
    p1 = store.create_node(type=NodeType.CONCEPT, title="Batch Collision Parent 1")
    p2 = store.create_node(type=NodeType.CONCEPT, title="Batch Collision Parent 2")
    x = store.create_node(type=NodeType.IDEA, title="Batch Collision Child X")
    x_before = (store.paths.nodes_dir / f"{x.id}.md").read_bytes()

    # x has no parent yet; the batch tries to give it two DIFFERENT parents in one call.
    with pytest.raises(CollisionError):
        store.create_edges([(x.id, p1.id, "part_of"), (x.id, p2.id, "part_of")])

    assert (store.paths.nodes_dir / f"{x.id}.md").read_bytes() == x_before
    assert store.get_node(x.id).part_of is None


def test_create_edges_part_of_rejects_preexisting_different_parent(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Batch Preexisting Old Parent")
    b = store.create_node(type=NodeType.CONCEPT, title="Batch Preexisting New Parent")
    d = store.create_node(type=NodeType.CONCEPT, title="Batch Preexisting Child", part_of=a.id)
    d_before = (store.paths.nodes_dir / f"{d.id}.md").read_bytes()

    # The A1 single-parent rule still applies against state from BEFORE this batch started.
    with pytest.raises(CollisionError):
        store.create_edges([(d.id, b.id, "part_of")])

    assert (store.paths.nodes_dir / f"{d.id}.md").read_bytes() == d_before
    assert store.get_node(d.id).part_of == a.id


def test_create_edges_missing_dst_rejected_and_nothing_written(store):
    a = store.create_node(type=NodeType.OBSERVATION, title="Batch Missing Dst A")
    b = store.create_node(type=NodeType.ARTIFACT, title="Batch Missing Dst B")
    a_before = (store.paths.nodes_dir / f"{a.id}.md").read_bytes()

    # The first edge alone is perfectly valid; the second names a dst that doesn't exist. The
    # whole batch must fail and even the valid first edge must NOT be applied.
    with pytest.raises(NotFoundError):
        store.create_edges([(a.id, b.id, "references"), (a.id, "Does Not Exist", "relates_to")])

    assert (store.paths.nodes_dir / f"{a.id}.md").read_bytes() == a_before
    assert store.get_node(a.id).references == []


def test_create_edges_duplicate_within_batch_and_preexisting_are_idempotent(store):
    a = store.create_node(type=NodeType.OBSERVATION, title="Batch Dup A")
    b = store.create_node(type=NodeType.ARTIFACT, title="Batch Dup B")
    store.create_edge(a.id, b.id, EdgeType.REFERENCES)  # already present before the batch
    path = store.paths.nodes_dir / f"{a.id}.md"
    mtime_before = path.stat().st_mtime_ns

    edges = store.create_edges(
        [(a.id, b.id, "references"), (a.id, b.id, "references")]  # already-present + in-batch dup
    )

    assert len(edges) == 2  # a fully no-op edge still returns its own Edge in the result
    assert all(e.src == a.id and e.dst == b.id and e.type == EdgeType.REFERENCES for e in edges)
    assert store.get_node(a.id).references == [b.id]  # not duplicated
    assert path.stat().st_mtime_ns == mtime_before  # true no-op: no write at all


def test_create_edges_warnings_surfaced_per_edge(store):
    concept = store.create_node(type=NodeType.CONCEPT, title="Batch Warn Concept")
    artifact = store.create_node(type=NodeType.ARTIFACT, title="Batch Warn Artifact")
    decision = store.create_node(type=NodeType.DECISION, title="Batch Warn Decision", status="accepted")

    # justifies conventionally wants a rationale -> decision edge; concept -> artifact violates it.
    # relates_to is unconstrained (graphrules._EDGE_CONVENTIONS has no entry for it) -- no warning.
    edges = store.create_edges([(concept.id, artifact.id, "justifies"), (concept.id, decision.id, "relates_to")])

    assert edges[0].warning is not None
    assert edges[1].warning is None


def test_create_edges_self_part_of_rejected_as_cycle_matching_create_edge(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Batch Self Part Of")
    with pytest.raises(CycleError):
        store.create_edges([(a.id, a.id, "part_of")])
    assert store.get_node(a.id).part_of is None


def test_create_edges_self_reference_list_edge_allowed_matching_create_edge(store):
    # Mirrors test_rename_node_self_reference_is_relabeled_not_dropped's own create_edge(a, a,
    # RELATES_TO) call: a self-reference on a LIST edge type is a legitimate no-op-free write,
    # not an error -- create_edges must not add a src==dst guard that create_edge itself lacks.
    a = store.create_node(type=NodeType.CONCEPT, title="Batch Self Reference")
    edges = store.create_edges([(a.id, a.id, "relates_to")])
    assert edges[0].src == edges[0].dst == a.id
    assert store.get_node(a.id).relates_to == [a.id]


def test_delete_edge_omits_key_when_list_becomes_empty(store):
    a = store.create_node(type=NodeType.OBSERVATION, title="Obs For Delete")
    b = store.create_node(type=NodeType.ARTIFACT, title="Art For Delete")
    store.create_edge(a.id, b.id, EdgeType.REFERENCES)
    assert "references:" in (store.paths.nodes_dir / f"{a.id}.md").read_text()

    store.delete_edge(a.id, b.id, EdgeType.REFERENCES)
    text = (store.paths.nodes_dir / f"{a.id}.md").read_text()
    assert "references:" not in text
    assert store.get_node(a.id).references == []


def test_delete_edge_works_on_dangling_edge(store):
    # A3: only src must exist — this is the exact dangling_edges() -> delete_edge repair loop
    # that was previously impossible, since dst (b) is deleted before the cleanup call.
    a = store.create_node(type=NodeType.OBSERVATION, title="Dangling Edge Src")
    b = store.create_node(type=NodeType.ARTIFACT, title="Dangling Edge Dst")
    store.create_edge(a.id, b.id, EdgeType.REFERENCES)
    store.delete_node(b.id)  # b's file gone; a's reference to b is now dangling

    dangling = index.dangling_edges(store.con)
    assert any(e[1] == a.id and e[2] == b.id for e in dangling)

    store.delete_edge(a.id, b.id, EdgeType.REFERENCES)  # must succeed even though b no longer exists

    assert store.get_node(a.id).references == []
    assert "references:" not in (store.paths.nodes_dir / f"{a.id}.md").read_text()
    assert not any(e[1] == a.id and e[2] == b.id for e in index.dangling_edges(store.con))


# -- update_node (remove_properties) ------------------------------------------------------------


def test_update_node_remove_properties_deletes_a_key(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Removable Prop", properties={"a": 1, "b": 2})
    updated = store.update_node(node.id, remove_properties=["a"])
    assert updated.properties == {"b": 2}
    assert store.get_node(node.id).properties == {"b": 2}


def test_update_node_remove_properties_absent_key_is_noop(store):
    node = store.create_node(type=NodeType.CONCEPT, title="No Such Key", properties={"a": 1})
    updated = store.update_node(node.id, remove_properties=["does-not-exist"])
    assert updated.properties == {"a": 1}
    assert updated.updated == node.updated  # no-op short-circuit: no `updated` bump either


def test_update_node_remove_properties_wins_over_merge_of_same_key(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Merge Then Remove", properties={"a": 1})
    updated = store.update_node(node.id, properties={"a": 99, "b": 2}, remove_properties=["a"])
    assert updated.properties == {"b": 2}
    assert store.get_node(node.id).properties == {"b": 2}


def test_update_node_remove_properties_leaves_other_keys_alone(store):
    node = store.create_node(type=NodeType.CONCEPT, title="Partial Removal", properties={"a": 1, "b": 2, "c": 3})
    updated = store.update_node(node.id, remove_properties=["b"])
    assert updated.properties == {"a": 1, "c": 3}


# -- delete_node --------------------------------------------------------------------------------


def test_delete_node_leaves_inbound_edges_dangling_not_deleted(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Deletion Target")
    b = store.create_node(type=NodeType.OBSERVATION, title="Referrer Of Target")
    store.create_edge(b.id, a.id, EdgeType.REFERENCES)

    store.delete_node(a.id)

    with pytest.raises(NotFoundError):
        store.get_node(a.id)
    assert not (store.paths.nodes_dir / f"{a.id}.md").exists()
    dangling = index.dangling_edges(store.con)
    assert any(e[1] == b.id and e[2] == a.id for e in dangling)
    assert store.get_node(b.id).references == [a.id]  # b's own edge list is untouched


# -- rename_node ----------------------------------------------------------------------------------


def test_rename_node_rewrites_all_inbound_wikilinks(store):
    target = store.create_node(type=NodeType.CONCEPT, title="Original Title")
    child = store.create_node(type=NodeType.CONCEPT, title="Child Node", part_of=target.id)
    referrer = store.create_node(type=NodeType.OBSERVATION, title="Referrer Node")
    store.create_edge(referrer.id, target.id, EdgeType.REFERENCES)

    renamed = store.rename_node(target.id, "Renamed Title")

    assert renamed.id == "Renamed Title"
    assert not (store.paths.nodes_dir / f"{target.id}.md").exists()
    assert (store.paths.nodes_dir / "Renamed Title.md").exists()
    assert store.get_node(child.id).part_of == "Renamed Title"
    assert store.get_node(referrer.id).references == ["Renamed Title"]
    with pytest.raises(NotFoundError):
        store.get_node(target.id)


def test_rename_node_rejects_new_title_collision(store):
    store.create_node(type=NodeType.CONCEPT, title="Foo")
    bar = store.create_node(type=NodeType.CONCEPT, title="Bar")
    with pytest.raises(CollisionError):
        store.rename_node(bar.id, "Foo")
    assert (store.paths.nodes_dir / "Bar.md").exists()
    assert store.get_node("Bar").title == "Bar"


def test_rename_node_rejects_collision_even_when_existing_content_coincidentally_matches(store):
    # Guards the resumable-retry escape hatch added for the A4 fix: it must NOT be fooled by two
    # independently-created nodes that happen to be byte-for-byte identical once created/updated
    # are stripped (two freshly-made, still-default concept nodes, exactly this case) -- telling
    # "my own abandoned duplicate" apart from "a genuinely different, unrelated collision" can't
    # rely on content matching alone, which is why provenance is proven with a marker file instead.
    store.create_node(type=NodeType.CONCEPT, title="Foo")
    bar = store.create_node(type=NodeType.CONCEPT, title="Bar")
    with pytest.raises(CollisionError):
        store.rename_node(bar.id, "Foo")
    assert (store.paths.nodes_dir / "Bar.md").exists()
    assert store.get_node("Bar").title == "Bar"
    assert store.get_node("Foo").title == "Foo"  # untouched, not silently absorbed


def test_rename_node_does_not_bump_updated_on_referrer_files(store):
    target = store.create_node(type=NodeType.CONCEPT, title="Rename Target")
    referrer = store.create_node(type=NodeType.OBSERVATION, title="Stable Referrer")
    store.create_edge(referrer.id, target.id, EdgeType.REFERENCES)
    original_updated = store.get_node(referrer.id).updated

    store.rename_node(target.id, "Rename Target Renamed")

    assert store.get_node(referrer.id).updated == original_updated
    assert store.get_node(referrer.id).references == ["Rename Target Renamed"]


def test_rename_node_postcondition_ignores_unrelated_preexisting_dangling_edge(store):
    # Regression test: an earlier implementation restricted dangling_edges() to "any edge
    # touching an affected id" rather than "edges targeting the retired id specifically", which
    # false-triggered the postcondition AssertionError whenever a referrer happened to carry its
    # OWN unrelated pre-existing dangling edge (e.g. from an earlier delete_node) alongside the
    # edge actually being rewritten.
    target = store.create_node(type=NodeType.CONCEPT, title="Rename Target With Noise")
    ghost = store.create_node(type=NodeType.ARTIFACT, title="Soon Deleted Ghost")
    referrer = store.create_node(type=NodeType.OBSERVATION, title="Noisy Referrer")
    store.create_edge(referrer.id, target.id, EdgeType.REFERENCES)
    store.create_edge(referrer.id, ghost.id, EdgeType.RELATES_TO)
    store.delete_node(ghost.id)  # referrer now also carries an unrelated dangling edge

    renamed = store.rename_node(target.id, "Rename Target With Noise Renamed")  # must not raise

    assert store.get_node(referrer.id).references == [renamed.id]
    assert store.get_node(referrer.id).relates_to == [ghost.id]  # unrelated dangling ref untouched


def test_rename_node_title_only_change_keeps_id_and_referrer_edges_valid(store):
    target = store.create_node(type=NodeType.CONCEPT, title="Case Only")
    referrer = store.create_node(type=NodeType.OBSERVATION, title="Case Only Referrer")
    store.create_edge(referrer.id, target.id, EdgeType.REFERENCES)

    # sanitize_title_to_id is case-preserving, so re-asserting the exact same title is a
    # genuine new_id == id no-op path, not a rename to a different id.
    renamed = store.rename_node(target.id, "Case Only")

    assert renamed.id == target.id
    assert (store.paths.nodes_dir / f"{target.id}.md").exists()
    assert store.get_node(referrer.id).references == [target.id]


def test_rename_node_self_reference_is_relabeled_not_dropped(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Self Referencing")
    store.create_edge(a.id, a.id, EdgeType.RELATES_TO)
    assert store.get_node(a.id).relates_to == [a.id]

    renamed = store.rename_node(a.id, "Self Referencing Renamed")
    assert renamed.relates_to == [renamed.id]


def test_rename_node_is_retriable_after_crash_mid_referrer_rewrite(tmp_path, monkeypatch):
    # A4: referrers are persisted before the node's own file is retired, so a crash between two
    # referrer writes leaves `id` still resolvable — a retry finds the already-rewritten referrer
    # no longer pointing at `id` (its file/index already show new_id) and simply finishes the rest.
    store = GraphStore(tmp_path)
    target = store.create_node(type=NodeType.CONCEPT, title="Retry Rename Target")
    ref_a = store.create_node(type=NodeType.OBSERVATION, title="Retry Referrer A")
    ref_b = store.create_node(type=NodeType.OBSERVATION, title="Retry Referrer B")
    store.create_edge(ref_a.id, target.id, EdgeType.REFERENCES)
    store.create_edge(ref_b.id, target.id, EdgeType.REFERENCES)

    real_atomic_write = store_module._atomic_write
    call_count = {"n": 0}

    def flaky_atomic_write(path, text):
        call_count["n"] += 1
        if call_count["n"] == 2:  # first referrer already landed on disk; crash before the second
            raise RuntimeError("simulated crash mid rename_node")
        real_atomic_write(path, text)

    monkeypatch.setattr(store_module, "_atomic_write", flaky_atomic_write)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.rename_node(target.id, "Retry Rename Target Renamed")
    monkeypatch.undo()
    # Simulate the crashed process vanishing (its uncommitted transaction with it), then a fresh
    # process starting up: a new GraphStore/connection whose __init__ reindex() repairs the index
    # from disk truth (design §6.3) before the retry runs.
    store.con.rollback()
    store.con.close()
    store2 = GraphStore(tmp_path)

    renamed = store2.rename_node(target.id, "Retry Rename Target Renamed")  # must complete now

    assert renamed.id == "Retry Rename Target Renamed"
    assert not (store2.paths.nodes_dir / f"{target.id}.md").exists()
    assert (store2.paths.nodes_dir / "Retry Rename Target Renamed.md").exists()
    assert store2.get_node(ref_a.id).references == [renamed.id]
    assert store2.get_node(ref_b.id).references == [renamed.id]
    stale = store2.con.execute("SELECT 1 FROM edges WHERE dst = ?", (target.id,)).fetchone()
    assert stale is None


def test_rename_node_crash_mid_referrer_loop_leaves_zero_dangling_and_heals_on_retry(tmp_path, monkeypatch):
    """Batch-B regression for the pre-fix dangling-referrer bug: the OLD ordering wrote every
    OTHER referrer before the renamed node's own file, so a crash after K of N referrers landed
    left those K pointing at an id that had never come into existence on disk -- permanently
    dangling. The fixed ordering writes new_id's own file FIRST, so a crash mid-referrer-loop
    must leave dangling_edges() == [] (every referrer, rewritten or not, still names an id that
    exists) even though old and new titles briefly coexist as a transient duplicate -- and a
    bare reindex + an identical retry of this exact call must heal that duplicate down to
    exactly one node, reachable under exactly the new title.
    """
    store = GraphStore(tmp_path)
    target = store.create_node(type=NodeType.CONCEPT, title="Alpha")
    referrers = [
        store.create_node(type=NodeType.OBSERVATION, title=f"R{i}", edges={EdgeType.RELATES_TO: [target.id]})
        for i in range(1, 6)
    ]

    real_atomic_write = store_module._atomic_write
    count = {"n": 0}
    K = 3  # crash after the 3rd of 5 referrers has landed on disk

    def flaky_atomic_write(path, text):
        real_atomic_write(path, text)
        if path.stem.startswith("R"):
            count["n"] += 1
            if count["n"] == K:
                raise RuntimeError("simulated crash mid rename_node referrer loop")

    monkeypatch.setattr(store_module, "_atomic_write", flaky_atomic_write)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.rename_node(target.id, "Alpha Renamed")
    monkeypatch.undo()
    # Simulate the crashed process vanishing (its uncommitted transaction with it) and a fresh
    # process starting up, whose GraphStore.__init__ reindex() rebuilds the index from disk truth.
    store.con.rollback()
    store.con.close()
    store2 = GraphStore(tmp_path)

    assert index.dangling_edges(store2.con) == []
    # Both titles legitimately coexist right now -- the expected transient duplicate, not a bug.
    node_ids_after_crash = {row[0] for row in store2.con.execute("SELECT id FROM nodes").fetchall()}
    assert {"Alpha", "Alpha Renamed"} <= node_ids_after_crash

    renamed = store2.rename_node(target.id, "Alpha Renamed")  # identical retry: must not raise
    assert renamed.id == "Alpha Renamed"

    store3 = GraphStore(tmp_path)
    assert index.dangling_edges(store3.con) == []
    node_ids = {row[0] for row in store3.con.execute("SELECT id FROM nodes").fetchall()}
    assert "Alpha Renamed" in node_ids
    assert "Alpha" not in node_ids  # healed: reachable under exactly one title now
    for r in referrers:
        assert store3.get_node(r.id).relates_to == ["Alpha Renamed"]


def test_rename_node_with_retrieval_survives_crash_mid_referrer_rewrite(tmp_path, monkeypatch):
    """S3b write-path integration: the same A4 crash-retriability guarantee
    test_rename_node_is_retriable_after_crash_mid_referrer_rewrite establishes must still
    hold with retrieval enabled -- the embed_upsert/embed_delete calls threaded into
    rename_node must not reintroduce the pre-A4 failure mode."""
    cfg = RetrievalConfig()
    store = GraphStore(tmp_path, retrieval_config=cfg)
    target = store.create_node(type=NodeType.CONCEPT, title="Embed Retry Target", body="stable body text")
    ref_a = store.create_node(type=NodeType.OBSERVATION, title="Embed Retry Referrer A")
    ref_b = store.create_node(type=NodeType.OBSERVATION, title="Embed Retry Referrer B")
    store.create_edge(ref_a.id, target.id, EdgeType.REFERENCES)
    store.create_edge(ref_b.id, target.id, EdgeType.REFERENCES)

    real_atomic_write = store_module._atomic_write
    call_count = {"n": 0}

    def flaky_atomic_write(path, text):
        call_count["n"] += 1
        if call_count["n"] == 2:  # first referrer already landed; crash before the second
            raise RuntimeError("simulated crash mid rename_node")
        real_atomic_write(path, text)

    monkeypatch.setattr(store_module, "_atomic_write", flaky_atomic_write)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.rename_node(target.id, "Embed Retry Target Renamed")
    monkeypatch.undo()
    store.con.rollback()
    store.con.close()

    store2 = GraphStore(tmp_path, retrieval_config=cfg)  # fresh process; __init__ reindexes + re-embeds
    renamed = store2.rename_node(target.id, "Embed Retry Target Renamed")  # must complete now

    assert renamed.id == "Embed Retry Target Renamed"
    assert store2.get_node(ref_a.id).references == [renamed.id]
    assert store2.get_node(ref_b.id).references == [renamed.id]
    vec_row = store2.con.execute(
        "SELECT 1 FROM node_vectors WHERE node_id = ?", ("Embed Retry Target Renamed",)
    ).fetchone()
    assert vec_row is not None, "renamed node must be embedded, not left stale"
    old_vec_row = store2.con.execute("SELECT 1 FROM node_vectors WHERE node_id = ?", (target.id,)).fetchone()
    assert old_vec_row is None, "old id's vector must not linger"


def test_rename_node_embed_delete_never_commits_a_neither_id_gap(tmp_path, monkeypatch):
    """Correctness bug found in review and fixed here: embed_upsert/embed_delete commit
    internally (S3a's own contract) -- unlike the plain index._* calls the rest of
    rename_node uses, which stay uncommitted until self.con.commit() at the very end. The
    first cut of the write-path wiring called embed_delete(old id) BEFORE
    index._upsert_node_index(renamed); if that intermediate commit fired, it durably
    removed the old id's row before the new id's row existed -- a real window (proved via
    direct experiment) where NEITHER id resolves in the index even though the renamed file
    is already on disk. Fixed by moving embed_delete/embed_upsert to run only after BOTH
    index._remove_node_index(old id) and index._upsert_node_index(renamed) have already
    been issued, so any embed-triggered early commit captures old-removed and new-created
    atomically together. This test inspects a genuinely separate, freshly-opened raw
    connection (not a new GraphStore, which would reindex() and mask the gap regardless of
    ordering) to see exactly what is durably committed at the crash instant.
    """
    import sqlite3

    cfg = RetrievalConfig()
    store = GraphStore(tmp_path, retrieval_config=cfg)
    target = store.create_node(type=NodeType.CONCEPT, title="Gap Check Target", body="stable body text")
    new_id = "Gap Check Target Renamed"

    real_upsert = index._upsert_node_index

    def crash_after_new_id_upsert(con, node, content_hash):
        real_upsert(con, node, content_hash)
        if node.id == new_id:
            raise RuntimeError("simulated crash right after the renamed node's own index upsert")

    monkeypatch.setattr(index, "_upsert_node_index", crash_after_new_id_upsert)
    with pytest.raises(RuntimeError, match="simulated crash"):
        store.rename_node(target.id, new_id)
    monkeypatch.undo()

    raw = sqlite3.connect(str(store.paths.index_db))
    try:
        old_row = raw.execute("SELECT 1 FROM nodes WHERE id = ?", (target.id,)).fetchone()
        new_row = raw.execute("SELECT 1 FROM nodes WHERE id = ?", (new_id,)).fetchone()
        assert (store.paths.nodes_dir / f"{new_id}.md").exists(), "file rename already happened"
        assert not (old_row is None and new_row is None), (
            "neither old nor new id is durably committed, even though the renamed file "
            "already exists on disk -- the exact gap the embed-call ordering fix closes"
        )
    finally:
        raw.close()
        store.con.close()


# -- merge_nodes ------------------------------------------------------------------------------


def test_merge_nodes_repoints_inbound_edges_and_deletes_drop_file(store):
    keep = store.create_node(type=NodeType.CONCEPT, title="Keep Node")
    drop = store.create_node(type=NodeType.CONCEPT, title="Drop Node")
    referrer = store.create_node(type=NodeType.OBSERVATION, title="Merge Referrer")
    store.create_edge(referrer.id, drop.id, EdgeType.REFERENCES)

    outcome = store.merge_nodes(keep.id, drop.id)

    assert outcome.kept.id == keep.id
    assert not (store.paths.nodes_dir / f"{drop.id}.md").exists()
    with pytest.raises(NotFoundError):
        store.get_node(drop.id)
    assert store.get_node(referrer.id).references == [keep.id]


def test_merge_nodes_avoids_self_loop_when_keep_already_referenced_drop(store):
    keep = store.create_node(type=NodeType.CONCEPT, title="Keep With Existing Ref")
    drop = store.create_node(type=NodeType.CONCEPT, title="Drop Target")
    store.create_edge(keep.id, drop.id, EdgeType.RELATES_TO)

    store.merge_nodes(keep.id, drop.id)

    result = store.get_node(keep.id)
    assert keep.id not in result.relates_to  # dropped, not rewritten into a self-loop
    assert drop.id not in result.relates_to


def test_merge_nodes_rejects_keep_equals_drop(store):
    a = store.create_node(type=NodeType.CONCEPT, title="Solo Node")
    with pytest.raises(ValueError):
        store.merge_nodes(a.id, a.id)


def test_merge_nodes_handles_drop_self_reference_without_error(store):
    # drop referencing itself means drop_id appears as its own referrer row (src=dst=drop_id);
    # that entry must not be written back to drop's file right before os.remove()'ing it. It
    # must also not leak into keep via A2's outbound migration as a dangling keep -> drop_id ref.
    keep = store.create_node(type=NodeType.CONCEPT, title="Merge Keep Plain")
    drop = store.create_node(type=NodeType.CONCEPT, title="Merge Drop Self Ref")
    store.create_edge(drop.id, drop.id, EdgeType.RELATES_TO)

    outcome = store.merge_nodes(keep.id, drop.id)

    assert outcome.kept.id == keep.id
    assert drop.id not in outcome.kept.relates_to  # self-ref dropped, not migrated as a dangling ref
    assert not (store.paths.nodes_dir / f"{drop.id}.md").exists()
    with pytest.raises(NotFoundError):
        store.get_node(drop.id)


def test_merge_nodes_rejects_cycle_from_repointed_part_of(store):
    root = store.create_node(type=NodeType.CONCEPT, title="Merge Cycle Root")
    keep = store.create_node(type=NodeType.CONCEPT, title="Merge Cycle Keep", part_of=root.id)
    drop = store.create_node(type=NodeType.CONCEPT, title="Merge Cycle Drop")
    # root currently points at drop; merging keep<-drop would repoint root's part_of to keep,
    # but keep is already root's own child -> cycle.
    store.create_edge(root.id, drop.id, EdgeType.PART_OF)

    with pytest.raises(CycleError):
        store.merge_nodes(keep.id, drop.id)
    assert (store.paths.nodes_dir / f"{drop.id}.md").exists()  # nothing touched


def test_merge_nodes_migrates_drop_outbound_edges_deduped(store):
    keep = store.create_node(type=NodeType.CONCEPT, title="Outbound Migrate Keep")
    drop = store.create_node(type=NodeType.CONCEPT, title="Outbound Migrate Drop")
    shared = store.create_node(type=NodeType.ARTIFACT, title="Outbound Shared Target")
    only_on_drop = store.create_node(type=NodeType.ARTIFACT, title="Outbound Drop Only Target")
    referrer = store.create_node(type=NodeType.OBSERVATION, title="Outbound Migrate Referrer")
    store.create_edge(keep.id, shared.id, EdgeType.REFERENCES)
    store.create_edge(drop.id, shared.id, EdgeType.REFERENCES)  # dup of keep's existing target
    store.create_edge(drop.id, only_on_drop.id, EdgeType.REFERENCES)  # drop-only target
    store.create_edge(drop.id, keep.id, EdgeType.RELATES_TO)  # would-be self-loop onto keep
    store.create_edge(referrer.id, drop.id, EdgeType.REFERENCES)  # pure inbound relink
    keep_path = store.paths.nodes_dir / f"{keep.id}.md"
    keep_mtime_before = keep_path.stat().st_mtime_ns
    referrer_updated_before = store.get_node(referrer.id).updated

    outcome = store.merge_nodes(keep.id, drop.id)

    assert sorted(outcome.kept.references) == sorted([shared.id, only_on_drop.id])  # deduped
    assert keep.id not in outcome.kept.relates_to  # no self-loop introduced by migration
    assert keep_path.stat().st_mtime_ns != keep_mtime_before  # keep's file was actually rewritten
    assert outcome.warnings == []
    assert outcome.dropped_body_preview == ""
    assert store.get_node(referrer.id).references == [keep.id]
    assert store.get_node(referrer.id).updated == referrer_updated_before  # pure relink, not bumped


def test_merge_nodes_dropped_body_preview_truncates_to_280_chars(store):
    keep = store.create_node(type=NodeType.CONCEPT, title="Body Preview Keep")
    long_body = "x" * 400
    drop = store.create_node(type=NodeType.CONCEPT, title="Body Preview Drop", body=long_body)

    outcome = store.merge_nodes(keep.id, drop.id)

    assert outcome.dropped_body_preview == long_body[:280]
    assert len(outcome.dropped_body_preview) == 280


def test_merge_nodes_adopts_drop_part_of_when_keep_has_none(store):
    parent = store.create_node(type=NodeType.CONCEPT, title="Adopted Parent")
    keep = store.create_node(type=NodeType.CONCEPT, title="Adoption Keep")
    drop = store.create_node(type=NodeType.CONCEPT, title="Adoption Drop", part_of=parent.id)

    outcome = store.merge_nodes(keep.id, drop.id)

    assert outcome.kept.part_of == parent.id
    assert outcome.warnings == []


def test_merge_nodes_part_of_conflict_skips_adoption_and_warns(store):
    keep_parent = store.create_node(type=NodeType.CONCEPT, title="Keep Own Parent")
    drop_parent = store.create_node(type=NodeType.CONCEPT, title="Drop Own Parent")
    keep = store.create_node(type=NodeType.CONCEPT, title="Conflict Keep", part_of=keep_parent.id)
    drop = store.create_node(type=NodeType.CONCEPT, title="Conflict Drop", part_of=drop_parent.id)

    outcome = store.merge_nodes(keep.id, drop.id)

    assert outcome.kept.part_of == keep_parent.id  # keep's own parent wins, drop's not adopted
    assert len(outcome.warnings) == 1
    assert drop_parent.id in outcome.warnings[0]


def test_merge_nodes_rejects_cycle_from_part_of_adoption(store):
    # keep has no parent, so merge would try to adopt drop's parent (keep_child) — but keep_child
    # is already keep's own child, so adopting it as keep's parent would form a cycle.
    keep = store.create_node(type=NodeType.CONCEPT, title="Adoption Cycle Keep")
    keep_child = store.create_node(type=NodeType.CONCEPT, title="Adoption Cycle Keep Child", part_of=keep.id)
    drop = store.create_node(type=NodeType.CONCEPT, title="Adoption Cycle Drop", part_of=keep_child.id)

    with pytest.raises(CycleError):
        store.merge_nodes(keep.id, drop.id)

    assert (store.paths.nodes_dir / f"{drop.id}.md").exists()  # nothing touched — rejected pre-write
    assert store.get_node(keep.id).part_of is None


def test_merge_nodes_failure_rolls_back_and_does_not_leak_into_next_write(store):
    """CRITICAL fix: self.con is one persistent connection for the store's whole life, and
    every mutator issues its index._upsert_node_index/_remove_node_index calls uncommitted,
    only committing at the very end. Before _locked_transaction existed, a mid-method
    exception (here: os.remove on drop's file raising, as if the file were already gone via
    an external race — merge_nodes writes referrer/keep's rewritten files+index rows to
    reflect the repoint to `keep` BEFORE this final os.remove(drop) step) left those partial
    INDEX writes sitting in a still-open transaction — never rolled back — so the NEXT
    unrelated commit (a totally different create_node) swept them into the sqlite index too,
    durably repointing R's edge from Drop to Keep in the index despite merge_nodes having
    raised. This is checked against a RAW connection to the index db (never a fresh
    GraphStore, whose reindex() would rebuild the index from the already-rewritten on-disk
    referrer file regardless of the fix, masking the very distinction being tested) so only
    what the sqlite index itself durably committed is observed. Reproduced by the
    orchestrator; fixed by GraphStore._locked_transaction rolling back on any exception
    escaping the locked body.
    """
    import sqlite3

    keep = store.create_node(type=NodeType.CONCEPT, title="Rollback Merge Keep")
    drop = store.create_node(type=NodeType.CONCEPT, title="Rollback Merge Drop")
    referrer = store.create_node(type=NodeType.CONCEPT, title="Rollback Merge Referrer")
    store.create_edge(referrer.id, drop.id, EdgeType.RELATES_TO)

    real_remove = os.remove

    def flaky_remove(path):
        if Path(path).stem == drop.id:
            raise OSError("simulated: file already gone (external race)")
        return real_remove(path)

    original = store_module.os.remove
    store_module.os.remove = flaky_remove
    try:
        with pytest.raises(OSError, match="already gone"):
            store.merge_nodes(keep.id, drop.id)
    finally:
        store_module.os.remove = original

    # the failed mutation must not leave an open transaction behind
    assert store.con.in_transaction is False

    # a subsequent, wholly unrelated write must not durably drag the failed merge's partial
    # index writes (referrer repointed to keep) along with its own, unrelated commit
    store.create_node(type=NodeType.CONCEPT, title="Rollback Merge Unrelated")

    raw = sqlite3.connect(str(store.paths.index_db))
    try:
        edge_rows = raw.execute("SELECT src, dst FROM edges WHERE src = ?", (referrer.id,)).fetchall()
    finally:
        raw.close()
    assert edge_rows == [(referrer.id, drop.id)], (
        "referrer's edge in the durable sqlite index must still point at drop — the failed "
        "merge's in-flight repoint to keep must never have been committed, whether by its own "
        "commit or leaked into a later, unrelated one"
    )


# -- NFC filename self-heal (reindex) -------------------------------------------------------------


def test_nfd_filename_node_becomes_mutable_after_reindex_self_heal(tmp_path):
    # A foreign tool (macOS Finder, a native editor) can hand-drop a node file whose FILENAME is
    # NFD-decomposed even though load_node_file/reindex NFC-normalize the in-memory id -- every
    # mutator derives its path from that NFC id via _node_path, so before the reindex self-heal
    # (index.reindex renaming the file to its NFC form) this node was visible in search/get_node
    # yet raised FileNotFoundError from update_node/delete_node/create_edge(as src) forever.
    import unicodedata

    nfc_title = "café"
    nfd_title = unicodedata.normalize("NFD", nfc_title)
    assert nfc_title != nfd_title
    nodes_dir = tmp_path / "graph" / "nodes"
    nodes_dir.mkdir(parents=True)
    (nodes_dir / f"{nfd_title}.md").write_bytes(
        (
            "---\ntype: concept\ntitle: café\n"
            "created: '2026-01-01T00:00:00Z'\nupdated: '2026-01-01T00:00:00Z'\n---\n\n"
            "hand-dropped node, NFD filename\n"
        ).encode("utf-8")
    )

    store = GraphStore(tmp_path)  # constructor's own reindex() must self-heal the filename
    assert (nodes_dir / f"{nfc_title}.md").exists()
    assert not (nodes_dir / f"{nfd_title}.md").exists()

    indexed_id = store.con.execute("SELECT id FROM nodes").fetchone()[0]
    assert indexed_id == nfc_title

    other = store.create_node(type=NodeType.OBSERVATION, title="Refers To Cafe")

    updated = store.update_node(indexed_id, body="now mutable", mode="replace")
    assert updated.body == "now mutable"
    edge = store.create_edge(indexed_id, other.id, EdgeType.RELATES_TO)
    assert edge.src == indexed_id
    store.delete_node(indexed_id)
    with pytest.raises(NotFoundError):
        store.get_node(indexed_id)


# -- locking ------------------------------------------------------------------------------------


def test_write_lock_serializes_two_concurrent_writers(tmp_path):
    store = GraphStore(tmp_path)
    events: list[tuple[str, str]] = []
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        barrier.wait()
        with store._write_lock():
            events.append((name, "start"))
            time.sleep(0.1)
            events.append((name, "end"))

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(events) == 4
    # the lock must fully serialize the two critical sections: [start, end] of one thread must
    # never straddle the other's — i.e. events come in matched (start, end) pairs from the SAME
    # thread, never interleaved as [A-start, B-start, A-end, B-end].
    assert events[0][1] == "start" and events[1] == (events[0][0], "end")
    assert events[2][1] == "start" and events[3] == (events[2][0], "end")
    assert events[0][0] != events[2][0]


def test_write_lock_released_after_kill9_of_holder(tmp_path):
    marker = tmp_path / "acquired.marker"
    script = (
        "from pathlib import Path\n"
        "from redraft.locking import write_lock\n"
        f"lock = write_lock(Path({str(tmp_path)!r}))\n"
        "with lock:\n"
        f"    Path({str(marker)!r}).write_text('locked')\n"
        "    import time\n"
        "    time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            if time.monotonic() > deadline:
                pytest.fail("subprocess never signaled that it acquired the lock")
            time.sleep(0.05)

        proc.kill()  # SIGKILL on POSIX: the exact "kill -9" scenario design §6.2 relies on
        proc.wait(timeout=5)

        lock2 = write_lock(tmp_path)
        start = time.monotonic()
        with lock2.acquire(timeout=5):
            elapsed = time.monotonic() - start
        assert elapsed < 5, "lock was not released promptly after the holder was killed -9"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
