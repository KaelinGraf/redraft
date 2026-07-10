"""StoreWorker: the single GraphStore-owning thread (s6-ui.md §3.2).

The riskiest seam in this backend: GraphStore.__init__ opens ONE sqlite3.Connection
(self.con) and every mutating method plus get_node touches it. sqlite3 connections raise
"SQLite objects created in a thread can only be used in that same thread" the moment
they're touched from a thread other than their creator -- write_tools.py's own docstring
proved this live against the real GraphStore. The .redraft.lock filelock is a
CROSS-PROCESS mutex; it does nothing to stop two threads INSIDE this one UI process from
both touching the same sqlite3.Connection object concurrently.

StoreWorker owns the process's one GraphStore instance on one dedicated background thread,
via a ThreadPoolExecutor(max_workers=1). This gets both properties for free, from the
stdlib, with no hand-rolled lock:
  - Thread affinity: GraphStore(...) is constructed ON that worker thread (inside the
    first submitted callable), so self.con's creating thread and its only-ever-touching
    thread are the same thread, always.
  - Intra-process serialization: a ThreadPoolExecutor with exactly one worker processes
    submitted callables strictly FIFO -- that queue IS the serialization primitive.

Not every GraphStore-touching UI operation goes through StoreWorker -- see the module
docstrings in queries.py (Lane B: read-only, own connection, never touches StoreWorker)
and mutations.py (Lane A: real mutations, always through StoreWorker) for the split.
"""
from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TypeVar

from redraft.retrieval import RetrievalConfig
from redraft.store import GraphStore

T = TypeVar("T")


class StoreWorker:
    """Owns the process's single GraphStore instance on one dedicated thread. Every
    GraphStore-touching call in the UI backend goes through .call() -- never touch
    `worker.store.<method>` directly from a request handler or the event loop; that would
    reintroduce the exact cross-thread sqlite3.Connection hazard this class exists to close.
    """

    def __init__(self, graph_dir: Path, retrieval_config: RetrievalConfig) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graphstore-worker")
        self.store: GraphStore  # only ever assigned/read on the worker thread
        self._ready = self._executor.submit(self._init, graph_dir, retrieval_config)

    def _init(self, graph_dir: Path, retrieval_config: RetrievalConfig) -> None:
        self.store = GraphStore(graph_dir, retrieval_config=retrieval_config)

    def wait_ready(self) -> None:
        """Block the CALLING (non-worker) thread until GraphStore construction -- including
        its own constructor's reindex() call -- finishes. create_app() calls this once,
        synchronously, right after constructing a StoreWorker: Lane-B reads (queries.py) open
        their own connection straight to index/graph.sqlite3 and never touch StoreWorker at
        all (by design, s6-ui.md §3.4), so nothing else would otherwise stop a request that
        lands the instant the app boots from racing GraphStore's own async-on-a-thread
        construction of that very file. Re-raises whatever _init raised (e.g. a malformed
        graph), so a construction failure still fails app startup fast, matching how
        server.build_server()'s synchronous `GraphStore(...)` construction already fails
        fast today for the MCP server.
        """
        self._ready.result()

    async def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Run fn(self.store, *args, **kwargs) on the worker thread; await the result without
        blocking the event loop. `fn` is normally an unbound GraphStore method (e.g.
        GraphStore.create_node) so `functools.partial(fn, self.store, ...)` reduces to
        `self.store.create_node(...)` -- this works for any current or future GraphStore method
        with zero new plumbing. Composed multi-call operations (mutations.reparent_node) are
        plain `def f(store, ...)` functions passed the same way. NOTE: `fn` must be a plain
        SYNC callable -- run_in_executor only ever CALLS fn(...) on the worker thread, it does
        not drive a coroutine, so an `async def` passed here would silently return an
        un-awaited coroutine object instead of running (verified empirically). See
        mutations.upload_attachment's docstring for the one composition that needed genuinely
        async I/O and is structured around this constraint instead of fighting it.
        """
        await asyncio.wrap_future(self._ready)  # no-op after the first call; futures cache their result
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(fn, self.store, *args, **kwargs))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
