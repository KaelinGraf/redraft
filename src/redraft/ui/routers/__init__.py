"""include_all(app): registers every /api/* router (s6-ui.md §2). One place that knows the
full set of router modules, so app.py doesn't need to."""
from __future__ import annotations

from fastapi import FastAPI

from redraft.ui.routers import admin, attachments, edges, nodes, outline, reports, schema, search, timeline

_ROUTERS = (schema, outline, nodes, edges, search, reports, attachments, admin, timeline)


def include_all(app: FastAPI) -> None:
    for module in _ROUTERS:
        app.include_router(module.router)
