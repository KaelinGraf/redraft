"""s6-ui.md §12.1: snapshot/reindex/status/git-status shapes; git-status dirty-flag
correctness before/after a mutation and before/after a snapshot."""
from __future__ import annotations


async def test_status_shape_and_generation_advances_on_mutations(ui_client):
    r = await ui_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body == {"generation": 0, "last_reindex_at": None, "embedder_ready": body["embedder_ready"]}

    await ui_client.post("/api/nodes", json={"type": "concept", "title": "X"})
    r = await ui_client.get("/api/status")
    assert r.json()["generation"] == 1

    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Y"})
    r = await ui_client.get("/api/status")
    assert r.json()["generation"] == 2


async def test_reindex_shape(ui_client, graph_dir):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "X"})
    r = await ui_client.post("/api/reindex")
    assert r.status_code == 200
    body = r.json()
    assert body["scanned"] == 1
    assert body["deleted"] == 0
    assert body["malformed"] == []


async def test_snapshot_shape_and_commits(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "X"})
    r = await ui_client.post("/api/snapshot", json={"message": "first snapshot"})
    assert r.status_code == 200
    body = r.json()
    assert body["committed"] is True
    assert body["sha"] is not None
    assert len(body["sha"]) == 40
    assert body["pushed"] is False
    assert body["initialized_repo"] is True


async def test_snapshot_push_defaults_false(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "X"})
    r = await ui_client.post("/api/snapshot", json={"message": "no push field given"})
    assert r.json()["pushed"] is False


async def test_git_status_dirty_flag_transitions(ui_client, graph_dir):
    # Before any git repo exists at all (snapshot() has never run): reports clean, not an
    # error -- there is no working tree yet for "dirty" to meaningfully describe.
    r = await ui_client.get("/api/git-status")
    assert r.status_code == 200
    assert r.json() == {"dirty": False, "changed_paths": []}

    await ui_client.post("/api/nodes", json={"type": "concept", "title": "X"})
    await ui_client.post("/api/snapshot", json={"message": "initial commit"})

    r = await ui_client.get("/api/git-status")
    assert r.json() == {"dirty": False, "changed_paths": []}  # clean immediately after commit

    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Y"})
    r = await ui_client.get("/api/git-status")
    body = r.json()
    assert body["dirty"] is True
    assert "graph/nodes/Y.md" in body["changed_paths"]

    await ui_client.post("/api/snapshot", json={"message": "second commit"})
    r = await ui_client.get("/api/git-status")
    assert r.json() == {"dirty": False, "changed_paths": []}  # clean again after the second commit
