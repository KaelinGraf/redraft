"""s6-ui.md §12.1: the Lane-A gate. Fires N concurrent POST /api/nodes calls via
asyncio.gather; asserts (a) no sqlite3 thread-affinity exception surfaces anywhere (would
show up as a 500 with that exact message), (b) all N nodes actually exist afterward via a
follow-up GET /api/outline (no lost writes under concurrency -- correctness of the end
state is what actually matters, not an internal lock assertion)."""
from __future__ import annotations

import asyncio


async def test_concurrent_node_creates_all_land_with_no_thread_affinity_error(ui_client):
    n = 20
    responses = await asyncio.gather(
        *[ui_client.post("/api/nodes", json={"type": "concept", "title": f"Concurrent {i}"}) for i in range(n)]
    )

    for r in responses:
        assert r.status_code == 200, r.text
        assert "SQLite objects created in a thread" not in r.text

    outline = await ui_client.get("/api/outline")
    ids = {node["id"] for node in outline.json()["nodes"]}
    assert ids == {f"Concurrent {i}" for i in range(n)}


async def test_concurrent_mixed_reads_and_writes_never_race(ui_client):
    """Lane B (outline/status polling) firing concurrently with Lane A (node creation) must
    never itself raise -- the two lanes use different connections/executors entirely."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Seed"})

    async def writer(i: int):
        return await ui_client.post("/api/nodes", json={"type": "idea", "title": f"Mixed {i}"})

    async def reader():
        return await ui_client.get("/api/outline")

    tasks = [writer(i) for i in range(10)] + [reader() for _ in range(10)] + [ui_client.get("/api/status") for _ in range(5)]
    responses = await asyncio.gather(*tasks)
    assert all(r.status_code == 200 for r in responses)

    outline = await ui_client.get("/api/outline")
    ids = {node["id"] for node in outline.json()["nodes"]}
    assert ids == {"Seed"} | {f"Mixed {i}" for i in range(10)}
