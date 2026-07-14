"""CSRF hardening (MAJOR/CRITICAL pre-launch finding): the UI has no auth of its own, so
without an Origin check a cross-origin page's script could POST/GET against it. Verified here
purely as a UNIT assertion on the app.py `_same_origin_guard` middleware's behavior via the
existing ASGITransport TestClient pattern (tests/conftest.py's ui_client) -- no live attack
request is fired at a real running server, and no cross-origin browser navigation happens.
"""
from __future__ import annotations

from redraft.ui.app import _is_allowed_origin


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
