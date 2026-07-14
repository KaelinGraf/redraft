"""spa_fallback (redraft.ui.app) BUG FOUND AND FIXED: a root-level static file (favicon.svg,
copied verbatim into static/ by vite's public/ dir) used to fall straight through to the
SPA-shell branch and come back as index.html/text-html -- one test per branch of the fix: a
real root-level static file is served as itself, and a genuine unknown client route still gets
the SPA shell (unchanged prior behavior). static/ is committed (never gitignored, see
.gitignore), so both files are always present -- no build-not-run skip guard needed."""
from __future__ import annotations

from redraft.ui.app import STATIC_DIR


async def test_root_level_static_file_served_as_itself(ui_client):
    r = await ui_client.get("/favicon.svg")
    assert r.status_code == 200
    assert "svg" in r.headers["content-type"]
    assert r.content == (STATIC_DIR / "favicon.svg").read_bytes()


async def test_unknown_client_route_still_gets_spa_shell(ui_client):
    r = await ui_client.get("/some/client/side/route")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert r.content == (STATIC_DIR / "index.html").read_bytes()


async def test_head_root_is_not_405(ui_client):
    # BUG FOUND AND FIXED: the SPA fallback was registered @app.get-only, so a HEAD request
    # (health checks, link unfurlers) got a 405 on every path, including "/".
    r = await ui_client.head("/")
    assert r.status_code == 200


async def test_head_api_route_stays_405_not_swallowed_by_spa_fallback(ui_client):
    # BUG FOUND AND FIXED (introduced by the HEAD fix above, caught before it shipped): Starlette
    # calls the FIRST route that FULLY matches a request, and this wildcard's `:path` converter
    # fully matches every URL -- so declaring HEAD on it made it win the routing race against
    # every real /api/* GET-only route on a HEAD request, silently returning the SPA shell (200,
    # text/html) instead of the 405 those endpoints should give. Live-reproduced against a real
    # server before this guard existed: HEAD /api/status came back 200 with the SPA shell body.
    r = await ui_client.head("/api/status")
    assert r.status_code == 405
    r = await ui_client.get("/api/status")  # GET on the same route is untouched
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
