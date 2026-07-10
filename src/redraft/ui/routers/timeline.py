"""GET /api/timeline -- the Timeline tab (milestone/date planning), Lane B: its own
connection, never touches StoreWorker (same posture as outline.py/reports.py/search.py).

Gets its own file rather than piggybacking on outline.py: outline.py houses /api/outline and
/api/attention together specifically because NEITHER ever had a dedicated file in s6-ui.md
§2's original module layout (that doc's own docstring: "neither the design's module layout
... nor its endpoint table name a separate router file for /api/attention"). Timeline is a
sixth tab invented after that layout was written -- there is no "designed name" to piggyback
on -- so it follows the one-file-per-tab norm the OTHER tabs already set (reports.py for
Doc+Reports, search.py for Search+dedup-hints, admin.py for the admin surface), rather than
growing outline.py's own, differently-reasoned cohabitation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

from redraft.tools.read_tools import index_read_conn
from redraft.ui.models import TimelineOut
from redraft.ui.queries import timeline_items

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

router = APIRouter()


@router.get("/api/timeline", response_model=TimelineOut)
def get_timeline(request: Request) -> TimelineOut:
    state: UIAppState = request.app.state.ui
    with index_read_conn(state.graph_dir) as conn:
        return timeline_items(conn)
