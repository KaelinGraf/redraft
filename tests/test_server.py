"""build_server() wiring, ServerConfig, and the pin-R8 fail-fast startup check."""
from __future__ import annotations

import sqlite3

import pytest
from fastmcp import Client

import redraft.server as server_mod
from redraft.server import REDRAFT_DIR_ENV, ServerConfig, build_server


def test_server_config_from_env_missing_var_raises(monkeypatch):
    monkeypatch.delenv(REDRAFT_DIR_ENV, raising=False)
    with pytest.raises(RuntimeError, match=REDRAFT_DIR_ENV):
        ServerConfig.from_env()


def test_server_config_from_env_reads_graph_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(REDRAFT_DIR_ENV, str(tmp_path))
    config = ServerConfig.from_env()
    assert config.graph_dir == tmp_path


async def test_build_server_registers_exactly_the_implemented_tools(mcp_server):
    async with Client(mcp_server) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {
        "create_node", "update_node", "create_edge", "create_edges", "delete_edge",
        "delete_node", "merge_nodes", "rename_node",
        "get_node", "neighbors", "get_subgraph",
        "reindex", "snapshot",
        # S3b: search/find_similar (hybrid retrieval) + the seven integrity/hygiene tools.
        "search_nodes", "find_similar",
        "decisions_without_rationale", "open_questions", "orphans", "contradictions",
        "stale", "dangling_edges", "case_collisions",
        # S4b: report assembly + topic briefing.
        "assemble_report", "briefing",
        # session-start overview: cheap shallow project map.
        "overview",
    }


async def test_build_server_registers_resources_and_prompt(mcp_server):
    async with Client(mcp_server) as client:
        resource_uris = {str(r.uri) for r in await client.list_resources()}
        template_uris = {t.uriTemplate for t in await client.list_resource_templates()}
        prompt_names = {p.name for p in await client.list_prompts()}
    assert resource_uris == {"graph://project/root", "graph://project/stats", "graph://project/overview"}
    assert template_uris == {"graph://project/node/{node_id}"}
    assert prompt_names == {"organizing_protocol"}


class _FakeConnNoFTS5:
    def execute(self, sql):
        raise sqlite3.OperationalError("no such module: fts5")

    def close(self):
        pass


class _FakeConnNoExtensionLoading:
    def execute(self, sql):
        pass  # FTS5 check passes

    def enable_load_extension(self, flag):
        raise sqlite3.NotSupportedError("extension loading not supported by this build")

    def close(self):
        pass


class _FakeConnNoSqliteVec:
    def execute(self, sql):
        pass  # FTS5 check passes

    def enable_load_extension(self, flag):
        pass  # toggle itself succeeds; only the real extension load below fails

    def load_extension(self, path):
        raise sqlite3.OperationalError(f"{path}.so: cannot open shared object file")

    def close(self):
        pass


def test_check_sqlite_capabilities_fails_fast_without_fts5(monkeypatch):
    monkeypatch.setattr(server_mod.sqlite3, "connect", lambda *a, **kw: _FakeConnNoFTS5())
    with pytest.raises(RuntimeError, match="FTS5"):
        server_mod._check_sqlite_capabilities()


def test_check_sqlite_capabilities_fails_fast_without_extension_loading(monkeypatch):
    monkeypatch.setattr(server_mod.sqlite3, "connect", lambda *a, **kw: _FakeConnNoExtensionLoading())
    with pytest.raises(RuntimeError, match="extension loading"):
        server_mod._check_sqlite_capabilities()


def test_check_sqlite_capabilities_fails_fast_without_sqlite_vec(monkeypatch):
    monkeypatch.setattr(server_mod.sqlite3, "connect", lambda *a, **kw: _FakeConnNoSqliteVec())
    with pytest.raises(RuntimeError, match="sqlite-vec"):
        server_mod._check_sqlite_capabilities()


def test_check_sqlite_capabilities_passes_on_this_environment():
    server_mod._check_sqlite_capabilities()  # must not raise


def test_build_server_calls_the_capability_check_before_constructing_anything(monkeypatch, graph_dir):
    calls = []

    def _blocked():
        calls.append(1)
        raise RuntimeError("blocked")

    monkeypatch.setattr(server_mod, "_check_sqlite_capabilities", _blocked)
    with pytest.raises(RuntimeError, match="blocked"):
        build_server(ServerConfig(graph_dir=graph_dir))
    assert calls == [1]
