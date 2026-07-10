"""HTTP status mapping for the six redraft.errors exception types GraphStore itself raises
(s6-ui.md §9) -- NOT the fastmcp-specific tool_errors.ToolError hierarchy, which is
irrelevant off the MCP transport. Mirrors tool_errors.translate_storage_error's dict-based
approach: same idea, a parallel small table, since the *output shape* here is genuinely
different (an HTTP status code via FastAPI's own per-exception-type handler registration,
not a second Python exception hierarchy).

No per-call-site wrapping (no `with translate_ui_errors(): ...`) is needed or used anywhere
in this package: Starlette's exception middleware already wraps the whole request-handling
call stack and catches anything a route handler lets propagate -- including an exception
raised deep inside an `await worker.call(...)` -- dispatching it by exact type (via
type(exc).__mro__) to whichever handler was registered below. Every Lane-A/Lane-B call site
in routers/*.py simply lets a NotFoundError/CollisionError/... bubble up unguarded; this is
strictly less code than wrapping every call site and is equally correct (verified live
against the installed fastapi==0.139.0/starlette==1.3.1: a custom exception raised inside an
awaited call, deep in a route handler, is caught by a handler registered via
add_exception_handler exactly as it would be for one raised directly).
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from redraft.errors import (
    CollisionError,
    CycleError,
    GitOperationError,
    LockTimeoutError,
    MalformedFrontmatterError,
    NotFoundError,
)

_HTTP_STATUS: dict[type[Exception], int] = {
    NotFoundError: 404,
    CollisionError: 409,
    CycleError: 409,
    LockTimeoutError: 503,
    MalformedFrontmatterError: 422,
    GitOperationError: 502,
}


def _make_handler(status: int):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    return handle


def install_error_handlers(app: FastAPI) -> None:
    """Registers one handler per _HTTP_STATUS entry. Anything NOT in this table (a genuinely
    unexpected exception) is left to FastAPI's own default 500 handling -- deliberately not
    caught here, the same "don't fall back to a generic mapping" posture
    tool_errors.translate_storage_error takes for the MCP layer, so a real bug surfaces
    loudly instead of being silently flattened into a misleading 4xx."""
    for exc_type, status in _HTTP_STATUS.items():
        app.add_exception_handler(exc_type, _make_handler(status))
