"""POST /api/attachments (multipart) (s6-ui.md §6, §9) -- Lane A. See
mutations.upload_attachment's own docstring for why it is called directly here rather than
dispatched through StoreWorker.call as a single unit (it needs genuinely async file I/O,
which StoreWorker.call's run_in_executor-based dispatch cannot drive)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Form, Request, UploadFile

from redraft.models import NodeOut, to_node_out
from redraft.ui import mutations

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

router = APIRouter()


@router.post("/api/attachments", response_model=NodeOut)
async def upload_attachment(
    request: Request,
    file: UploadFile,
    title: Annotated[str, Form()],
    part_of: Annotated[str | None, Form()] = None,
) -> NodeOut:
    state: UIAppState = request.app.state.ui
    node = await mutations.upload_attachment(state, file, title, part_of)
    return to_node_out(node)
