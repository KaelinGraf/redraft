"""s6-ui.md §12.1: outline/schema/node-detail/neighbors/attention shape and existence-check
(404 mapping) correctness."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from redraft.models import STATUS_BY_TYPE
from redraft.retrieval import integrity
from redraft.schema import EdgeType, NodeType
from redraft.tools.read_tools import index_read_conn
from redraft.ui.queries import _STALE_AFTER_DAYS


async def test_schema_matches_the_closed_vocab(ui_client):
    r = await ui_client.get("/api/schema")
    assert r.status_code == 200
    body = r.json()
    assert set(body["node_types"]) == {t.value for t in NodeType}
    assert set(body["edge_types"]) == {t.value for t in EdgeType}
    assert body["status_by_type"]["decision"] == sorted(STATUS_BY_TYPE["decision"])
    assert body["status_by_type"]["concept"] is None


async def test_outline_reflects_created_nodes_and_edges(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.post("/api/edges", json={"src": "Child", "dst": "Root", "type": "part_of"})

    r = await ui_client.get("/api/outline")
    assert r.status_code == 200
    body = r.json()
    assert {n["id"] for n in body["nodes"]} == {"Root", "Child"}
    assert ("Child", "Root", "part_of") in {(e["src"], e["dst"], e["type"]) for e in body["edges"]}


async def test_node_detail_zero_depth_omits_edges_and_neighbors(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.post("/api/edges", json={"src": "Child", "dst": "Root", "type": "part_of"})

    r = await ui_client.get("/api/nodes/Child")
    assert r.status_code == 200
    body = r.json()
    assert body["node"]["id"] == "Child"
    assert body["neighbors"] == []
    assert body["edges"] == []


async def test_node_detail_positive_depth_includes_neighbors_and_edges(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.post("/api/edges", json={"src": "Child", "dst": "Root", "type": "part_of"})

    r = await ui_client.get("/api/nodes/Child", params={"neighbor_depth": 1})
    body = r.json()
    assert {n["id"] for n in body["neighbors"]} == {"Root"}
    assert [(e["src"], e["dst"], e["type"]) for e in body["edges"]] == [("Child", "Root", "part_of")]


async def test_node_detail_negative_depth_rejected_422(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    r = await ui_client.get("/api/nodes/Root", params={"neighbor_depth": -1})
    assert r.status_code == 422


async def test_node_detail_missing_node_404(ui_client):
    r = await ui_client.get("/api/nodes/Nonexistent")
    assert r.status_code == 404
    assert "Nonexistent" in r.json()["detail"]


async def test_neighbors_endpoint_shape_and_direction_filter(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.post("/api/edges", json={"src": "Child", "dst": "Root", "type": "part_of"})

    r = await ui_client.get("/api/nodes/Root/neighbors", params={"direction": "in"})
    assert r.status_code == 200
    assert r.json() == [{"src": "Child", "dst": "Root", "type": "part_of", "direction": "in"}]

    r = await ui_client.get("/api/nodes/Root/neighbors", params={"direction": "out"})
    assert r.json() == []

    r = await ui_client.get("/api/nodes/Root/neighbors", params={"direction": "in", "edge_types": ["justifies"]})
    assert r.json() == []  # filtered out: the real edge is part_of, not justifies


async def test_neighbors_missing_node_404(ui_client):
    r = await ui_client.get("/api/nodes/Nonexistent/neighbors")
    assert r.status_code == 404


async def test_attention_matches_the_four_underlying_integrity_queries(ui_client, graph_dir):
    """The aggregation endpoint must never drift from the four retrieval.integrity functions
    it composes -- exercised with at least one non-empty hit per signal, not just "both
    empty", so the wiring is actually proven, not just shape-compatible."""
    await ui_client.post("/api/nodes", json={"type": "question", "title": "Q1", "status": "open"})
    await ui_client.post("/api/nodes", json={"type": "decision", "title": "D1", "status": "accepted"})

    old = (datetime.now(timezone.utc) - timedelta(days=_STALE_AFTER_DAYS + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (graph_dir / "graph" / "nodes" / "Old Decision.md").write_text(
        f"---\ntype: decision\ntitle: Old Decision\nstatus: proposed\n"
        f"created: {old}\nupdated: {old}\n---\n\nstale body\n"
    )
    r = await ui_client.post("/api/reindex")
    assert r.status_code == 200

    r = await ui_client.get("/api/attention")
    assert r.status_code == 200
    body = r.json()

    before_iso = (datetime.now(timezone.utc) - timedelta(days=_STALE_AFTER_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with index_read_conn(graph_dir) as conn:
        expected_open = {n["id"] for n in integrity.open_questions(conn)}
        expected_unjustified = {n["id"] for n in integrity.decisions_without_rationale(conn)}
        expected_dangling = integrity.dangling_edges(conn)
        expected_stale = {n["id"] for n in integrity.stale(conn, before_iso)}

    assert {n["id"] for n in body["open_questions"]} == expected_open == {"Q1"}
    assert {n["id"] for n in body["unjustified_decisions"]} == expected_unjustified == {"D1", "Old Decision"}
    assert body["dangling_edges"] == expected_dangling == []
    assert {n["id"] for n in body["stale"]} == expected_stale == {"Old Decision"}
