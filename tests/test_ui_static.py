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
