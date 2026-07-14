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
import re
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

# FIX (CSRF): this UI has no auth of its own -- it's a strictly local dev tool (127.0.0.1 by
# default) -- so any cross-origin page open in the same browser could otherwise POST/GET
# against it. Any loopback HOST is accepted regardless of PORT: loopback is inherently
# local-trust (only a process already running as this same user/machine can bind or reach
# it), which avoids having to thread the actual bound port into a middleware built at
# create_app() time (uvicorn's port==0 auto-assign, or a caller that just doesn't pass one,
# would otherwise have no port to check against).
_LOOPBACK_ORIGIN_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", re.IGNORECASE)
# Same loopback host set as _LOOPBACK_ORIGIN_RE, just without the scheme -- Host headers never
# carry one (design note on _same_origin_guard below explains why this exists alongside Origin).
_LOOPBACK_HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", re.IGNORECASE)


def _is_allowed_origin(origin: str) -> bool:
    return _LOOPBACK_ORIGIN_RE.match(origin) is not None


def _is_allowed_host(host: str) -> bool:
    return _LOOPBACK_HOST_RE.match(host) is not None


def _origin_matches_host(origin: str, host: str | None) -> bool:
    """Self-origin test for the non-strict (non-loopback bind) mode: does `origin`'s
    host[:port], after stripping its http/https scheme, equal the request's own Host header
    value case-insensitively? A browser's same-origin SPA fetch always satisfies this (its
    Origin is literally scheme + the URL authority it loaded the page from, which is what it
    sends as Host); a cross-site page's script never can, since its Origin names ITS OWN
    authority. Non-http(s) origins ("null", extension schemes) never match."""
    if host is None:
        return False
    m = re.match(r"^https?://(.+)$", origin, re.IGNORECASE)
    return m is not None and m.group(1).lower() == host.lower()


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
    graph_dir: Path,
    retrieval_config: RetrievalConfig,
    *,
    reindex_poll_interval: float = 5.0,
    strict_loopback: bool = True,
) -> FastAPI:
    """strict_loopback=True (the default, and the secure configuration) pins
    _same_origin_guard below to loopback-only Host and Origin values — correct for the default
    127.0.0.1 bind. main() passes False when the operator explicitly binds a non-loopback
    --host (LAN exposure), where loopback-only checks would 403 every remote request,
    including the app's own SPA fetches; see the middleware docstring for what is (and isn't)
    still protected in that mode."""
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

    @app.middleware("http")
    async def _same_origin_guard(request: Request, call_next):
        """CSRF hardening: reject any request carrying an Origin header whose host isn't
        loopback. Browsers attach Origin to cross-origin AND same-origin fetch/XHR/form
        requests alike, so a same-origin SPA fetch (Origin == this server's own scheme+host)
        is exactly as loopback as request.url itself and passes unaffected; only a genuinely
        foreign Origin (e.g. http://evil.example, from a cross-origin page's script) is
        rejected. Requests with NO Origin header at all (curl, a same-origin top-level
        navigation) are also let through unchanged -- Origin's absence means no
        browser-mediated cross-origin caller is involved in the first place. Registered AFTER
        _start_background_tasks_once above so it wraps OUTSIDE it (Starlette's user_middleware
        list is built such that the last `@app.middleware` added runs first) -- a rejected
        cross-origin request never even reaches that lazy task-kickoff, let alone any router.
        Applies to every route, GET included: GET is read-only so allowing it is safe, and
        applying uniformly (rather than only to mutating verbs) also closes the reindex-CSRF
        finding for free without a second, route-specific mechanism.

        DNS-rebinding residual (red-team finding), closed here too: Origin-only checking still
        lets through any request that simply carries NO Origin header at all -- true of every
        non-browser HTTP client, not just curl. A DNS-rebinding attacker who gets a name like
        evil.example to resolve to 127.0.0.1 (after the victim's browser already loaded a page
        from it) can issue exactly such a request, whose Host header names the attacker's own
        domain rather than this server's loopback address, straight at this process. Checked in
        the SAME middleware (one CSRF gate, not two) rather than a second one: any request whose
        Host header is present and not loopback is rejected before the Origin check even runs.

        strict_loopback=False (operator explicitly bound a non-loopback --host, i.e. chose LAN
        exposure) relaxes BOTH checks -- loopback-only versions would 403 literally every
        remote request, browser or not, making the documented `redraft ui --host 0.0.0.0` flag
        useless. In that mode: the Host check is skipped entirely (we cannot enumerate which
        IPs/names legitimately reach this machine), and the Origin check also accepts a
        self-origin -- Origin whose host[:port], scheme stripped, equals the request's own Host
        header (_origin_matches_host) -- alongside the loopback set, so the SPA's own fetches
        pass while a genuinely foreign Origin (classic cross-site CSRF) still 403s. TRADEOFF,
        stated plainly: Origin==Host self-origin matching is defeatable by DNS rebinding (the
        rebound page's Origin carries the attacker's domain, but so does the victim's Host
        header -- they match). Non-loopback binding is an explicit operator opt-in to that
        residual risk; the loopback default remains the secure configuration.
        """
        origin = request.headers.get("origin")
        if strict_loopback:
            host = request.headers.get("host")
            if host is not None and not _is_allowed_host(host):
                return PlainTextResponse("cross-origin request rejected", status_code=403)
            if origin is not None and not _is_allowed_origin(origin):
                return PlainTextResponse("cross-origin request rejected", status_code=403)
        elif origin is not None and not (
            _is_allowed_origin(origin) or _origin_matches_host(origin, request.headers.get("host"))
        ):
            return PlainTextResponse("cross-origin request rejected", status_code=403)
        return await call_next(request)

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    async def spa_fallback(request: Request, full_path: str):
        """Registered LAST, after every /api/* router include and the /assets mount --
        client-side routes (react-router-dom paths) have no matching server route, so this
        catch-all returns the SPA shell for anything else.

        GET+HEAD (not just GET): a bare @app.get left every path -- including "/" -- 405ing on
        HEAD, which health checks and link unfurlers use routinely. FastAPI/Starlette strip the
        body for a HEAD response automatically, so no branching is needed here for the SPA/static
        paths this route is actually meant to serve.

        BUG FOUND AND FIXED (this same HEAD change): Starlette's router calls the FIRST route
        that FULLY matches a request, and this wildcard's `:path` converter fully matches every
        URL -- so once it also declared HEAD, it started winning the routing race against every
        real /api/* GET-only route on a HEAD request (e.g. HEAD /api/outline), silently
        returning this SPA shell (200, text/html) instead of the 405 those endpoints should give
        (the specific route only ever gets a PARTIAL match for an unsupported method, and a
        partial match never overrides a wildcard's full one). Explicitly refusing HEAD for any
        full_path under "api/" restores /api/*'s intended GET-only 405 without touching a single
        one of those routes.

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
        if request.method == "HEAD" and full_path.startswith("api/"):
            return PlainTextResponse("Method Not Allowed", status_code=405)
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
    # A non-loopback bind (`--host 0.0.0.0`, a LAN IP, a hostname) is an explicit operator
    # opt-in to network exposure -- create_app's loopback-hardcoded guards would otherwise 403
    # every single remote request (see _same_origin_guard). Compared directly against the bare
    # bind-address forms argparse hands us -- schemeless AND bracketless (a bind address is
    # "::1", never "[::1]"), so _is_allowed_host (which expects the bracketed Host-header form)
    # deliberately isn't reused here.
    strict = host.lower() in ("127.0.0.1", "localhost", "::1")
    application = create_app(
        resolved, RetrievalConfig(), reindex_poll_interval=reindex_poll_interval, strict_loopback=strict
    )
    uvicorn.run(application, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
