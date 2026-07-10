"""The four graph:// resources. See resources.py's module docstring for the
confirmed-live McpError-vs-ToolError distinction between resource-read
errors and tool-call errors -- these tests assert McpError, not ToolError.
"""
from __future__ import annotations

import dataclasses
import json
import urllib.parse

import pytest
from fastmcp import Client
from mcp.shared.exceptions import McpError


async def _create(client: Client, type: str, title: str, **kw):
    result = await client.call_tool("create_node", {"type": type, "title": title, **kw})
    return result.data


def _node_uri(node_id: str) -> str:
    return "graph://project/node/" + urllib.parse.quote(node_id, safe="")


async def test_root_resource_lists_root_nodes_and_counts(mcp_server):
    async with Client(mcp_server) as client:
        root = await _create(client, "concept", "Resource Root")
        child = await _create(client, "decision", "Resource Child", status="accepted")
        await client.call_tool("create_edge", {"src": child.id, "dst": root.id, "type": "part_of"})

        contents = await client.read_resource("graph://project/root")
        payload = json.loads(contents[0].text)
        assert [r["id"] for r in payload["roots"]] == [root.id]  # child has a part_of parent, excluded
        assert payload["counts_by_type"] == {"concept": 1, "decision": 1}
        assert payload["counts_by_status"] == {"accepted": 1}


async def test_node_resource_mirrors_get_node_depth_zero(mcp_server):
    async with Client(mcp_server) as client:
        node = await _create(client, "concept", "Point-cloud registration", body="prose")

        contents = await client.read_resource(_node_uri(node.id))
        payload = json.loads(contents[0].text)
        assert payload["node"]["id"] == node.id
        assert payload["node"]["body"] == "prose"
        assert payload["neighbors"] == []
        assert payload["edges"] == []


async def test_node_resource_missing_node_raises_mcp_error(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(McpError, match="not_found"):
            await client.read_resource(_node_uri("does-not-exist"))


async def test_stats_resource_counts_orphans_and_dangling(mcp_server):
    async with Client(mcp_server) as client:
        orphan = await _create(client, "concept", "Isolated Node")
        parent = await _create(client, "concept", "Stats Parent")
        child = await _create(client, "decision", "Stats Child")
        await client.call_tool("create_edge", {"src": child.id, "dst": parent.id, "type": "part_of"})
        await client.call_tool("delete_node", {"id": parent.id})  # leaves child's part_of edge dangling

        contents = await client.read_resource("graph://project/stats")
        payload = json.loads(contents[0].text)
        assert payload["orphan_count"] == 1  # only the untouched `orphan` node
        assert payload["dangling_edge_count"] == 1  # child's part_of edge, now pointing at a deleted parent


async def test_overview_resource_and_tool_return_the_same_structure(mcp_server):
    """The `overview` MCP tool (tools/report_tools.py) and graph://project/overview both call
    report.overview() through their own independent connection -- this checks they actually
    agree, then spot-checks the shape against a small root/branch/gap graph.

    fastmcp's Client deserializes a tool result purely from its JSON output SCHEMA (it never
    sees our actual ProjectOverview class) -- for a plain BaseModel with the pydantic default
    additionalProperties: false, that schema-driven path produces a dataclass, not a pydantic
    model (fastmcp.utilities.json_schema_type._object_schema_to_type, case 4), so this reads
    tool_data via plain attribute access / dataclasses.asdict(), never .model_dump().
    """
    async with Client(mcp_server) as client:
        root = await _create(client, "concept", "Overview Root")
        branch = await _create(client, "concept", "Overview Branch", body="Shape of the thing.")
        await client.call_tool("create_edge", {"src": branch.id, "dst": root.id, "type": "part_of"})
        question = await _create(client, "question", "Overview Open Question", status="open")
        await client.call_tool("create_edge", {"src": question.id, "dst": branch.id, "type": "part_of"})
        decision = await _create(client, "decision", "Overview Unjustified Decision", status="proposed")
        await client.call_tool("create_edge", {"src": decision.id, "dst": branch.id, "type": "part_of"})
        # parentless AND childless -- excluded from roots, tallied in floating_by_type instead
        # (report.overview()'s spine-roots-only contract); included here so the new field is
        # actually exercised by this cross-check, not left at its trivial {} default.
        await _create(client, "rationale", "Overview Floating Rationale")

        tool_data = (await client.call_tool("overview", {})).data
        contents = await client.read_resource("graph://project/overview")
        resource_payload = json.loads(contents[0].text)

    assert dataclasses.asdict(tool_data) == resource_payload

    assert [r.id for r in tool_data.roots] == [root.id]
    assert tool_data.floating_by_type == {"rationale": 1}
    assert resource_payload["floating_by_type"] == {"rationale": 1}
    branch_out = tool_data.roots[0].branches[0]
    assert branch_out.id == branch.id
    assert branch_out.descendant_count == 2
    assert branch_out.open_question_count == 1
    assert branch_out.unjustified_decision_count == 1
    assert branch_out.excerpt == "Shape of the thing."
    assert any(q.id == question.id for q in tool_data.top_open_questions)
