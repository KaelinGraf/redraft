"""s6-ui.md §12.1: listing, reading, and the path-traversal guard on GET
/api/reports/{filename}. Reports are written directly under reports/ (the same convention
the organizing protocol's own §7 describes -- an agent saves a writeup there before its next
snapshot); there is no "create a report" endpoint, so these tests write files directly.

Also covers GET /api/doc/{root_id} (assemble_report) -- s6-ui.md §9's own endpoint table
lists it in this same router file (reports.py), but neither §12.1's test-file table nor any
other test_ui_*.py file actually names a home for it; housed here as the natural fit rather
than left untested."""
from __future__ import annotations

from pathlib import Path

import pytest

from redraft.errors import NotFoundError
from redraft.ui.queries import read_report


async def test_reports_empty_before_any_report_exists(ui_client):
    r = await ui_client.get("/api/reports")
    assert r.status_code == 200
    assert r.json() == []


async def test_reports_listing_shape(ui_client, graph_dir):
    reports_dir = graph_dir / "reports"
    reports_dir.mkdir()
    (reports_dir / "2026-01-01-review.md").write_text("# Review\n\nBody text.\n")
    (reports_dir / "not-markdown.txt").write_text("ignored")

    r = await ui_client.get("/api/reports")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["filename"] == "2026-01-01-review.md"
    assert body[0]["size"] == len("# Review\n\nBody text.\n")
    assert body[0]["modified_at"]


async def test_read_report_returns_content(ui_client, graph_dir):
    reports_dir = graph_dir / "reports"
    reports_dir.mkdir()
    (reports_dir / "notes.md").write_text("# Notes\n\nHello.\n")

    r = await ui_client.get("/api/reports/notes.md")
    assert r.status_code == 200
    assert r.json() == {"filename": "notes.md", "content": "# Notes\n\nHello.\n"}


async def test_read_report_missing_file_404(ui_client, graph_dir):
    (graph_dir / "reports").mkdir()
    r = await ui_client.get("/api/reports/nope.md")
    assert r.status_code == 404


async def test_read_report_before_reports_dir_exists_404(ui_client):
    r = await ui_client.get("/api/reports/nope.md")
    assert r.status_code == 404


async def test_read_report_path_traversal_rejected_404(ui_client, graph_dir):
    """End-to-end: a request path that Starlette's own routing/URL-normalization lets through
    unchanged (verified -- most encoded-slash/dot-dot variants get normalized to a different
    route by Starlette itself, before ever reaching read_report) must still not leak content
    from outside reports/ if any of them DO arrive intact.

    BUG FOUND AND FIXED (test-only -- no production code changed): empirically, of the three
    cases below, only "%2e%2e" actually reaches read_report's own guard. "..": httpx's own
    client-side URL constructor collapses "/api/reports/.." to "/api" before the request is
    even sent (RFC 3986 dot-segment removal on a literal ".." segment); "..%2fsecret.md":
    the decoded ".." segment is likewise collapsed during ASGI-level routing before it can
    match /api/reports/{filename}. Neither ever reaches read_report -- both fall through to
    app.py's SPA catch-all instead, which correctly (and separately from this guard) returns
    200 + the built index.html for any unmatched path once the frontend is built (needed for
    client-side deep-links, s6-ui.md §7.1) -- verified live, see this project's S6 frontend
    chunk. The "always 404" assertion this test originally made was therefore only ever
    incidentally true pre-frontend (nothing was mounted at "/", so EVERY unmatched path
    404'd, for reasons unrelated to read_report), not a property of read_report itself --
    once a real SPA shell exists to serve, two of the three cases legitimately 200. The
    invariant this test's own docstring actually documents -- content never leaks -- is
    unconditional below; the 404 check is scoped to responses that reached our own JSON API
    (not the unrelated SPA shell), which is the part read_report actually owns."""
    reports_dir = graph_dir / "reports"
    reports_dir.mkdir()
    secret = graph_dir / "secret.md"
    secret.write_text("top secret")

    for path in ("/api/reports/..", "/api/reports/..%2fsecret.md", "/api/reports/%2e%2e"):
        r = await ui_client.get(path)
        assert "top secret" not in r.text
        if r.headers.get("content-type", "").startswith("application/json"):
            assert r.status_code == 404  # reached our own API (not the SPA shell) -> must error


def test_read_report_function_rejects_bare_dotdot_directly(tmp_path: Path):
    """Direct unit test of queries.read_report's guard, independent of whatever URL
    normalization an HTTP client/server layer might also apply -- proves the FUNCTION itself
    rejects a literal '..' input."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    with pytest.raises(NotFoundError):
        read_report(reports_dir, "..")


def test_read_report_function_rejects_symlink_escaping_reports_dir(tmp_path: Path):
    """The is_relative_to() re-check on the RESOLVED path is the genuinely load-bearing half
    of the guard for this case (not PurePosixPath(...).name, which does nothing for a
    perfectly normal-looking symlink filename with no dots or slashes in it at all): a
    symlink planted inside reports/ (e.g. by a malicious git merge) that points OUTSIDE it
    must still be refused, even though a bare .is_file() check alone would happily follow it
    and return True for the escaped target."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("should never be reachable via reports/")
    (reports_dir / "escape").symlink_to(secret)

    with pytest.raises(NotFoundError):
        read_report(reports_dir, "escape")


# -- GET /api/doc/{root_id} (assemble_report) --------------------------------------------


async def test_doc_returns_sections_and_decision_tables_for_the_same_root(ui_client):
    """One query backs both the Doc tab (.sections) and the Tables tab (.decision_tables) --
    s6-ui.md §10.1."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    await ui_client.post(
        "/api/nodes", json={"type": "question", "title": "Which approach", "status": "resolved"}
    )
    await ui_client.post(
        "/api/edges", json={"src": "Which approach", "dst": "Root", "type": "part_of"}
    )
    await ui_client.post("/api/nodes", json={"type": "decision", "title": "Chosen", "status": "accepted"})
    await ui_client.post("/api/edges", json={"src": "Chosen", "dst": "Root", "type": "part_of"})
    await ui_client.post(
        "/api/edges", json={"src": "Chosen", "dst": "Which approach", "type": "addresses"}
    )

    r = await ui_client.get("/api/doc/Root")
    assert r.status_code == 200
    body = r.json()
    assert body["root_id"] == "Root"
    assert body["sections"][0]["node"]["id"] == "Root"
    section_ids = {s["node"]["id"] for s in body["sections"][0]["children"]}
    assert section_ids == {"Which approach", "Chosen"}
    group = next(g for g in body["decision_tables"] if g["driver"]["id"] == "Which approach")
    assert [r["decision"]["id"] for r in group["rows"]] == ["Chosen"]


async def test_doc_respects_depth_and_include_edge_types_params(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    await ui_client.post("/api/nodes", json={"type": "idea", "title": "Child"})
    await ui_client.post("/api/edges", json={"src": "Child", "dst": "Root", "type": "part_of"})

    r = await ui_client.get("/api/doc/Root", params={"depth": 0})
    assert r.json()["sections"][0]["children"] == []

    r = await ui_client.get("/api/doc/Root", params={"depth": 4, "include_edge_types": ["justifies"]})
    assert r.status_code == 200  # a filter that matches nothing is not an error


async def test_doc_negative_depth_422(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Root"})
    r = await ui_client.get("/api/doc/Root", params={"depth": -1})
    assert r.status_code == 422


async def test_doc_missing_root_404(ui_client):
    r = await ui_client.get("/api/doc/Nonexistent")
    assert r.status_code == 404
