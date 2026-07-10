"""GET /api/outline, GET /api/attention (s6-ui.md §9) -- both Lane B. Housed together
because neither the design's module layout (§2) nor its endpoint table (§9) name a separate
router file for /api/attention; it is the RIGHT-hand hygiene strip's own "dump everything"
read, exactly the same shape as /api/outline's LEFT-hand "dump everything" read for the
Outline tree/Map tab, so it lives alongside it here rather than getting a one-route file of
its own."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

from redraft.tools.read_tools import index_read_conn
from redraft.ui.models import AttentionOut, OutlineOut
from redraft.ui.queries import attention_summary, outline_nodes

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

router = APIRouter()


@router.get("/api/outline", response_model=OutlineOut)
def get_outline(request: Request) -> OutlineOut:
    state: UIAppState = request.app.state.ui
    with index_read_conn(state.graph_dir) as conn:
        return outline_nodes(conn)


@router.get("/api/attention", response_model=AttentionOut)
def get_attention(request: Request) -> AttentionOut:
    state: UIAppState = request.app.state.ui
    with index_read_conn(state.graph_dir) as conn:
        return attention_summary(conn)
