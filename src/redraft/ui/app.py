"""FastAPI app assembly for the operator UI (s6-ui.md §7, §4, §5).

create_app(graph_dir, retrieval_config, *, reindex_poll_interval=5.0) -> FastAPI is the
real, testable entrypoint (tests/conftest.py's ui_app fixture calls it directly). main() is
the `redraft ui` CLI entrypoint (redraft.cli's `ui` subcommand hands off here after parsing
--host/--port/--graph-dir/--reindex-poll-interval). app() is a zero-arg factory for manual
`uvicorn redraft.ui.app:app --factory` boot checks (s6-ui.md Phase A/E gates), resolving
REDRAFT_DIR the same way server.ServerConfig.from_env() does for the MCP server.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from redraft.config import resolve_graph_dir
from redraft.retrieval._util import now_iso  # already reused this way by report.py
from redraft.retrieval.embeddings import RetrievalConfig, get_embedder
from redraft.store import GraphStore
from redraft.ui.errors import install_error_handlers
from redraft.ui.routers import include_all
from redraft.ui.store_worker import StoreWorker

logger = logging.getLogger(__name__)

STATIC_DIR = (Path(__file__).parent / "static").resolve()

T = TypeVar("T")


@dataclass
class UIAppState:
    """Everything a request handler needs off request.app.state.ui. `generation` is bumped
    once at the end of every successful Lane-A mutation via .mutate() (s6-ui.md §4.2) --
    mutated only from the asyncio event-loop thread (every route handler and the background
    poll task both run there; StoreWorker's own dedicated worker thread never touches this
    field), so a plain int needs no lock: CPython attribute reads/writes are never torn, and
    every reader only ever asks "did this change," never relies on an exact value.
    """

    graph_dir: Path
    retrieval_config: RetrievalConfig
    worker: StoreWorker
    reindex_poll_interval: float
    generation: int = 0
    last_reindex_at: str | None = None
    embedder_ready: bool = False

    async def mutate(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Lane-A helper: run fn via StoreWorker, then bump `generation`. Every mutating
        router handler calls this instead of `worker.call` directly, so the generation
        counter can never be forgotten at a call site (s6-ui.md §4.2).

        BUG FOUND AND FIXED (systemic, not per-endpoint): several GraphStore methods have
        bare `ValueError` raise sites for ordinary client-input mistakes that are NOT among
        the six redraft.errors types ui/errors.py's _HTTP_STATUS table maps -- title
        sanitizing to "" via ids.sanitize_title_to_id (reachable from create_node,
        rename_node, and upload_attachment's own create_node sub-step), a status illegal for
        a node's type via schema.validate_status (reachable from create_node and
        update_node), and merge_nodes' keep_id==drop_id guard. This is the exact same
        failure class write_tools.py's own "BUG FOUND AND FIXED" docstring documents for the
        MCP layer. Rather than a try/except repeated at each of those call sites (found by
        auditing every store.py method reachable through this one helper), the fix lives
        here, once: any bare ValueError escaping a Lane-A call is a client-input problem, not
        a server bug, and maps uniformly to 422.
        """
        try:
            result = await self.worker.call(fn, *args, **kwargs)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        self.generation += 1
        return result


async def _warm_embedder(state: UIAppState) -> None:
    """s6-ui.md §5.1: fire-and-forget background task, off BOTH lanes -- the model load
    touches no sqlite3.Connection at all (pure in-memory ONNX session construction), so
    routing it through StoreWorker would only add a pointless dependency of the
    write-serializing queue on a ~10s cold download, for zero benefit."""
    try:
        await asyncio.to_thread(get_embedder, state.retrieval_config)
        state.embedder_ready = True
    except Exception:
        logger.exception("embedding model failed to warm; dedup hints stay FTS-only")


async def _poll_reindex(state: UIAppState) -> None:
    """s6-ui.md §4.2 point 2: catches a hand-edit or the MCP server's own writes reaching
    this process's outline/query view without a human clicking anything. A failed tick (e.g.
    a transient LockTimeoutError racing a concurrent Lane-A write) is logged and the loop
    keeps going -- one bad tick must never silently kill every future one."""
    while True:
        await asyncio.sleep(state.reindex_poll_interval)
        try:
            await state.mutate(GraphStore.reindex)
            state.last_reindex_at = now_iso()
        except Exception:
            logger.exception("background reindex poll tick failed")


