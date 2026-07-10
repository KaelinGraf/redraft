"""s6-ui.md §12.1: create (incl. part_of/edges pass-through), update, delete, rename
(referrer rewrite reflected in a follow-up outline fetch), merge (warnings surfaced)."""
from __future__ import annotations


async def test_create_node_minimal(ui_client):
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "Solo"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "Solo"
    assert body["type"] == "concept"
    assert body["status"] is None
    assert body["properties"] == {}


async def test_create_node_with_part_of_and_edges_pass_through(ui_client):
    """create_node's part_of/edges convenience params: GraphStore already supports them
    natively (unlike the MCP tool's deliberately narrower 5-param signature) -- the UI form
    can do "create under this parent, with links" in one submission."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent"})
    await ui_client.post("/api/nodes", json={"type": "decision", "title": "Other Decision", "status": "accepted"})
    r = await ui_client.post(
        "/api/nodes",
        json={
            "type": "rationale",
            "title": "A Rationale",
            "part_of": "Parent",
            "edges": {"justifies": ["Other Decision"]},
        },
    )
    assert r.status_code == 200
    assert r.json()["id"] == "A Rationale"

    r = await ui_client.get("/api/outline")
    edges = {(e["src"], e["dst"], e["type"]) for e in r.json()["edges"]}
    assert ("A Rationale", "Parent", "part_of") in edges
    assert ("A Rationale", "Other Decision", "justifies") in edges


async def test_create_node_title_collision_409(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Dup"})
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "Dup"})
    assert r.status_code == 409


async def test_create_node_bad_status_for_type_422(ui_client):
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "X", "status": "open"})
    assert r.status_code == 422


async def test_create_node_illegal_status_value_422(ui_client):
    r = await ui_client.post("/api/nodes", json={"type": "decision", "title": "X", "status": "bogus"})
    assert r.status_code == 422


async def test_create_node_empty_title_422(ui_client):
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "   "})
    assert r.status_code == 422


async def test_create_node_part_of_inside_edges_rejected_422(ui_client):
    """BUG FOUND AND FIXED (see CreateNodeRequest's own validator docstring):
    GraphStore.create_node raises a bare, unmapped ValueError if `part_of` is passed inside
    `edges` -- the request model now rejects this before ever touching the store."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent"})
    r = await ui_client.post(
        "/api/nodes", json={"type": "idea", "title": "X", "edges": {"part_of": ["Parent"]}}
    )
    assert r.status_code == 422


async def test_create_node_title_of_entirely_illegal_characters_422_not_500(ui_client):
    """The one residual gap CreateNodeRequest's validator can't close statelessly (a title
    that is non-empty/non-whitespace but sanitizes to "" anyway) is still caught -- by the
    router's own narrow ValueError safety net -- as a clean 422, not an unhandled 500."""
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "////"})
    assert r.status_code == 422


async def test_update_node_body_append_and_status(ui_client):
    await ui_client.post("/api/nodes", json={"type": "question", "title": "Q", "body": "first", "status": "open"})
    r = await ui_client.patch("/api/nodes/Q", json={"body": "second", "status": "resolved"})
    assert r.status_code == 200
    body = r.json()
    assert body["body"] == "first\n\nsecond"
    assert body["status"] == "resolved"


async def test_update_node_missing_404(ui_client):
    r = await ui_client.patch("/api/nodes/Nope", json={"body": "x"})
    assert r.status_code == 404


async def test_update_node_bad_status_for_type_422_not_500(ui_client):
    """BUG FOUND AND FIXED: GraphStore.update_node's own bare ValueError (schema.
    validate_status) for a status illegal on the node's actual type isn't among ui/errors.py's
    six mapped types -- update_node's router handler catches it narrowly, so this 422s
    cleanly instead of surfacing as an unhandled 500."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "X"})
    r = await ui_client.patch("/api/nodes/X", json={"status": "open"})
    assert r.status_code == 422


async def test_update_node_remove_properties_deletes_a_key(ui_client):
    """BUG FIXED: PATCH's `remove_properties` is the only way to actually delete a properties
    key -- `properties` alone only merges (dict.update), so a key omitted from it is left
    untouched, not removed."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Y", "properties": {"a": 1, "b": 2}})
    r = await ui_client.patch("/api/nodes/Y", json={"remove_properties": ["a"]})
    assert r.status_code == 200
    assert r.json()["properties"] == {"b": 2}


