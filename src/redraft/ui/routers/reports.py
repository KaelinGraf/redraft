"""GET /api/doc/{root_id}, GET /api/reports, GET /api/reports/{filename} (s6-ui.md §9) --
all Lane B. The SAME GET /api/doc/{root_id} payload backs both the Doc tab (.sections) and
the Tables tab (.decision_tables) -- one query, two views (s6-ui.md §10.1); no separate
/api/tables endpoint exists."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Query, Request

from redraft.models import EdgeType, ReportBundle
from redraft.report import assemble_report as lib_assemble_report
from redraft.tools.read_tools import index_read_conn
from redraft.ui.models import ReportContent, ReportFile
from redraft.ui.queries import list_reports, read_report

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

router = APIRouter()


@router.get("/api/doc/{root_id}", response_model=ReportBundle)
def get_doc(
    root_id: str,
    request: Request,
    depth: Annotated[int, Query(ge=0)] = 4,
    include_edge_types: Annotated[list[EdgeType] | None, Query()] = None,
) -> ReportBundle:
    state: UIAppState = request.app.state.ui
    with index_read_conn(state.graph_dir) as conn:
        return lib_assemble_report(conn, root_id, include_edge_types=include_edge_types, depth=depth)


@router.get("/api/reports", response_model=list[ReportFile])
def get_reports(request: Request) -> list[ReportFile]:
    state: UIAppState = request.app.state.ui
    return list_reports(state.graph_dir / "reports")


@router.get("/api/reports/{filename}", response_model=ReportContent)
def get_report(filename: str, request: Request) -> ReportContent:
    state: UIAppState = request.app.state.ui
    content = read_report(state.graph_dir / "reports", filename)
    return ReportContent(filename=filename, content=content)