def create_app(
    graph_dir: Path, retrieval_config: RetrievalConfig, *, reindex_poll_interval: float = 5.0
) -> FastAPI:
    worker = StoreWorker(graph_dir, retrieval_config)
    # Block until GraphStore's own construction (including its constructor's reindex() call)
    # finishes -- closes the startup race between Lane-B's direct index_read_conn reads
    # (which never touch StoreWorker at all, by design, s6-ui.md §3.4) and StoreWorker's own
    # async-on-a-thread GraphStore construction of index/graph.sqlite3 itself.
    worker.wait_ready()
    state = UIAppState(
        graph_dir=graph_dir,
        retrieval_config=retrieval_config,
        worker=worker,
        reindex_poll_interval=reindex_poll_interval,
    )

    app = FastAPI(title="redraft")
    app.state.ui = state
    install_error_handlers(app)
    include_all(app)
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    tasks_started = False

    @app.middleware("http")
    async def _start_background_tasks_once(request: Request, call_next):
        """Lazily starts the embedder-warmup and reindex-poll background tasks on the FIRST
        request this process ever serves, rather than inside a FastAPI `lifespan` startup
        handler. Verified live: httpx.ASGITransport (the transport tests/conftest.py's
        ui_client fixture uses, per s6-ui.md §12.1) never sends a "lifespan" ASGI scope
        message at all -- only "http" -- so a lifespan-only start would never fire under
        test. It would also be wrong for create_app() to call asyncio.create_task(...)
        directly at construction time: create_app() always runs synchronously, before any
        event loop exists yet, both from `redraft ui`'s CLI entrypoint (uvicorn.run() hasn't
        created its loop yet at that point) and from tests' plain-sync `ui_app` fixture --
        asyncio.create_task requires an ALREADY running loop. The first real request, in
        contrast, is always genuinely awaited from inside a running loop (confirmed
        empirically), so that is the earliest point both paths can safely start these tasks,
        with identical behavior in production (uvicorn) and under ASGITransport-based tests.
        """
        nonlocal tasks_started
        if not tasks_started:
            tasks_started = True
            asyncio.create_task(_warm_embedder(state))
            if state.reindex_poll_interval:
                asyncio.create_task(_poll_reindex(state))
        return await call_next(request)

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Registered LAST, after every /api/* router include and the /assets mount --
        client-side routes (react-router-dom paths) have no matching server route, so this
        catch-all returns the SPA shell for anything else.

        BUG FOUND AND FIXED: a root-level static file that vite's public/ dir copies straight
        into static/ (favicon.svg) had no route of its own -- every request for it fell
        straight through to the SPA-shell branch below and got back index.html with
        content-type text/html, so a browser tab silently never picked up the icon. Any
        full_path that resolves (path-traversal-safe, via is_relative_to) to a real file
        under STATIC_DIR is now served directly first.

        static/index.html does not exist yet in this backend-only chunk (s6-ui.md §7.1's
        frontend hasn't landed) -- a plain 404 here instead of an unhandled stat-error crash,
        so a manual smoke-check against this backend alone (or a stray browser probe like
        /favicon.ico) gets a clean response, not a 500."""
        candidate = (STATIC_DIR / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(STATIC_DIR):
            return FileResponse(candidate)
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            return PlainTextResponse(
                "redraft ui: frontend not built yet (static/index.html is missing)", status_code=404
            )
        return FileResponse(index)

    return app


def app() -> FastAPI:
    """Zero-arg factory for `uvicorn redraft.ui.app:app --factory` manual boot checks
    (s6-ui.md Phase A/E gates) -- resolves REDRAFT_DIR exactly like
    server.ServerConfig.from_env() does, then delegates to create_app()."""
    return create_app(resolve_graph_dir(), RetrievalConfig())


def main(
    *,
    host: str = "127.0.0.1",
    port: int = 8420,
    graph_dir: str | Path | None = None,
    reindex_poll_interval: float = 5.0,
) -> None:
    """`redraft ui`'s real entrypoint -- redraft.cli's `ui` subcommand parses
    --host/--port/--graph-dir/--reindex-poll-interval and hands off here. host/port default
    to 127.0.0.1:8420, the exact address s6-ui.md §7.1's own vite dev-proxy config
    (`http://127.0.0.1:8420`) assumes the backend is listening on."""
    import uvicorn

    resolved = resolve_graph_dir(graph_dir)
    application = create_app(resolved, RetrievalConfig(), reindex_poll_interval=reindex_poll_interval)
    uvicorn.run(application, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
