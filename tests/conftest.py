"""Shared pytest fixtures. All tests operate on tmp_path — never the real repo's graph/.

I7: merged from three sources at integration -- main's own store/_hermetic_git_identity
fixtures, S2's graph_dir/mcp_server fixtures (its sys.modules stub injection and
tests/_stub_store.py are gone; every server test now runs against a real GraphStore), and
S3a's conn/vec_ready_conn/retrieval_config/warm_embedder fixtures plus insert_node/
insert_edge helpers (unchanged -- that slice never depended on redraft.store).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
import sqlite_vec

from fixtures_ddl import build_fixture_db
from redraft.retrieval.embeddings import RetrievalConfig, get_embedder
from redraft.retrieval.vector_index import ensure_embedding_schema
from redraft.store import GraphStore


@pytest.fixture(autouse=True)
def _hermetic_git_identity(monkeypatch):
    """Git commits need user.name/user.email resolvable. Set them via env vars so
    test_gitops.py's "not yet a repo" scenario works even without ambient global git
    config, and so tests never depend on the machine's real git identity.
    """
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test Author")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")


@pytest.fixture
def store(tmp_path):
    return GraphStore(tmp_path)


# -- S2 server-layer fixtures ---------------------------------------------------------------


@pytest.fixture
def graph_dir(tmp_path: Path) -> Path:
    """REDRAFT_DIR is the graph repo ROOT (pin R4): contains graph/nodes/."""
    (tmp_path / "graph" / "nodes").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def mcp_server(graph_dir: Path):
    from redraft.server import ServerConfig, build_server

    return build_server(ServerConfig(graph_dir=graph_dir))


# -- S6 UI-layer fixtures ---------------------------------------------------------------


@pytest.fixture
def ui_app(graph_dir: Path):
    from redraft.ui.app import create_app

    app = create_app(graph_dir, RetrievalConfig(), reindex_poll_interval=0)  # 0 disables the
                                                                               #   background poll -- deterministic tests
    yield app
    app.state.ui.worker.shutdown()


@pytest.fixture
async def ui_client(ui_app):
    # base_url's host becomes this client's default Host header (httpx derives it from the
    # authority when none is set explicitly) -- loopback so _same_origin_guard's Host check
    # (redraft.ui.app) doesn't 403 every request that doesn't override it by hand.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ui_app), base_url="http://127.0.0.1") as client:
        yield client


# -- S3a retrieval-layer fixtures ------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    """A fresh file-backed sqlite3 connection per test: sqlite-vec loaded, storage's
    nodes/edges/nodes_fts schema built (fixtures_ddl.build_fixture_db). File-backed (not
    :memory:) because WAL — the production journal mode — is unsupported for :memory: DBs;
    a real tmp file keeps this closer to how GraphStore actually opens its index."""
    db_path = tmp_path / "fixture.sqlite3"
    c = sqlite3.connect(str(db_path))
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    build_fixture_db(c)
    yield c
    c.close()


@pytest.fixture
def vec_ready_conn(conn, retrieval_config):
    """`conn` with the retrieval schema (embedding_meta/node_vectors/vec_nodes) already
    bootstrapped for retrieval_config's model/dims -- for tests exercising search/find_similar
    that aren't specifically testing ensure_embedding_schema's own bootstrap/invalidation
    behavior (those call it directly against the raw `conn` fixture instead)."""
    ensure_embedding_schema(conn, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
    return conn


@pytest.fixture(scope="session")
def retrieval_config() -> RetrievalConfig:
    return RetrievalConfig()


@pytest.fixture(scope="session", autouse=True)
def warm_embedder(retrieval_config):
    """Loads the real model once for the whole session and times it -- first-ever run
    downloads to ~/.cache/fastembed (~10s, mostly network); subsequent runs are a warm
    ONNX-session load from the on-disk cache (sub-second). Printed with -s so the number
    lands in CI/agent logs, per the task brief's "report timing" instruction."""
    t0 = time.time()
    get_embedder(retrieval_config)
    elapsed = time.time() - t0
    print(
        f"\n[warm_embedder] {retrieval_config.embedding_model_id} loaded in {elapsed:.3f}s "
        f"(cache_dir={retrieval_config.cache_dir})"
    )
    return retrieval_config


def insert_node(
    conn: sqlite3.Connection,
    id: str,
    type: str,
    title: str | None = None,
    body: str = "",
    status: str | None = None,
    properties: dict | None = None,
    created: str = "2026-01-01T00:00:00Z",
    updated: str = "2026-01-01T00:00:00Z",
) -> None:
    """Insert a synthetic nodes row directly (no GraphStore -- this slice does not import
    redraft.store). title defaults to id, matching the Obsidian-native convention
    that id IS the sanitized title in the common (no-illegal-characters) case."""
    conn.execute(
        "INSERT INTO nodes(id, type, title, body, status, properties, created, updated, content_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            id,
            type,
            title if title is not None else id,
            body,
            status,
            json.dumps(properties or {}),
            created,
            updated,
            hashlib.sha256(body.encode("utf-8")).hexdigest(),
        ),
    )
    conn.commit()


def insert_edge(conn: sqlite3.Connection, src: str, dst: str, type: str) -> None:
    edge_id = hashlib.sha256(f"{src}\x1f{dst}\x1f{type}".encode("utf-8")).hexdigest()
    conn.execute("INSERT INTO edges(id, src, dst, type) VALUES (?,?,?,?)", (edge_id, src, dst, type))
    conn.commit()
