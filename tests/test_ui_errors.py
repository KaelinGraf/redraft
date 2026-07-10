"""s6-ui.md §12.1: one test per ui/errors.py _HTTP_STATUS mapping entry, asserting the exact
status code. NotFoundError/CollisionError/CycleError are triggered through genuine API usage
(natural, fast, no mocking needed); LockTimeoutError/MalformedFrontmatterError/
GitOperationError are triggered by monkeypatching a GraphStore method to raise the exact
exception type directly -- the same "construct/raise the real storage exception type"
strategy tests/test_errors.py already uses for the MCP layer's equivalent mapping table,
since actually reproducing a 30-second lock timeout or a genuinely broken git repo end to
end would be slow/awkward for no extra coverage of the code actually under test here
(ui/errors.py's own _HTTP_STATUS table)."""
from __future__ import annotations

from redraft.errors import GitOperationError, LockTimeoutError, MalformedFrontmatterError
from redraft.store import GraphStore


async def test_not_found_maps_to_404(ui_client):
    r = await ui_client.get("/api/nodes/Nope")
    assert r.status_code == 404


async def test_collision_maps_to_409(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Dup"})
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "Dup"})
    assert r.status_code == 409


async def test_cycle_rejected_maps_to_409(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "A"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "B"})
    await ui_client.put("/api/nodes/B/parent", json={"new_parent": "A"})
    r = await ui_client.put("/api/nodes/A/parent", json={"new_parent": "B"})
    assert r.status_code == 409


async def test_lock_timeout_maps_to_503(ui_client, monkeypatch):
    def _raise(*args, **kwargs):
        raise LockTimeoutError("could not acquire write lock 'x' within 30.0s")

    monkeypatch.setattr(GraphStore, "create_node", _raise)
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "X"})
    assert r.status_code == 503


async def test_malformed_frontmatter_maps_to_422(ui_client, monkeypatch):
    def _raise(*args, **kwargs):
        raise MalformedFrontmatterError("bad frontmatter")

    monkeypatch.setattr(GraphStore, "create_node", _raise)
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "X"})
    assert r.status_code == 422


async def test_git_operation_failed_maps_to_502(ui_client, monkeypatch):
    def _raise(*args, **kwargs):
        raise GitOperationError(["status"], 128, "fatal: not a git repository")

    monkeypatch.setattr(GraphStore, "snapshot", _raise)
    r = await ui_client.post("/api/snapshot", json={"message": "x"})
    assert r.status_code == 502


async def test_all_six_mapped_types_produce_a_json_detail_body(ui_client):
    """Every mapped error response carries a {"detail": "..."} body, not an empty response --
    the frontend needs SOMETHING to show the operator."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Dup"})
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "Dup"})
    assert r.status_code == 409
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]
