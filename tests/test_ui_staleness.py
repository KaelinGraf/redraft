"""s6-ui.md §12.1/§4: cross-process index staleness. A hand-edited/externally-written
graph/nodes/*.md file is picked up either via the explicit POST /api/reindex button, or --
with a short enough poll interval -- by the background poll task on its own, with no
explicit call at all."""
from __future__ import annotations

import asyncio

import httpx

from redraft.retrieval.embeddings import RetrievalConfig


def _write_external_node(graph_dir, title: str) -> None:
    """Simulates a hand-edit / a second process's own GraphStore write, bypassing the UI's
    own API entirely -- exactly the failure-mode-2 scenario (s6-ui.md §4.1)."""
    (graph_dir / "graph" / "nodes" / f"{title}.md").write_text(
        f"---\ntype: concept\ntitle: {title}\n"
        "created: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\n\nexternally written\n"
    )


async def test_explicit_reindex_picks_up_external_write_and_bumps_generation(ui_client, graph_dir):
    gen_before = (await ui_client.get("/api/status")).json()["generation"]
    outline_before = (await ui_client.get("/api/outline")).json()
    assert outline_before["nodes"] == []

    _write_external_node(graph_dir, "External Node")

    r = await ui_client.post("/api/reindex")
    assert r.status_code == 200
    assert r.json()["scanned"] == 1
    assert r.json()["upserted"] == 1

    outline_after = (await ui_client.get("/api/outline")).json()
    assert {n["id"] for n in outline_after["nodes"]} == {"External Node"}

    status_after = (await ui_client.get("/api/status")).json()
    assert status_after["generation"] > gen_before
    assert status_after["last_reindex_at"] is not None


async def test_background_poll_picks_up_external_write_without_an_explicit_reindex_call(graph_dir):
    """Constructs its OWN app with a short real reindex_poll_interval (s6-ui.md §12.1) --
    NOT the ui_app/ui_client fixtures above, which hardcode reindex_poll_interval=0
    specifically to keep every OTHER UI test deterministic."""
    from redraft.ui.app import create_app

    app = create_app(graph_dir, RetrievalConfig(), reindex_poll_interval=0.1)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            gen_before = (await client.get("/api/status")).json()["generation"]

            _write_external_node(graph_dir, "Polled Node")

            # Bounded wait: up to 10s (100 * 0.1s poll interval) -- generous headroom under a
            # loaded full-suite run (observed flaky at a tighter 3s bound: the poll task still
            # ticks correctly, but a busy event loop/worker thread during a large parallel
            # test run can occasionally push its first tick past a tight bound). The loop
            # exits as soon as the condition is met, typically within 1-2 ticks, so this only
            # costs time in the failure case.
            for _ in range(100):
                outline = (await client.get("/api/outline")).json()
                if any(n["id"] == "Polled Node" for n in outline["nodes"]):
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("background poll never picked up the external write")

            # BUG FOUND AND FIXED (test-only, no production change): unlike the explicit
            # POST /api/reindex sibling test above -- where state.mutate()'s generation += 1
            # runs INLINE in the same request/response cycle the test itself awaits, so it is
            # strictly ordered before that request even returns -- the background poll's own
            # mutate() call runs inside an independent asyncio.create_task, decoupled from any
            # HTTP request. Its DB commit (which the outline read above can already observe,
            # via a brand-new sqlite connection, s6-ui.md §4.1) and its `generation += 1`
            # continuation (a separately scheduled event-loop callback, s6-ui.md §4.2) are two
            # different signals that only become eventually consistent with EACH OTHER, not
            # atomically joined -- exactly the "the consumer only ever asks 'did this change,'
            # never relies on an exact value" contract UIAppState.generation's own docstring
            # already documents. A single-shot assert immediately after the outline loop
            # breaks assumes a stricter ordering than that contract provides, and flaked under
            # full-suite scheduling contention (reproduced: outline confirmed the write, this
            # assertion still failed). Poll for it too, same bounded budget, rather than
            # asserting it in one shot.
            for _ in range(100):
                status_after = (await client.get("/api/status")).json()
                if status_after["generation"] > gen_before:
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("generation counter never advanced after the background poll picked up the write")
            assert status_after["last_reindex_at"] is not None
    finally:
        app.state.ui.worker.shutdown()
