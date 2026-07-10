"""s6-ui.md §12.1: create/delete edge, reparent (PUT .../parent) happy path AND CycleError
-> 409 mapping. Also exercises ruling §14.6's closed partial-state window: a reparent onto a
missing parent must 404 AND leave the original parent intact."""
from __future__ import annotations


async def _outline_edges(client):
    r = await client.get("/api/outline")
    return {(e["src"], e["dst"], e["type"]) for e in r.json()["edges"]}


async def test_create_edge_happy_path_and_warning_surfaced(ui_client):
    await ui_client.post("/api/nodes", json={"type": "decision", "title": "D", "status": "accepted"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "C"})  # wrong src type for justifies
    r = await ui_client.post("/api/edges", json={"src": "C", "dst": "D", "type": "justifies"})
    assert r.status_code == 200
    body = r.json()
    assert body["src"] == "C" and body["dst"] == "D" and body["type"] == "justifies"
    assert body["warnings"] != []  # graphrules convention warning: justifies wants a rationale src


async def test_create_edge_missing_dst_404(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "A"})
    r = await ui_client.post("/api/edges", json={"src": "A", "dst": "Nope", "type": "relates_to"})
    assert r.status_code == 404


async def test_create_edge_second_part_of_parent_without_delete_is_collision_409(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "P1"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "P2"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "X"})
    await ui_client.post("/api/edges", json={"src": "X", "dst": "P1", "type": "part_of"})
    r = await ui_client.post("/api/edges", json={"src": "X", "dst": "P2", "type": "part_of"})
    assert r.status_code == 409


async def test_create_edges_batch_happy_path_two_edges_at_once(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Batch Parent"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Batch Ref"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Batch Child"})

    r = await ui_client.post(
        "/api/edges/batch",
        json={
            "edges": [
                {"src": "Batch Child", "dst": "Batch Parent", "type": "part_of"},
                {"src": "Batch Child", "dst": "Batch Ref", "type": "references"},
            ]
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert [(e["src"], e["dst"], e["type"]) for e in body] == [
        ("Batch Child", "Batch Parent", "part_of"),
        ("Batch Child", "Batch Ref", "references"),
    ]
    edges = await _outline_edges(ui_client)
    assert ("Batch Child", "Batch Parent", "part_of") in edges
    assert ("Batch Child", "Batch Ref", "references") in edges


async def test_create_edges_batch_generation_bumped_once_not_per_edge(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Gen Parent"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Gen Ref"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Gen Child"})

    before = (await ui_client.get("/api/status")).json()["generation"]
    r = await ui_client.post(
        "/api/edges/batch",
        json={
            "edges": [
                {"src": "Gen Child", "dst": "Gen Parent", "type": "part_of"},
                {"src": "Gen Child", "dst": "Gen Ref", "type": "references"},
            ]
        },
    )
    assert r.status_code == 200
    after = (await ui_client.get("/api/status")).json()["generation"]
    assert after == before + 1  # ONE bump for the whole batch, not one per edge


async def test_create_edges_batch_atomic_failure_applies_neither_edge(ui_client):
    """The atomicity proof: one edge in the batch names a missing dst -> the whole call 4xx's,
    and a follow-up GET must show NEITHER edge of that failed batch applied -- including the
    other edge, which was individually valid."""
    await ui_client.post("/api/nodes", json={"type": "observation", "title": "Atomic A"})
    await ui_client.post("/api/nodes", json={"type": "artifact", "title": "Atomic B"})

    r = await ui_client.post(
        "/api/edges/batch",
        json={
            "edges": [
                {"src": "Atomic A", "dst": "Atomic B", "type": "references"},  # individually valid
                {"src": "Atomic A", "dst": "Does Not Exist", "type": "relates_to"},  # missing dst
            ]
        },
    )
    assert r.status_code == 404

    edges = await _outline_edges(ui_client)
    assert ("Atomic A", "Atomic B", "references") not in edges  # the valid edge was NOT applied either
    assert edges == set()


async def test_delete_edge_reports_existed_true_then_false(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "A"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "B"})
    await ui_client.post("/api/edges", json={"src": "A", "dst": "B", "type": "relates_to"})

    r = await ui_client.request("DELETE", "/api/edges", json={"src": "A", "dst": "B", "type": "relates_to"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "existed": True, "orphaned_inbound_edges": []}
    assert ("A", "B", "relates_to") not in await _outline_edges(ui_client)

    r = await ui_client.request("DELETE", "/api/edges", json={"src": "A", "dst": "B", "type": "relates_to"})
    assert r.json() == {"ok": True, "existed": False, "orphaned_inbound_edges": []}


async def test_reparent_happy_path_replaces_old_parent(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent A"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent B"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})

    r = await ui_client.put("/api/nodes/Child/parent", json={"new_parent": "Parent A"})
    assert r.status_code == 200
    assert ("Child", "Parent A", "part_of") in await _outline_edges(ui_client)

    r = await ui_client.put("/api/nodes/Child/parent", json={"new_parent": "Parent B"})
    assert r.status_code == 200
    edges = await _outline_edges(ui_client)
    assert ("Child", "Parent B", "part_of") in edges
    assert ("Child", "Parent A", "part_of") not in edges  # old edge is gone, not just supplemented


async def test_reparent_to_none_unparents(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.put("/api/nodes/Child/parent", json={"new_parent": "Parent"})

    r = await ui_client.put("/api/nodes/Child/parent", json={"new_parent": None})
    assert r.status_code == 200
    assert await _outline_edges(ui_client) == set()


async def test_reparent_to_missing_parent_404_and_original_parent_intact(ui_client):
    """The load-bearing proof of orchestrator ruling §14.6: the flagged partial-state window
    is CLOSED. A reparent onto a nonexistent target must 404 (not 500/other), and the node's
    ORIGINAL part_of edge must still be exactly as it was -- never deleted, never left
    un-parented."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Real Parent"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.put("/api/nodes/Child/parent", json={"new_parent": "Real Parent"})

    r = await ui_client.put("/api/nodes/Child/parent", json={"new_parent": "Does Not Exist"})
    assert r.status_code == 404

    edges = await _outline_edges(ui_client)
    assert ("Child", "Real Parent", "part_of") in edges
    assert len(edges) == 1  # nothing else was touched -- no un-parented, no stray second edge

    r = await ui_client.get("/api/nodes/Child", params={"neighbor_depth": 1})
    assert r.json()["edges"] == [{"src": "Child", "dst": "Real Parent", "type": "part_of", "direction": "out"}]


async def test_reparent_cycle_rejected_409_and_original_state_intact(ui_client):
    """Cycle rejection closed via the §14.6(a2) pre-check: reparenting a node's own ancestor
    underneath it must 409, with NEITHER side's part_of edge touched."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Grandparent"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.put("/api/nodes/Parent/parent", json={"new_parent": "Grandparent"})
    await ui_client.put("/api/nodes/Child/parent", json={"new_parent": "Parent"})

    before = await _outline_edges(ui_client)
    r = await ui_client.put("/api/nodes/Grandparent/parent", json={"new_parent": "Child"})
    assert r.status_code == 409
    assert await _outline_edges(ui_client) == before  # nothing changed at all


async def test_reparent_missing_node_itself_404(ui_client):
    r = await ui_client.put("/api/nodes/Nope/parent", json={"new_parent": None})
    assert r.status_code == 404
