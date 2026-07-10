"""get_node, neighbors, get_subgraph -- the recursive-CTE traversal (design-
server.md section 7) run directly against the index DB. Client opened fresh
inside each test, never in a fixture.
"""
from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


async def _create(client: Client, type: str, title: str, **kw):
    result = await client.call_tool("create_node", {"type": type, "title": title, **kw})
    return result.data


async def test_get_node_default_depth_has_no_neighbors(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Lonely")
        result = await client.call_tool("get_node", {"id": a.id})
        assert result.data.node.id == a.id
        assert result.data.neighbors == []
        assert result.data.edges == []


async def test_get_node_missing_raises_not_found(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("get_node", {"id": "does-not-exist"})


async def test_get_node_negative_depth_raises_invalid_argument(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Depth Guard")
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("get_node", {"id": a.id, "neighbor_depth": -1})


async def test_get_node_with_neighbor_depth_one(mcp_server):
    async with Client(mcp_server) as client:
        parent = await _create(client, "concept", "Registration")
        child = await _create(client, "decision", "Fork Backbone")
        await client.call_tool("create_edge", {"src": child.id, "dst": parent.id, "type": "part_of"})

        result = await client.call_tool("get_node", {"id": parent.id, "neighbor_depth": 1})
        assert result.data.node.id == parent.id
        assert {n.id for n in result.data.neighbors} == {child.id}
        assert len(result.data.edges) == 1
        edge = result.data.edges[0]
        assert (edge.src, edge.dst, edge.type, edge.direction) == (child.id, parent.id, "part_of", "in")


async def test_get_node_neighbor_depth_reaches_multiple_hops(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Hop A")
        b = await _create(client, "concept", "Hop B")
        c = await _create(client, "concept", "Hop C")
        await client.call_tool("create_edge", {"src": b.id, "dst": a.id, "type": "part_of"})
        await client.call_tool("create_edge", {"src": c.id, "dst": b.id, "type": "part_of"})

        one_hop = await client.call_tool("get_node", {"id": a.id, "neighbor_depth": 1})
        assert {n.id for n in one_hop.data.neighbors} == {b.id}

        two_hop = await client.call_tool("get_node", {"id": a.id, "neighbor_depth": 2})
        assert {n.id for n in two_hop.data.neighbors} == {b.id, c.id}


async def test_neighbors_direction_filtering(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Center")
        out_target = await _create(client, "artifact", "Out Target")
        in_source = await _create(client, "decision", "In Source")
        await client.call_tool("create_edge", {"src": a.id, "dst": out_target.id, "type": "references"})
        await client.call_tool("create_edge", {"src": in_source.id, "dst": a.id, "type": "relates_to"})

        out_only = await client.call_tool("neighbors", {"id": a.id, "direction": "out"})
        assert [e.dst for e in out_only.data] == [out_target.id]
        assert out_only.data[0].direction == "out"

        in_only = await client.call_tool("neighbors", {"id": a.id, "direction": "in"})
        assert [e.src for e in in_only.data] == [in_source.id]
        assert in_only.data[0].direction == "in"

        both = await client.call_tool("neighbors", {"id": a.id, "direction": "both"})
        assert len(both.data) == 2


async def test_neighbors_filtered_by_edge_type(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Typed Center")
        t1 = await _create(client, "artifact", "T1")
        t2 = await _create(client, "artifact", "T2")
        await client.call_tool("create_edge", {"src": a.id, "dst": t1.id, "type": "references"})
        await client.call_tool("create_edge", {"src": a.id, "dst": t2.id, "type": "relates_to"})

        result = await client.call_tool("neighbors", {"id": a.id, "edge_types": ["references"], "direction": "out"})
        assert [e.dst for e in result.data] == [t1.id]


async def test_neighbors_explicit_empty_edge_types_matches_nothing(mcp_server):
    """Regression test for a bug found while reviewing the filter logic:
    edge_types=[] (explicit empty list) must match nothing, distinct from
    edge_types=None (no filter) -- matching design-server.md section 6's own
    `types is None or x in types` pattern, where `x in []` is always False."""
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Empty Filter Center")
        t1 = await _create(client, "artifact", "Empty Filter T1")
        await client.call_tool("create_edge", {"src": a.id, "dst": t1.id, "type": "references"})

        result = await client.call_tool("neighbors", {"id": a.id, "edge_types": [], "direction": "out"})
        assert result.data == []

        no_filter = await client.call_tool("neighbors", {"id": a.id, "direction": "out"})
        assert len(no_filter.data) == 1  # confirms omitting edge_types is NOT the same as []


async def test_neighbors_missing_id_raises_not_found(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("neighbors", {"id": "does-not-exist"})


async def test_get_subgraph_returns_nodes_and_edges(mcp_server):
    async with Client(mcp_server) as client:
        root = await _create(client, "concept", "Subgraph Root")
        child = await _create(client, "decision", "Subgraph Child")
        grandchild = await _create(client, "rationale", "Subgraph Grandchild")
        await client.call_tool("create_edge", {"src": child.id, "dst": root.id, "type": "part_of"})
        await client.call_tool("create_edge", {"src": grandchild.id, "dst": child.id, "type": "justifies"})

        result = await client.call_tool("get_subgraph", {"root_id": root.id, "depth": 3})
        node_ids = {n.id for n in result.data.nodes}
        assert node_ids == {root.id, child.id, grandchild.id}
        assert len(result.data.edges) == 2


async def test_get_subgraph_respects_depth_cap(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Cap A")
        b = await _create(client, "concept", "Cap B")
        c = await _create(client, "concept", "Cap C")
        await client.call_tool("create_edge", {"src": b.id, "dst": a.id, "type": "part_of"})
        await client.call_tool("create_edge", {"src": c.id, "dst": b.id, "type": "part_of"})

        result = await client.call_tool("get_subgraph", {"root_id": a.id, "depth": 1})
        node_ids = {n.id for n in result.data.nodes}
        assert node_ids == {a.id, b.id}
        assert c.id not in node_ids


async def test_get_subgraph_filtered_by_edge_type(mcp_server):
    async with Client(mcp_server) as client:
        root = await _create(client, "concept", "Filter Root")
        part_child = await _create(client, "decision", "Filter Part Child")
        related = await _create(client, "idea", "Filter Related")
        await client.call_tool("create_edge", {"src": part_child.id, "dst": root.id, "type": "part_of"})
        await client.call_tool("create_edge", {"src": root.id, "dst": related.id, "type": "relates_to"})

        result = await client.call_tool("get_subgraph", {"root_id": root.id, "edge_types": ["part_of"], "depth": 3})
        node_ids = {n.id for n in result.data.nodes}
        assert node_ids == {root.id, part_child.id}
        assert related.id not in node_ids


async def test_get_subgraph_explicit_empty_edge_types_returns_only_root(mcp_server):
    async with Client(mcp_server) as client:
        root = await _create(client, "concept", "Empty Filter Subgraph Root")
        child = await _create(client, "decision", "Empty Filter Subgraph Child")
        await client.call_tool("create_edge", {"src": child.id, "dst": root.id, "type": "part_of"})

        result = await client.call_tool("get_subgraph", {"root_id": root.id, "edge_types": [], "depth": 3})
        assert [n.id for n in result.data.nodes] == [root.id]
        assert result.data.edges == []


async def test_get_subgraph_negative_depth_raises_invalid_argument(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Neg Depth")
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("get_subgraph", {"root_id": a.id, "depth": -1})


async def test_get_subgraph_missing_root_raises_not_found(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("get_subgraph", {"root_id": "does-not-exist"})


async def test_traversal_terminates_on_a_cycle(mcp_server):
    """part_of cycles are rejected at write time, but other edge types can
    legitimately form a cycle (e.g. two nodes relates_to each other). The
    path-guarded recursive CTE (design-server.md section 7) must terminate,
    not hang or error -- this test times out (via the test runner itself) if
    it doesn't."""
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Cycle A")
        b = await _create(client, "concept", "Cycle B")
        await client.call_tool("create_edge", {"src": a.id, "dst": b.id, "type": "relates_to"})
        await client.call_tool("create_edge", {"src": b.id, "dst": a.id, "type": "relates_to"})

        result = await client.call_tool("get_subgraph", {"root_id": a.id, "depth": 10})
        assert {n.id for n in result.data.nodes} == {a.id, b.id}
