"""s6-ui.md §12.1: GET /api/dedup-hints degraded-mode contract. When the embedder isn't warm
yet, the endpoint must return degraded=True, matched_vector=False for every hit, and must
never touch the embedding model at all -- not just "not use its result".

Also covers GET /api/search (hybrid_search.search_nodes) -- s6-ui.md §9's own endpoint table
lists it in this same router file (search.py), but neither §12.1's test-file table nor any
other test_ui_*.py file names a home for it; housed here as the natural fit rather than left
untested."""
from __future__ import annotations

import asyncio


async def _wait_until_embedder_warm(ui_app, ui_client) -> None:
    """Node creation ALWAYS embeds (GraphStore.create_node calls embed_upsert unconditionally
    whenever retrieval_config is set, independent of the UI's own embedder_ready bookkeeping
    flag), and the app's one-shot warmup task is fire-and-forget -- started by the lazy-start
    middleware on the very first request, with no guarantee it has finished by the time that
    request's own response comes back. Waiting for it to genuinely finish BEFORE monkeypatching
    embedder_ready=False is required: the middleware only ever starts that task ONCE per app
    (tasks_started), so once it has completed, nothing else will race a later monkeypatch and
    silently flip the flag back to True underneath a test."""
    for _ in range(50):
        if ui_app.state.ui.embedder_ready:
            return
        await asyncio.sleep(0.1)
    raise AssertionError("embedder never warmed within the bounded wait")


async def test_dedup_hints_degraded_mode_never_touches_embedder(ui_app, ui_client, monkeypatch):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Widget Registration Backbone"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Unrelated Thing"})
    await _wait_until_embedder_warm(ui_app, ui_client)

    monkeypatch.setattr(ui_app.state.ui, "embedder_ready", False)

    def _boom(*args, **kwargs):
        raise AssertionError("get_embedder must not be called on the degraded (FTS-only) path")

    monkeypatch.setattr("redraft.retrieval.embeddings.get_embedder", _boom)

    r = await ui_client.get("/api/dedup-hints", params={"title": "Widget Registration"})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert body["hits"] != []
    assert all(h["matched_vector"] is False for h in body["hits"])
    assert all(h["matched_fts"] is True for h in body["hits"])


async def test_dedup_hints_degraded_mode_score_is_rank_order_not_bm25(ui_app, ui_client, monkeypatch):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Zeta Zeta Zeta"})
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Zeta Alone"})
    await _wait_until_embedder_warm(ui_app, ui_client)

    monkeypatch.setattr(ui_app.state.ui, "embedder_ready", False)

    r = await ui_client.get("/api/dedup-hints", params={"title": "Zeta", "k": 5})
    body = r.json()
    assert body["degraded"] is True
    scores = [h["score"] for h in body["hits"]]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 1.0  # 1/(1+0) for the top rank position -- a synthetic rank value,
    # not a calibrated bm25 number (fts_candidates only returns an ordered id list)


async def test_dedup_hints_warm_mode_uses_hybrid_search(ui_app, ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Alpha Beta Gamma"})
    await _wait_until_embedder_warm(ui_app, ui_client)

    r = await ui_client.get("/api/dedup-hints", params={"title": "Alpha Beta Gamma"})
    assert r.status_code == 200
    assert r.json()["degraded"] is False


# -- GET /api/search (hybrid_search.search_nodes) ----------------------------------------


async def test_search_finds_matching_node_by_text(ui_client):
    await ui_client.post(
        "/api/nodes", json={"type": "concept", "title": "Registration backbone", "body": "GeoTransformer fork"}
    )
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Totally unrelated topic"})

    r = await ui_client.get("/api/search", params={"q": "GeoTransformer"})
    assert r.status_code == 200
    hits = r.json()
    assert any(h["node"]["id"] == "Registration backbone" for h in hits)


async def test_search_filters_by_type_and_status(ui_client):
    await ui_client.post(
        "/api/nodes", json={"type": "decision", "title": "Shared Keyword Decision", "status": "accepted"}
    )
    await ui_client.post(
        "/api/nodes", json={"type": "concept", "title": "Shared Keyword Concept"}
    )

    r = await ui_client.get("/api/search", params={"q": "Shared Keyword", "types": ["decision"]})
    ids = {h["node"]["id"] for h in r.json()}
    assert ids == {"Shared Keyword Decision"}

    r = await ui_client.get(
        "/api/search", params={"q": "Shared Keyword", "types": ["decision"], "status": "rejected"}
    )
    assert r.json() == []  # status filter excludes the accepted one


async def test_search_empty_query_returns_empty(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Something"})
    r = await ui_client.get("/api/search", params={"q": ""})
    assert r.status_code == 200
    assert r.json() == []
