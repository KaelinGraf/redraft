"""CSRF hardening (MAJOR/CRITICAL pre-launch finding): the UI has no auth of its own, so
without an Origin check a cross-origin page's script could POST/GET against it. Verified here
purely as a UNIT assertion on the app.py `_same_origin_guard` middleware's behavior via the
existing ASGITransport TestClient pattern (tests/conftest.py's ui_client) -- no live attack
request is fired at a real running server, and no cross-origin browser navigation happens.
"""
from __future__ import annotations

import httpx
import pytest

from redraft.ui.app import _is_allowed_host, _is_allowed_origin


def test_is_allowed_origin_accepts_any_loopback_host_and_port():
    assert _is_allowed_origin("http://127.0.0.1:8420")
    assert _is_allowed_origin("http://127.0.0.1:1")
    assert _is_allowed_origin("http://127.0.0.1")
    assert _is_allowed_origin("http://localhost:8420")
    assert _is_allowed_origin("http://LOCALHOST:8420")  # case-insensitive host
    assert _is_allowed_origin("http://[::1]:8420")
    assert _is_allowed_origin("https://127.0.0.1:8420")  # scheme not pinned to http


def test_is_allowed_origin_rejects_foreign_hosts():
    assert not _is_allowed_origin("http://evil.example")
    assert not _is_allowed_origin("http://evil.example:8420")
    assert not _is_allowed_origin("http://127.0.0.1.evil.example")  # loopback-prefix spoof
    assert not _is_allowed_origin("http://127.0.0.1evil.example")
    assert not _is_allowed_origin("null")  # browsers send this for some sandboxed contexts


async def test_foreign_origin_rejected_on_post_nodes(ui_client):
    r = await ui_client.post(
        "/api/nodes",
        json={"type": "concept", "title": "CSRF Probe"},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403


async def test_foreign_origin_rejected_on_post_reindex(ui_client):
    r = await ui_client.post("/api/reindex", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


async def test_loopback_origin_allowed_on_post_nodes(ui_client):
    r = await ui_client.post(
        "/api/nodes",
        json={"type": "concept", "title": "Loopback Origin OK"},
        headers={"Origin": "http://127.0.0.1:8420"},
    )
    assert r.status_code == 200


async def test_no_origin_header_allowed_on_post_nodes(ui_client):
    # curl and same-origin top-level navigations never send an Origin header at all.
    r = await ui_client.post("/api/nodes", json={"type": "concept", "title": "No Origin Header"})
    assert r.status_code == 200


async def test_foreign_origin_rejected_on_get_too(ui_client):
    # GET is read-only but the guard applies uniformly, by design, rather than only to
    # mutating verbs -- simplest and closes the reindex-CSRF finding for free.
    r = await ui_client.get("/api/status", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


async def test_loopback_origin_allowed_on_get(ui_client):
    r = await ui_client.get("/api/status", headers={"Origin": "http://127.0.0.1:8420"})
    assert r.status_code == 200


# -- Host header (DNS-rebinding residual) -----------------------------------------------------


def test_is_allowed_host_accepts_loopback_with_and_without_port():
    assert _is_allowed_host("127.0.0.1:8420")
    assert _is_allowed_host("127.0.0.1")
    assert _is_allowed_host("localhost:8420")
    assert _is_allowed_host("LOCALHOST:8420")
    assert _is_allowed_host("[::1]:8420")


def test_is_allowed_host_rejects_foreign_hosts():
    assert not _is_allowed_host("evil.example")
    assert not _is_allowed_host("evil.example:8420")
    assert not _is_allowed_host("127.0.0.1.evil.example")


async def test_foreign_host_rejected_even_with_no_origin_header(ui_client):
    # The exact DNS-rebinding residual: no Origin header at all (every non-browser client, or
    # a rebound page's request that omits it) previously sailed through unaffected as long as
    # Origin was absent -- Host is the only signal left to catch it.
    r = await ui_client.get("/api/status", headers={"Host": "evil.example"})
    assert r.status_code == 403


async def test_foreign_host_rejected_on_post_nodes(ui_client):
    r = await ui_client.post(
        "/api/nodes", json={"type": "concept", "title": "Host Probe"}, headers={"Host": "evil.example"}
    )
    assert r.status_code == 403


async def test_loopback_host_with_port_allowed(ui_client):
    r = await ui_client.get("/api/status", headers={"Host": "127.0.0.1:8420"})
    assert r.status_code == 200


async def test_loopback_host_localhost_allowed(ui_client):
    r = await ui_client.get("/api/status", headers={"Host": "localhost"})
    assert r.status_code == 200


# -- strict_loopback=False (operator bound a non-loopback --host; FIX 5) -----------------------


@pytest.fixture
async def lax_client(graph_dir):
    """create_app(strict_loopback=False) -- what main() builds for a non-loopback bind. The
    default-strict ui_client fixture is untouched; every test above still runs against it."""
    from redraft.retrieval.embeddings import RetrievalConfig
    from redraft.ui.app import create_app

    app = create_app(graph_dir, RetrievalConfig(), reindex_poll_interval=0, strict_loopback=False)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            yield client
    finally:
        app.state.ui.worker.shutdown()


async def test_lax_mode_allows_lan_host_with_no_origin(lax_client):
    r = await lax_client.get("/api/status", headers={"Host": "192.168.1.5:8420"})
    assert r.status_code == 200


async def test_lax_mode_allows_self_origin_matching_host(lax_client):
    r = await lax_client.get(
        "/api/status", headers={"Host": "192.168.1.5:8420", "Origin": "http://192.168.1.5:8420"}
    )
    assert r.status_code == 200


async def test_lax_mode_still_rejects_foreign_origin(lax_client):
    # Classic cross-site CSRF stays closed even in lax mode: a foreign page's Origin names ITS
    # authority, which can never equal this request's Host.
    r = await lax_client.get(
        "/api/status", headers={"Host": "192.168.1.5:8420", "Origin": "http://evil.example"}
    )
    assert r.status_code == 403


async def test_lax_mode_still_allows_loopback_origin(lax_client):
    r = await lax_client.get("/api/status", headers={"Origin": "http://127.0.0.1:5173"})
    assert r.status_code == 200


async def test_strict_default_unchanged_without_kwarg(ui_client):
    # ui_client's app is built by conftest's ui_app fixture calling create_app WITHOUT
    # strict_loopback -- the default must stay strict (regression pin for FIX 5's kwarg).
    r = await ui_client.get("/api/status", headers={"Host": "evil.example"})
    assert r.status_code == 403