async def test_update_node_properties_merge_still_works_unchanged(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Z", "properties": {"a": 1}})
    r = await ui_client.patch("/api/nodes/Z", json={"properties": {"b": 2}})
    assert r.status_code == 200
    assert r.json()["properties"] == {"a": 1, "b": 2}


async def test_delete_node_reports_orphaned_inbound_edges(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.post("/api/edges", json={"src": "Child", "dst": "Root", "type": "part_of"})

    r = await ui_client.delete("/api/nodes/Root")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["existed"] is True
    assert body["orphaned_inbound_edges"] == [{"src": "Child", "dst": "Root", "type": "part_of", "direction": "in"}]

    r = await ui_client.get("/api/outline")
    assert "Root" not in {n["id"] for n in r.json()["nodes"]}


async def test_delete_node_missing_404(ui_client):
    r = await ui_client.delete("/api/nodes/Nope")
    assert r.status_code == 404


async def test_rename_node_relinks_referrers_and_reflects_in_outline(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Old Name"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Referrer"})
    await ui_client.post("/api/edges", json={"src": "Referrer", "dst": "Old Name", "type": "part_of"})

    r = await ui_client.post("/api/nodes/Old Name/rename", json={"new_title": "New Name"})
    assert r.status_code == 200
    body = r.json()
    assert body["old_id"] == "Old Name"
    assert body["new_id"] == "New Name"
    assert body["relinked"] == [{"src": "Referrer", "dst": "New Name", "type": "part_of", "direction": "out"}]

    r = await ui_client.get("/api/outline")
    ids = {n["id"] for n in r.json()["nodes"]}
    assert "New Name" in ids
    assert "Old Name" not in ids
    edges = {(e["src"], e["dst"], e["type"]) for e in r.json()["edges"]}
    assert ("Referrer", "New Name", "part_of") in edges
    assert ("Referrer", "Old Name", "part_of") not in edges


async def test_rename_node_missing_404(ui_client):
    r = await ui_client.post("/api/nodes/Nope/rename", json={"new_title": "Whatever"})
    assert r.status_code == 404


async def test_rename_node_title_of_entirely_illegal_characters_422_not_500(ui_client):
    """BUG FOUND AND FIXED: GraphStore.rename_node's own bare ValueError (ids.
    sanitize_title_to_id, for a title that sanitizes to "") isn't among ui/errors.py's six
    mapped types -- UIAppState.mutate's blanket ValueError->422 safety net covers it, so this
    422s cleanly instead of surfacing as an unhandled 500 (originally found via rename to a
    whitespace-only title; this exercises the same code path)."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Original"})
    r = await ui_client.post("/api/nodes/Original/rename", json={"new_title": "   "})
    assert r.status_code == 422


async def test_merge_nodes_surfaces_part_of_conflict_warning(ui_client):
    """s6-ui.md §12.1: "merge (warnings surfaced)" -- amendment A2's one non-fatal merge
    condition, a part_of adoption conflict."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent A"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent B"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Keep"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Drop", "body": "unique text"})
    await ui_client.post("/api/edges", json={"src": "Keep", "dst": "Parent A", "type": "part_of"})
    await ui_client.post("/api/edges", json={"src": "Drop", "dst": "Parent B", "type": "part_of"})

    r = await ui_client.post("/api/nodes/Keep/merge", json={"drop_id": "Drop"})
    assert r.status_code == 200
    body = r.json()
    assert body["kept"]["id"] == "Keep"
    assert body["dropped_id"] == "Drop"
    assert body["dropped_body_preview"] == "unique text"
    assert len(body["warnings"]) == 1
    assert "Parent B" in body["warnings"][0] and "Parent A" in body["warnings"][0]

    r = await ui_client.get("/api/outline")
    ids = {n["id"] for n in r.json()["nodes"]}
    assert "Drop" not in ids
    assert "Keep" in ids


async def test_merge_nodes_keep_equals_drop_400(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Solo"})
    r = await ui_client.post("/api/nodes/Solo/merge", json={"drop_id": "Solo"})
    assert r.status_code == 400


async def test_merge_nodes_missing_404(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Keep"})
    r = await ui_client.post("/api/nodes/Keep/merge", json={"drop_id": "Nope"})
    assert r.status_code == 404
