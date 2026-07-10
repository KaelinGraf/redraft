"""FastMCP server assembly: build_server() + the stdio entrypoint.

Closure-state pattern (design-server.md section 4): FastMCP's `lifespan`
parameter is deliberately not used (a documented open FastMCP issue makes its
per-call-execution behavior version-sensitive; verified fastmcp==3.4.3's
FastMCP.__init__ still accepts `lifespan` but nothing here needs async
setup/teardown). A single ServerState is built once in build_server() and
captured by closure in every register_*_tools() call.

Per pin R1, redraft.store.GraphStore owns all write semantics
(locking, part_of cycle rejection, inbound-wikilink rewriting, collision
checks) -- this module and every tools/*.py module is a thin mapping over
it. I7: the stub this module's import once required during S2's contract-first
development (tests/_stub_store.py, sys.modules-injected by tests/conftest.py) is gone --
the import below now resolves to S1's real GraphStore in every context, tests included.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import sqlite_vec
from fastmcp import FastMCP

from redraft.store import GraphStore  # unconditional; see module docstring
from redraft.prompts import ORGANIZING_PROTOCOL_TEXT, register_prompts
from redraft.resources import register_resources
from redraft.retrieval import RetrievalConfig
from redraft.tools import register_all

REDRAFT_DIR_ENV = "REDRAFT_DIR"


@dataclass
class ServerConfig:
    """Scoped to what the write/read/admin/search/integrity tool surface needs: the graph
    repo root (pin R4 -- contains graph/nodes/, gitignored index/, .redraft.lock) plus
    retrieval_config (S3b: embedding_model_id, dims, cache_dir, fts_candidate_pool, rrf_k --
    RetrievalConfig's own field set, design-server.md section 4/6). Defaults to
    RetrievalConfig() (bge-small-en-v1.5) rather than None: search_nodes/find_similar/the
    integrity tools are unconditionally registered by register_all, so a caller building a
    server the plain ServerConfig(graph_dir=...) way still gets a fully working retrieval
    surface, not one silently returning nothing because no config ever reached GraphStore.
    GraphStore itself still treats retrieval_config as optional (see its own docstring) --
    that seam is what lets tests/test_store.py construct a GraphStore directly with none of
    this. write_lock_timeout_s isn't needed here: GraphStore owns lock acquisition
    internally (pin R1), so this layer never constructs a filelock.FileLock itself.
    """

    graph_dir: Path
    retrieval_config: RetrievalConfig = field(default_factory=RetrievalConfig)

    @classmethod
    def from_env(cls) -> "ServerConfig":
        raw = os.environ.get(REDRAFT_DIR_ENV)
        if not raw:
            raise RuntimeError(
                f"{REDRAFT_DIR_ENV} must be set to the graph repo root "
                "(the directory containing graph/nodes/)"
            )
        return cls(graph_dir=Path(raw))


@dataclass
class ServerState:
    config: ServerConfig
    store: GraphStore


def _check_sqlite_capabilities() -> None:
    """Pin R8: fail fast at startup if FTS5, extension-loading, or sqlite-vec itself is
    unavailable, rather than failing confusingly inside a later tool call. All three are
    verified present in this uv-managed CPython 3.12 build (sqlite3.sqlite_version 3.50.4,
    sqlite-vec 0.1.9, checked live) but are environment properties, not guarantees -- some
    system Pythons (historically some macOS system builds) compile sqlite3 without FTS5 or
    extension-loading, and even a Python that supports extension-loading in principle can
    still fail to load sqlite-vec's specific compiled extension (design-storage.md 5.3 /
    design-server.md Risk 5).
    """
    con = sqlite3.connect(":memory:")
    try:
        try:
            con.execute("CREATE VIRTUAL TABLE _cap_check USING fts5(x)")
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                "SQLite FTS5 extension is unavailable in this Python's sqlite3 "
                "build; redraft requires it (a uv-managed CPython "
                f"3.12 is expected to include it): {e}"
            ) from e
        try:
            con.enable_load_extension(True)
            con.enable_load_extension(False)
        except (AttributeError, sqlite3.NotSupportedError) as e:
            raise RuntimeError(
                "SQLite extension loading is unavailable in this Python's "
                f"sqlite3 build; redraft's vector index requires it: {e}"
            ) from e
        try:
            con.enable_load_extension(True)
            sqlite_vec.load(con)
            con.enable_load_extension(False)
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                "The sqlite-vec extension failed to load into this Python's sqlite3 "
                "build; redraft's vector index requires it -- reinstall "
                f"dependencies with a uv-managed Python (`uv sync`): {e}"
            ) from e
    finally:
        con.close()


def build_server(config: ServerConfig) -> FastMCP:
    _check_sqlite_capabilities()
    mcp = FastMCP(
        name="redraft",
        instructions=ORGANIZING_PROTOCOL_TEXT,
        mask_error_details=True,
    )
    state = ServerState(config=config, store=GraphStore(config.graph_dir, retrieval_config=config.retrieval_config))
    register_all(mcp, state)
    register_resources(mcp, state)
    register_prompts(mcp, state)
    return mcp


def main() -> None:
    mcp = build_server(ServerConfig.from_env())
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
