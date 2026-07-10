"""Lane-A compositions touching more than one GraphStore call: reparent_node, delete_node,
rename_node, upload_attachment (s6-ui.md §6.3/§9.1, §9.1 as amended by orchestrator ruling
§14.6).

reparent_node/delete_node/rename_node are plain sync `def f(store, ...)` functions,
dispatched through StoreWorker.call exactly like an unbound GraphStore method
(store_worker.py's own docstring) -- each does nothing but call GraphStore's own
synchronous public methods plus read store.con directly (safe: this already runs on
StoreWorker's single worker thread, the same thread that owns store.con, so no second
index_read_conn is needed the way a Lane-B query would need one).

delete_node/rename_node deliberately mirror redraft.tools.write_tools's own MCP tool
compositions for the identical operations (orphaned-inbound-edge reporting;
predicted-relinked-list + body-reference flagging) rather than reusing them directly --
those are closures nested inside register_write_tools, not top-level importable functions,
and refactoring write_tools.py to extract them would point the MCP (engine) layer's
dependencies at this UI layer, the wrong direction. This is a small (~8-line), deliberate,
two-call-site duplication, not an oversight -- the project's own "three call sites is the
earliest a helper is warranted" rule of thumb is not yet met, and the alternative (a shared
helper) would require relocating the logic into the engine layer itself, out of scope here.

upload_attachment does NOT fit the "plain def f(store, ...)" shape and is NOT dispatched
through StoreWorker.call as a whole -- see its own docstring for why (a real bug found in
the design sketch: an `async def` handed to StoreWorker.call's run_in_executor-based
dispatch never actually runs, verified empirically). It is called directly from the
attachments router instead, and calls back into StoreWorker (via UIAppState.mutate) only for
its one genuine GraphStore-touching sub-step.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, UploadFile

from redraft import graphrules
from redraft.config import GraphPaths
from redraft.errors import CollisionError, CycleError
from redraft.ids import sanitize_attachment_filename
from redraft.models import DeleteResult, NeighborEdge, RenameResult
from redraft.schema import EdgeType, Node, NodeType
from redraft.store import GraphStore
from redraft.tools.read_tools import query_edges

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

# s6-ui.md ruling §14.2: a fixed module constant, no config knob. The refusal message states
# the limit -- the graph is a git repo; larger blobs don't belong in it.
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
_CHUNK = 1024 * 1024


def reparent_node(store: GraphStore, node_id: str, new_parent: str | None) -> Node:
    """PUT /api/nodes/{id}/parent. Composes GraphStore.get_node/delete_edge/create_edge -- no
    single existing GraphStore method does this in one call (create_edge(..., PART_OF) only
    *adds*; per amendment A1 it raises CollisionError outright if src already has a
    *different* parent, precisely so this composition -- not GraphStore itself -- owns the
    "delete old, then create new" sequencing).

    Implements s6-ui.md §9.1 AS AMENDED by orchestrator ruling §14.6 -- the flagged
    partial-state window is CLOSED here, not accepted:
      (a) new_parent's existence is checked FIRST, via store.get_node (raises NotFoundError
          -> 404), before the old part_of edge is ever touched -- kills the most likely
          failure (a missing/typo'd target) with zero partial-state exposure.
      (a2) a cycle is ALSO pre-checked, via graphrules.would_create_cycle -- its signature
          composes cleanly from here (store.con is a public GraphStore attribute), so this
          goes beyond §14.6's minimum bar and closes the cycle-rejection failure mode too, not
          only the missing-parent one. The precheck computes the identical answer create_edge's
          own internal would_create_cycle call would give after the delete below: the upward
          walk from new_parent only ever needs to determine whether node_id appears in its
          ancestor chain, stopping the instant it does -- node_id's OWN outbound edge (the one
          about to be deleted) is never consulted by that walk, so deleting it first vs. after
          cannot change the result.
      (b) create_edge is still wrapped in try/except with a best-effort compensating restore
          of the original part_of edge before re-raising -- belt-and-braces against the one
          residual window neither precheck can close: a concurrent CROSS-PROCESS write (a
          second `redraft serve` MCP session, say) landing between this call's delete_edge and
          create_edge -- e.g. deleting new_parent, or introducing a cycle, after our checks
          already passed. That residual window is exactly what ruling §14.6 accepts as "a
          cross-process race measured in milliseconds, single-user tool" -- Lane A's own
          single-worker-thread serialization already rules out an INTRA-process race.
    """
    current = store.get_node(node_id)
    old_parent = current.part_of

    if new_parent is not None:
        store.get_node(new_parent)  # (a): NotFoundError -> 404, before any edge is touched
        if new_parent != old_parent and graphrules.would_create_cycle(store.con, node_id, new_parent):
            raise CycleError(node_id, new_parent)  # (a2)

    if old_parent is not None and old_parent != new_parent:
        store.delete_edge(node_id, old_parent, EdgeType.PART_OF)
    if new_parent is not None and new_parent != old_parent:
        try:
            store.create_edge(node_id, new_parent, EdgeType.PART_OF)
        except BaseException:  # (b): best-effort restore, then ALWAYS re-raise the original
            if old_parent is not None:
                with contextlib.suppress(Exception):
                    store.create_edge(node_id, old_parent, EdgeType.PART_OF)
            raise
    return store.get_node(node_id)


def delete_node(store: GraphStore, node_id: str) -> DeleteResult:
    """DELETE /api/nodes/{id}. Mirrors write_tools.py's delete_node MCP tool composition:
    query orphaned inbound edges BEFORE deleting, so store.delete_node's own NotFoundError
    (if node_id doesn't exist) still propagates from the second call, unchanged -- the
    orphaned-edges query on a nonexistent id is a harmless, side-effect-free read either way.
    """
    orphaned = query_edges(store.con, anchor_col="dst", anchor_val=node_id, edge_types=None, direction="in")
    store.delete_node(node_id)
    return DeleteResult(ok=True, existed=True, orphaned_inbound_edges=orphaned)


def rename_node(store: GraphStore, node_id: str, new_title: str) -> RenameResult:
    """POST /api/nodes/{id}/rename. Mirrors write_tools.py's rename_node MCP tool composition:
    `relinked` is a PREDICTION from a pre-call inbound-edges snapshot, not an observed fact --
    GraphStore.rename_node itself still returns only a bare Node (design-storage.md §8), same
    caveat write_tools.py's own docstring already names."""
    inbound = query_edges(store.con, anchor_col="dst", anchor_val=node_id, edge_types=None, direction="out")
    node = store.rename_node(node_id, new_title)
    new_id = node.id
    relinked = [NeighborEdge(src=e.src, dst=new_id, type=e.type, direction="out") for e in inbound]
    literal = f"[[{node_id}]]"
    body_refs = [
        row[0] for row in store.con.execute("SELECT id, body FROM nodes").fetchall() if literal in (row[1] or "")
    ]
    return RenameResult(old_id=node_id, new_id=new_id, relinked=relinked, body_references_not_updated=body_refs)


async def _write_attachment_stream(dest: Path, file: UploadFile) -> int:
    """tempfile+os.replace, the same atomic-rename idiom as nodefile._atomic_write (s6-ui.md
    §2.2's note on why that function itself isn't reused/generalized here -- this needs a
    streaming, chunk-capped BINARY write, a genuinely different access pattern from "encode
    one string and write it in one call"). Raises HTTPException(413) without ever writing the
    over-cap remainder to disk -- enforcement does not trust UploadFile.size or Content-Length
    (either can be absent or wrong); it counts bytes as they stream through this loop.
    """
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-upload-")
    total = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := await file.read(_CHUNK):
                total += len(chunk)
                if total > MAX_ATTACHMENT_BYTES:
                    raise HTTPException(413, f"attachment exceeds the {MAX_ATTACHMENT_BYTES}-byte limit")
                f.write(chunk)
        # Re-checked immediately before the atomic rename (not just once, up front, before
        # this potentially-slow streaming write started) -- narrows, though per s6-ui.md
        # §6.2/Risk #8 does not eliminate, the collision TOCTOU window: a second uploader
        # racing this exact filename can still land between THIS check and os.replace below,
        # which is the same residual race Risk #8 explicitly accepts as "rare, low-consequence
        # (a confusing overwrite, not corruption)" -- this just shrinks it from "the whole
        # upload duration" down to one instant, for free.
        if dest.exists():
            raise CollisionError(dest.name, dest)
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    return total


async def upload_attachment(
    state: "UIAppState", file: UploadFile, title: str, part_of: str | None
) -> Node:
    """POST /api/attachments (s6-ui.md §6.3).

    BUG FOUND AND FIXED versus the design's own code sketch: the sketch shows this whole
    function as a plain `async def` intended to be dispatched via `worker.call(upload_attachment,
    ...)`, the same way reparent_node is. That does not work: StoreWorker.call's
    run_in_executor-based dispatch only ever CALLS a callable on the worker thread -- it does
    not drive a coroutine -- so handing it an `async def` returns an un-awaited coroutine
    object without ever running the body (verified empirically: `RuntimeWarning: coroutine
    ... was never awaited`, and the file is never actually read/written). The fix: this
    function is called DIRECTLY from the attachments router (not via worker.call), and calls
    back into StoreWorker (via state.mutate, the same generation-bumping helper every other
    Lane-A endpoint uses) only for its one genuine GraphStore-touching sub-step, create_node.
    Everything before that -- mkdir, filename sanitization, the collision check, the
    streaming write -- touches only the filesystem, never GraphStore/sqlite3 state, so it
    needs no worker-thread serialization at all; it runs directly on the request's own task.
    """
    attachments_dir = GraphPaths(state.graph_dir).attachments_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)  # lazy: doesn't exist until first upload
    safe_name = sanitize_attachment_filename(file.filename or "attachment")
    dest = attachments_dir / safe_name
    # Belt-and-braces (s6-ui.md Risk #7): sanitize_attachment_filename already strips '/'/'\\'
    # (both in ids.ILLEGAL) so this branch cannot currently be hit -- a cheap, independent
    # guard against a future weakening of that guarantee.
    if not dest.resolve().is_relative_to(attachments_dir.resolve()):
        raise HTTPException(400, "invalid attachment filename")
    if dest.exists():
        raise CollisionError(safe_name, dest)  # same type, same 409 mapping as a title collision
    total = await _write_attachment_stream(dest, file)
    try:
        return await state.mutate(
            GraphStore.create_node,
            type=NodeType.ARTIFACT,
            title=title,
            part_of=part_of,
            properties={
                "attachment_path": f"graph/attachments/{safe_name}",
                "attachment_original_filename": file.filename,
                "attachment_size_bytes": total,
                "attachment_mime_type": file.content_type,
            },
        )
    except BaseException:
        # compensating cleanup: never strand an attachment file with no node (e.g. a title
        # collision with an existing artifact node, raised by create_node itself)
        with contextlib.suppress(FileNotFoundError):
            os.remove(dest)
        raise
