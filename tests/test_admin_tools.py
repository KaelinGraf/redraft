"""reindex, snapshot -- thin delegation onto GraphStore.reindex()/.snapshot().

I7: runs against the real GraphStore (the stub and its sys.modules injection are gone).
reindex() here goes through the real projector against real node files on disk; the
"delete index dir, reindex, compare" Phase 0 guarantee itself is still S1's own gate test
(tests/test_index.py), this module only proves the tool plumbs GraphStore's ReindexStats
through correctly. snapshot() shells out to real git (see tests/test_gitops.py for the
git-operation-level gates); this module only proves message/push pass through and
CommitResult round-trips correctly.
"""
from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


async def _create(client: Client, type: str, title: str, **kw):
    result = await client.call_tool("create_node", {"type": type, "title": title, **kw})
    return result.data


async def test_reindex_returns_stats(mcp_server):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Indexed Node")
        result = await client.call_tool("reindex", {})
        assert result.data.scanned == 1
        assert result.data.deleted == 0
        assert result.data.malformed == []


async def test_snapshot_commits_and_returns_sha(mcp_server):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Snapshot Me")
        result = await client.call_tool("snapshot", {"message": "add a node"})
        assert result.data.committed is True
        assert result.data.sha is not None
        assert result.data.pushed is False


async def test_snapshot_is_noop_when_nothing_changed(mcp_server):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Only Change")
        first = await client.call_tool("snapshot", {"message": "first"})
        assert first.data.committed is True

        second = await client.call_tool("snapshot", {"message": "second, nothing new"})
        assert second.data.committed is False
        assert second.data.sha is None
        assert second.data.message == "nothing to commit"


async def test_snapshot_push_defaults_to_false_and_is_passed_through(mcp_server):
    """I7 (stub-vs-real drift): the stub simulated push=True as trivially succeeding.
    The real GraphStore shells out to a genuine `git push` (design-storage.md section
    7.2), which fails cleanly with git_operation_failed against a fresh repo with no
    configured remote -- decision #6's own documented, expected outcome. That failure is
    itself proof push=True was passed through and really attempted, not silently ignored;
    standing up a real git remote fixture just to observe pushed=True would test git
    itself, not this tool's plumbing."""
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Push Test")
        default_push = await client.call_tool("snapshot", {"message": "no push arg"})
        assert default_push.data.pushed is False

        await _create(client, "concept", "Push Test 2")
        with pytest.raises(ToolError, match="git_operation_failed"):
            await client.call_tool("snapshot", {"message": "with push", "push": True})
