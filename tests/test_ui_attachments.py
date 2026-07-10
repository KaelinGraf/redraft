"""s6-ui.md §12.1/§6: successful upload creates both the file and the artifact node with the
exact attachment_* properties; oversized upload -> 413 and no file left on disk; filename
collision -> 409; path-traversal-shaped filename sanitizes down to a safe name and never
escapes graph/attachments/; create_node failure after a successful file write triggers the
compensating cleanup (the file is gone afterward)."""
from __future__ import annotations


async def test_upload_attachment_creates_file_and_artifact_node(ui_client, graph_dir):
    r = await ui_client.post(
        "/api/attachments",
        data={"title": "My Datasheet"},
        files={"file": ("datasheet.pdf", b"pdf bytes here", "application/pdf")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "My Datasheet"
    assert body["type"] == "artifact"
    assert body["properties"] == {
        "attachment_path": "graph/attachments/datasheet.pdf",
        "attachment_original_filename": "datasheet.pdf",
        "attachment_size_bytes": len(b"pdf bytes here"),
        "attachment_mime_type": "application/pdf",
    }
    dest = graph_dir / "graph" / "attachments" / "datasheet.pdf"
    assert dest.is_file()
    assert dest.read_bytes() == b"pdf bytes here"


async def test_upload_attachment_with_part_of(ui_client):
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Parent"})
    r = await ui_client.post(
        "/api/attachments",
        data={"title": "Attached File", "part_of": "Parent"},
        files={"file": ("x.txt", b"hi", "text/plain")},
    )
    assert r.status_code == 200

    r = await ui_client.get("/api/outline")
    edges = {(e["src"], e["dst"], e["type"]) for e in r.json()["edges"]}
    assert ("Attached File", "Parent", "part_of") in edges


async def test_upload_attachment_oversized_413_and_no_file_left(ui_client, graph_dir, monkeypatch):
    monkeypatch.setattr("redraft.ui.mutations.MAX_ATTACHMENT_BYTES", 10)  # keep the test light
    r = await ui_client.post(
        "/api/attachments",
        data={"title": "Too Big"},
        files={"file": ("big.bin", b"x" * 11, "application/octet-stream")},
    )
    assert r.status_code == 413
    attachments_dir = graph_dir / "graph" / "attachments"
    assert not any(attachments_dir.glob("*")) if attachments_dir.exists() else True
    # the node must not have been created either
    r = await ui_client.get("/api/outline")
    assert r.json()["nodes"] == []


async def test_upload_attachment_filename_collision_409(ui_client):
    await ui_client.post(
        "/api/attachments", data={"title": "First"}, files={"file": ("same.txt", b"one", "text/plain")}
    )
    r = await ui_client.post(
        "/api/attachments", data={"title": "Second"}, files={"file": ("same.txt", b"two", "text/plain")}
    )
    assert r.status_code == 409


async def test_upload_attachment_path_traversal_filename_sanitized(ui_client, graph_dir):
    r = await ui_client.post(
        "/api/attachments",
        data={"title": "Sneaky"},
        files={"file": ("../../etc/passwd", b"not actually passwd", "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["properties"]["attachment_path"] == "graph/attachments/passwd"
    assert body["properties"]["attachment_original_filename"] == "../../etc/passwd"
    # never escaped graph/attachments/
    assert (graph_dir / "graph" / "attachments" / "passwd").is_file()
    assert not (graph_dir / "etc").exists()


async def test_upload_attachment_empty_title_422_not_500_and_no_stranded_file(ui_client, graph_dir):
    """BUG FOUND AND FIXED: upload_attachment's own create_node sub-step reaches the exact
    same bare-ValueError title-sanitization gap as create_node/rename_node (see
    UIAppState.mutate's docstring) -- covered by the same blanket safety net. The compensating
    cleanup in upload_attachment's own except clause must ALSO still fire: the file is
    written successfully before create_node ever runs, so a title-validation failure there
    must not strand it."""
    r = await ui_client.post(
        "/api/attachments", data={"title": "   "}, files={"file": ("orphan.txt", b"data", "text/plain")}
    )
    assert r.status_code == 422
    assert not (graph_dir / "graph" / "attachments" / "orphan.txt").exists()


async def test_upload_attachment_create_node_failure_triggers_compensating_cleanup(ui_client, graph_dir):
    """A title collision (a DIFFERENT hazard than the filename collision above) fires only
    once the file is already durably written -- the compensating os.remove(dest) in
    mutations.upload_attachment must fire, or the file would strand with no owning node."""
    await ui_client.post("/api/nodes", json={"type": "concept", "title": "Already Exists"})

    r = await ui_client.post(
        "/api/attachments",
        data={"title": "Already Exists"},
        files={"file": ("orphan-candidate.txt", b"data", "text/plain")},
    )
    assert r.status_code == 409

    dest = graph_dir / "graph" / "attachments" / "orphan-candidate.txt"
    assert not dest.exists()  # compensating cleanup ran -- never stranded
