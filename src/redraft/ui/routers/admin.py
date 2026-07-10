"""POST /api/snapshot, POST /api/reindex, GET /api/status, GET /api/git-status (s6-ui.md §9).
snapshot/reindex are Lane A; status/git-status are Lane B (status reads UIAppState directly,
no DB touch at all; git-status is read-only subprocess work, gitops.working_tree_status)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, Request

from redraft import gitops
from redraft.models import ReindexStats, SnapshotResult, model_from
from redraft.retrieval._util import now_iso  # already reused this way by report.py
from redraft.store import GraphStore
from redraft.ui.models import GitStatusOut, StatusOut

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

router = APIRouter()


@router.post("/api/snapshot", response_model=SnapshotResult)
async def snapshot(
    request: Request, message: Annotated[str, Body()], push: Annotated[bool, Body()] = False
) -> SnapshotResult:
    state: UIAppState = request.app.state.ui
    result = await state.mutate(GraphStore.snapshot, message, push=push)
    return model_from(SnapshotResult, result)


@router.post("/api/reindex", response_model=ReindexStats)
async def reindex(request: Request) -> ReindexStats:
    state: UIAppState = request.app.state.ui
    stats = await state.mutate(GraphStore.reindex)
    state.last_reindex_at = now_iso()
    return model_from(ReindexStats, stats)


@router.get("/api/status", response_model=StatusOut)
def get_status(request: Request) -> StatusOut:
    state: UIAppState = request.app.state.ui
    return StatusOut(
        generation=state.generation, last_reindex_at=state.last_reindex_at, embedder_ready=state.embedder_ready
    )


@router.get("/api/git-status", response_model=GitStatusOut)
def get_git_status(request: Request) -> GitStatusOut:
    state: UIAppState = request.app.state.ui
    result = gitops.working_tree_status(state.graph_dir)
    return GitStatusOut(dirty=result.dirty, changed_paths=result.changed_paths)
