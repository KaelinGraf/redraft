"""design-storage.md §5 (projector/indexer): reindex algorithm, FTS sync, dangling edges."""

from __future__ import annotations

import shutil
from unittest.mock import patch

from redraft import index
from redraft.schema import EdgeType, NodeType
from redraft.store import GraphStore


def _rows(store, table, order="id"):
    return store.con.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()


def test_reindex_empty_graph_produces_empty_valid_index(tmp_path):
    store = GraphStore(tmp_path)
    assert _rows(store, "nodes") == []
    assert _rows(store, "edges") == []
    stats = store.reindex()
    assert stats.scanned == 0
    assert stats.upserted == 0
    assert stats.deleted == 0
    assert stats.malformed == []


def test_reindex_after_deleting_index_directory_reproduces_identical_query_results(tmp_path):
    store = GraphStore(tmp_path)
    a = store.create_node(type=NodeType.CONCEPT, title="Root Concept", body="root")
    b = store.create_node(type=NodeType.DECISION, title="A Decision", body="d", part_of=a.id)
    store.create_edge(b.id, a.id, EdgeType.RELATES_TO)
    before_nodes = _rows(store, "nodes")
    before_edges = _rows(store, "edges")
    assert before_nodes and before_edges

    shutil.rmtree(tmp_path / "index")  # simulate a fresh-device clone: no derived index at all
    store2 = GraphStore(tmp_path)  # __init__ reindexes automatically (design §5.3)
    after_nodes = _rows(store2, "nodes")
    after_edges = _rows(store2, "edges")
    assert after_nodes == before_nodes
    assert after_edges == before_edges


def test_reindex_is_idempotent_when_rerun_with_no_changes(tmp_path):
    store = GraphStore(tmp_path)
    store.create_node(type=NodeType.CONCEPT, title="Idempotent Concept")
    before = _rows(store, "nodes")
    stats1 = store.reindex()
    stats2 = store.reindex()
    assert stats1.upserted == 0  # already upserted synchronously by create_node
    assert stats2.upserted == 0
    assert stats2.deleted == 0
    assert _rows(store, "nodes") == before


def test_reindex_skips_reparsing_files_whose_hash_is_unchanged(tmp_path):
    store = GraphStore(tmp_path)
    for i in range(3):
        store.create_node(type=NodeType.CONCEPT, title=f"Concept {i}")

    with patch("redraft.index.load_node_file", wraps=index.load_node_file) as spy:
        stats = store.reindex()
        assert spy.call_count == 0  # every content_hash already matches; nothing needed reparsing
        assert stats.upserted == 0


def test_externally_edited_file_is_picked_up_on_reindex(tmp_path):
    store = GraphStore(tmp_path)
    node = store.create_node(type=NodeType.OBSERVATION, title="Observed Thing", body="original body")
    path = store.paths.nodes_dir / f"{node.id}.md"
    text = path.read_text()
    edited = text.replace("original body", "edited by hand, outside the server")
    path.write_text(edited)

    stats = store.reindex()
    assert stats.upserted == 1
    assert store.get_node(node.id).body == "edited by hand, outside the server"


def test_reindex_never_writes_to_node_files(tmp_path):
    store = GraphStore(tmp_path)
    ids = [store.create_node(type=NodeType.IDEA, title=f"Idea {i}").id for i in range(3)]
    paths = [store.paths.nodes_dir / f"{i}.md" for i in ids]
    before = [(p.stat().st_mtime_ns, p.read_bytes()) for p in paths]

    store.reindex()

    after = [(p.stat().st_mtime_ns, p.read_bytes()) for p in paths]
    assert before == after


def test_dangling_edge_flagged_not_dropped_when_target_file_removed(tmp_path):
    store = GraphStore(tmp_path)
    a = store.create_node(type=NodeType.CONCEPT, title="Concept Target")
    b = store.create_node(type=NodeType.OBSERVATION, title="Observation Source")
    store.create_edge(b.id, a.id, EdgeType.REFERENCES)

    (store.paths.nodes_dir / f"{a.id}.md").unlink()  # external deletion, bypassing delete_node
    store.reindex()

    dangling = index.dangling_edges(store.con)
    assert any(e[1] == b.id and e[2] == a.id for e in dangling)
    # and it must NOT have been silently dropped from the edges table entirely
    assert any(row[1] == b.id and row[2] == a.id for row in _rows(store, "edges"))


def test_dangling_edges_stay_live_via_left_join(tmp_path):
    store = GraphStore(tmp_path)
    a = store.create_node(type=NodeType.CONCEPT, title="Will Be Removed")
    b = store.create_node(type=NodeType.OBSERVATION, title="Referrer Node")
    store.create_edge(b.id, a.id, EdgeType.REFERENCES)

    (store.paths.nodes_dir / f"{a.id}.md").unlink()
    store.reindex()
    assert any(e[2] == a.id for e in index.dangling_edges(store.con))

    # recreate the target under the exact same id: the same live query must clear on its own,
    # with no flag/state anywhere that needs manual repair.
    store.create_node(type=NodeType.CONCEPT, title="Will Be Removed")
    store.reindex()
    assert not any(e[2] == a.id for e in index.dangling_edges(store.con))


def test_malformed_node_file_does_not_abort_full_reindex(tmp_path):
    store = GraphStore(tmp_path)
    good1 = store.create_node(type=NodeType.CONCEPT, title="Good One")
    good2 = store.create_node(type=NodeType.CONCEPT, title="Good Two")
    bad_path = store.paths.nodes_dir / "Bad Node.md"
    bad_path.write_text("---\ntype: concept\n---\n\nmissing title/created/updated.\n")

    stats = store.reindex()

    assert stats.malformed and stats.malformed[0][0] == "Bad Node"
    assert store.get_node(good1.id).id == good1.id
    assert store.get_node(good2.id).id == good2.id


def test_fts_index_stays_in_sync_via_triggers_on_upsert_and_delete(tmp_path):
    store = GraphStore(tmp_path)
    node = store.create_node(
        type=NodeType.OBSERVATION, title="Searchable Node", body="unique_searchable_token_xyz appears here"
    )

    hits = store.con.execute(
        "SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'unique_searchable_token_xyz'"
    ).fetchall()
    assert len(hits) == 1

    store.update_node(node.id, body="a completely different body now", mode="replace")
    hits_after_update = store.con.execute(
        "SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'unique_searchable_token_xyz'"
    ).fetchall()
    assert hits_after_update == []
    hits_new = store.con.execute("SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'different'").fetchall()
    assert len(hits_new) == 1

    store.delete_node(node.id)
    hits_after_delete = store.con.execute("SELECT rowid FROM nodes_fts WHERE nodes_fts MATCH 'different'").fetchall()
    assert hits_after_delete == []
