"""Every write tool, including error mapping (pin R7: cycle_rejected,
collision, not_found in the raised ToolError's text) and the R2/R3 strict
create_edge / mechanical merge_nodes semantics. Client is opened fresh
inside each test (design-server.md section 13's documented FastMCP caveat:
event-loop issues if opened in a fixture), never in a fixture.
"""
from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


async def _create(client: Client, type: str, title: str, **kw):
    result = await client.call_tool("create_node", {"type": type, "title": title, **kw})
    return result.data


async def test_create_node_returns_node(mcp_server):
    async with Client(mcp_server) as client:
        node = await _create(client, "concept", "Point-cloud registration", body="Some prose.")
        assert node.id == "Point-cloud registration"
        assert node.type == "concept"
        assert node.body == "Some prose."
        assert node.status is None
        assert node.created == node.updated


async def test_create_node_empty_title_raises_invalid_argument(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("create_node", {"type": "concept", "title": ""})


async def test_create_node_whitespace_only_title_raises_invalid_argument(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("create_node", {"type": "concept", "title": "   "})


async def test_create_node_illegal_status_for_type_raises_invalid_argument(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("create_node", {"type": "concept", "title": "X", "status": "accepted"})


async def test_create_node_illegal_status_value_raises_invalid_argument(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("create_node", {"type": "decision", "title": "X", "status": "not_a_real_status"})


async def test_create_node_default_status_applied(mcp_server):
    async with Client(mcp_server) as client:
        node = await _create(client, "decision", "Fork GeoTransformer")
        assert node.status == "proposed"


async def test_create_node_collision_same_title_raises_collision(mcp_server):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Duplicate Title")
        with pytest.raises(ToolError, match="collision"):
            await client.call_tool("create_node", {"type": "concept", "title": "Duplicate Title"})


async def test_create_node_collision_is_case_insensitive(mcp_server):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Some Title")
        with pytest.raises(ToolError, match="collision"):
            await client.call_tool("create_node", {"type": "concept", "title": "SOME TITLE"})


async def test_update_node_append_mode_concatenates_body(mcp_server):
    async with Client(mcp_server) as client:
        node = await _create(client, "concept", "Appendable", body="first")
        result = await client.call_tool("update_node", {"id": node.id, "body": "second", "mode": "append"})
        assert result.data.body == "first\n\nsecond"
        assert result.data.updated >= node.updated


async def test_update_node_replace_mode_overwrites_body(mcp_server):
    async with Client(mcp_server) as client:
        node = await _create(client, "concept", "Replaceable", body="first")
        result = await client.call_tool("update_node", {"id": node.id, "body": "second", "mode": "replace"})
        assert result.data.body == "second"


async def test_update_node_properties_shallow_merge(mcp_server):
    async with Client(mcp_server) as client:
        node = await _create(client, "concept", "Props", properties={"a": 1, "b": 2})
        result = await client.call_tool("update_node", {"id": node.id, "properties": {"b": 3, "c": 4}})
        assert result.data.properties == {"a": 1, "b": 3, "c": 4}


async def test_update_node_missing_id_raises_not_found(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("update_node", {"id": "does-not-exist", "body": "x"})


async def test_update_node_remove_properties_deletes_a_key(mcp_server):
    async with Client(mcp_server) as client:
        node = await _create(client, "concept", "Removable Via MCP", properties={"a": 1, "b": 2})
        result = await client.call_tool("update_node", {"id": node.id, "remove_properties": ["a"]})
        assert result.data.properties == {"b": 2}


async def test_create_edge_success(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Parent Concept")
        b = await _create(client, "decision", "Child Decision")
        result = await client.call_tool("create_edge", {"src": b.id, "dst": a.id, "type": "part_of"})
        assert result.data.src == b.id
        assert result.data.dst == a.id
        assert result.data.dst_exists is True
        assert result.data.warnings == []


async def test_create_edge_nonexistent_dst_raises_not_found(mcp_server):
    """Pin R2: strict, no dangling-edge creation via the API."""
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Solo Node")
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("create_edge", {"src": a.id, "dst": "nonexistent", "type": "relates_to"})


async def test_create_edge_nonexistent_src_raises_not_found(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Solo Node 2")
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("create_edge", {"src": "nonexistent", "dst": a.id, "type": "relates_to"})


async def test_create_edge_second_part_of_parent_raises_collision(mcp_server):
    """Pin R2: second part_of parent is a collision telling the caller to delete_edge first, not a silent reparent."""
    async with Client(mcp_server) as client:
        parent1 = await _create(client, "concept", "Parent One")
        parent2 = await _create(client, "concept", "Parent Two")
        child = await _create(client, "decision", "Child")
        await client.call_tool("create_edge", {"src": child.id, "dst": parent1.id, "type": "part_of"})
        with pytest.raises(ToolError, match="collision") as exc_info:
            await client.call_tool("create_edge", {"src": child.id, "dst": parent2.id, "type": "part_of"})
        assert "delete_edge" in str(exc_info.value)


async def test_create_edge_part_of_cycle_raises_cycle_rejected(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "A")
        b = await _create(client, "concept", "B")
        c = await _create(client, "concept", "C")
        await client.call_tool("create_edge", {"src": b.id, "dst": a.id, "type": "part_of"})
        await client.call_tool("create_edge", {"src": c.id, "dst": b.id, "type": "part_of"})
        with pytest.raises(ToolError, match="cycle_rejected"):
            await client.call_tool("create_edge", {"src": a.id, "dst": c.id, "type": "part_of"})


async def test_create_edge_convention_violation_is_warning_not_error(mcp_server):
    async with Client(mcp_server) as client:
        wrong_src = await _create(client, "concept", "Not A Rationale")
        decision = await _create(client, "decision", "Some Decision")
        result = await client.call_tool("create_edge", {"src": wrong_src.id, "dst": decision.id, "type": "justifies"})
        assert result.data.warnings != []
        assert "convention" in result.data.warnings[0]


async def test_create_edge_idempotent_on_duplicate_call(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "A2")
        b = await _create(client, "concept", "B2")
        await client.call_tool("create_edge", {"src": a.id, "dst": b.id, "type": "relates_to"})
        result = await client.call_tool("create_edge", {"src": a.id, "dst": b.id, "type": "relates_to"})
        assert result.data.src == a.id
        neighbors = await client.call_tool("neighbors", {"id": a.id, "direction": "out"})
        assert len(neighbors.data) == 1


async def test_create_edges_batch_success_in_order(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Batch MCP Parent")
        b = await _create(client, "concept", "Batch MCP Ref")
        c = await _create(client, "idea", "Batch MCP Child")
        result = await client.call_tool(
            "create_edges",
            {
                "edges": [
                    {"src": c.id, "dst": a.id, "type": "part_of"},
                    {"src": c.id, "dst": b.id, "type": "references"},
                ]
            },
        )
        assert [(e.src, e.dst, e.type) for e in result.data] == [
            (c.id, a.id, "part_of"),
            (c.id, b.id, "references"),
        ]


async def test_create_edges_batch_atomic_failure_applies_nothing(mcp_server):
    """The individually-valid first edge must NOT be applied when a later edge in the same
    batch fails -- proof that create_edges (not a loop of create_edge) is atomic over MCP too."""
    async with Client(mcp_server) as client:
        a = await _create(client, "observation", "Batch MCP Atomic A")
        b = await _create(client, "artifact", "Batch MCP Atomic B")
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool(
                "create_edges",
                {
                    "edges": [
                        {"src": a.id, "dst": b.id, "type": "references"},
                        {"src": a.id, "dst": "nonexistent", "type": "relates_to"},
                    ]
                },
            )
        neighbors = await client.call_tool("neighbors", {"id": a.id, "direction": "out"})
        assert len(neighbors.data) == 0


async def test_delete_edge_existing_edge_reports_existed_true(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "A3")
        b = await _create(client, "concept", "B3")
        await client.call_tool("create_edge", {"src": a.id, "dst": b.id, "type": "relates_to"})
        result = await client.call_tool("delete_edge", {"src": a.id, "dst": b.id, "type": "relates_to"})
        assert result.data.ok is True
        assert result.data.existed is True


async def test_delete_edge_nonexistent_edge_is_idempotent(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "A4")
        b = await _create(client, "concept", "B4")
        result = await client.call_tool("delete_edge", {"src": a.id, "dst": b.id, "type": "relates_to"})
        assert result.data.ok is True
        assert result.data.existed is False


async def test_delete_edge_survives_nonexistent_dst_dangling_repair(mcp_server):
    """A3 (design amendment, stub-vs-real reconciliation): delete_edge only requires src to
    exist -- dst may already be gone. This is exactly the dangling-edge repair loop's use
    case: you cannot clean up a reference to a node that's already deleted if delete_edge
    itself insists the deleted node still exists."""
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "A5")
        b = await _create(client, "concept", "B5")
        await client.call_tool("create_edge", {"src": a.id, "dst": b.id, "type": "relates_to"})
        await client.call_tool("delete_node", {"id": b.id})  # b's file is gone; a's edge to it now dangles

        result = await client.call_tool("delete_edge", {"src": a.id, "dst": b.id, "type": "relates_to"})
        assert result.data.ok is True
        assert result.data.existed is True  # the dangling edge row itself did exist, pre-delete

        neighbors = await client.call_tool("neighbors", {"id": a.id, "direction": "out"})
        assert neighbors.data == []


async def test_delete_node_reports_orphaned_inbound_edges(mcp_server):
    async with Client(mcp_server) as client:
        parent = await _create(client, "concept", "Parent To Delete")
        child = await _create(client, "decision", "Child Pointing At Parent")
        await client.call_tool("create_edge", {"src": child.id, "dst": parent.id, "type": "part_of"})
        result = await client.call_tool("delete_node", {"id": parent.id})
        assert result.data.existed is True
        assert len(result.data.orphaned_inbound_edges) == 1
        assert result.data.orphaned_inbound_edges[0].src == child.id


async def test_delete_node_missing_id_raises_not_found(mcp_server):
    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("delete_node", {"id": "does-not-exist"})


async def test_merge_nodes_keep_equals_drop_raises_invalid_argument(mcp_server):
    async with Client(mcp_server) as client:
        a = await _create(client, "concept", "Solo For Merge")
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("merge_nodes", {"keep_id": a.id, "drop_id": a.id})


async def test_merge_nodes_relinks_inbound_and_migrates_outbound(mcp_server):
    """I4/I7: MergeResult no longer predicts relinked_inbound/migrated_outbound (GraphStore's
    real MergeOutcome doesn't report per-edge detail, only the net semantic result) -- the
    same facts are verified here by observing the graph after the call instead."""
    async with Client(mcp_server) as client:
        keep = await _create(client, "concept", "Keep Me")
        drop = await _create(client, "concept", "Drop Me")
        referrer = await _create(client, "decision", "Referrer")
        target = await _create(client, "artifact", "Migrated Target")
        await client.call_tool("create_edge", {"src": referrer.id, "dst": drop.id, "type": "relates_to"})
        await client.call_tool("create_edge", {"src": drop.id, "dst": target.id, "type": "references"})

        result = await client.call_tool("merge_nodes", {"keep_id": keep.id, "drop_id": drop.id})
        assert result.data.kept.id == keep.id
        assert result.data.dropped_id == drop.id

        referrer_out = await client.call_tool("neighbors", {"id": referrer.id, "direction": "out"})
        assert referrer_out.data[0].dst == keep.id  # inbound edge relinked drop -> keep

        keep_out = await client.call_tool("neighbors", {"id": keep.id, "direction": "out"})
        assert [(e.dst, e.type) for e in keep_out.data] == [(target.id, "references")]  # outbound migrated

        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("get_node", {"id": drop.id})


async def test_merge_nodes_outbound_self_loop_and_points_at_keep_are_dropped_not_migrated(mcp_server):
    """Regression test for a bug found while reviewing the merge algorithm:
    migrating drop's own outbound edges verbatim would either dangle
    (drop's self-loop, dst=drop_id, about to be deleted) or manufacture a
    surprise keep->keep self-loop (drop pointed at keep) -- both dropped
    instead, symmetric with the documented inbound self-loop guard."""
    async with Client(mcp_server) as client:
        keep = await _create(client, "concept", "Keep Outbound Guard")
        drop = await _create(client, "concept", "Drop Outbound Guard")
        await client.call_tool("create_edge", {"src": drop.id, "dst": drop.id, "type": "relates_to"})
        await client.call_tool("create_edge", {"src": drop.id, "dst": keep.id, "type": "relates_to"})

        result = await client.call_tool("merge_nodes", {"keep_id": keep.id, "drop_id": drop.id})
        assert result.data.kept.id == keep.id

        out_edges = await client.call_tool("neighbors", {"id": keep.id, "direction": "out"})
        assert out_edges.data == []  # neither edge was migrated: no self-loop was manufactured on keep


async def test_merge_nodes_self_loop_guard_excludes_keep_from_relinked(mcp_server):
    """design-storage.md 6.4 step 4: if keep already referenced drop, that
    edge is dropped, not rewritten into a keep->keep self-loop."""
    async with Client(mcp_server) as client:
        keep = await _create(client, "concept", "Keep Self")
        drop = await _create(client, "concept", "Drop Self")
        await client.call_tool("create_edge", {"src": keep.id, "dst": drop.id, "type": "relates_to"})

        result = await client.call_tool("merge_nodes", {"keep_id": keep.id, "drop_id": drop.id})
        assert result.data.kept.id == keep.id

        # keep's only outbound edge (keep->drop) must be dropped by the self-loop guard, not
        # rewritten into a keep->keep self-loop.
        neighbors = await client.call_tool("neighbors", {"id": keep.id, "direction": "out"})
        assert neighbors.data == []


async def test_merge_nodes_part_of_conflict_is_warning_not_failure(mcp_server):
    """Pin R3: a part_of conflict during merge is a warning, never a failure."""
    async with Client(mcp_server) as client:
        keep_parent = await _create(client, "concept", "Keep Parent")
        drop_parent = await _create(client, "concept", "Drop Parent")
        keep = await _create(client, "decision", "Keep With Parent")
        drop = await _create(client, "decision", "Drop With Parent")
        await client.call_tool("create_edge", {"src": keep.id, "dst": keep_parent.id, "type": "part_of"})
        await client.call_tool("create_edge", {"src": drop.id, "dst": drop_parent.id, "type": "part_of"})

        result = await client.call_tool("merge_nodes", {"keep_id": keep.id, "drop_id": drop.id})
        assert result.data.warnings != []
        assert "part_of" in result.data.warnings[0]

        node = await client.call_tool("get_node", {"id": keep.id, "neighbor_depth": 1})
        part_of_edges = [e for e in node.data.edges if e.type == "part_of" and e.direction == "out"]
        assert part_of_edges[0].dst == keep_parent.id  # unchanged, not overwritten by drop's parent


async def test_merge_nodes_body_not_auto_merged_preview_truncated(mcp_server):
    """Pin R3: body is NOT auto-merged; dropped_body_preview surfaces first 280 chars."""
    async with Client(mcp_server) as client:
        keep = await _create(client, "concept", "Keep Body")
        long_body = "x" * 400
        drop = await _create(client, "concept", "Drop Body", body=long_body)

        result = await client.call_tool("merge_nodes", {"keep_id": keep.id, "drop_id": drop.id})
        assert result.data.dropped_body_preview == long_body[:280]
        assert len(result.data.dropped_body_preview) == 280

        kept = await client.call_tool("get_node", {"id": keep.id})
        assert kept.data.node.body == ""  # drop's body was never merged in


async def test_rename_node_relinks_inbound_edges(mcp_server):
    async with Client(mcp_server) as client:
        target = await _create(client, "concept", "Old Name")
        referrer = await _create(client, "decision", "Points At Old Name")
        await client.call_tool("create_edge", {"src": referrer.id, "dst": target.id, "type": "relates_to"})

        result = await client.call_tool("rename_node", {"id": target.id, "new_title": "New Name"})
        assert result.data.old_id == "Old Name"
        assert result.data.new_id == "New Name"
        assert [e.src for e in result.data.relinked] == [referrer.id]

        neighbors = await client.call_tool("neighbors", {"id": referrer.id, "direction": "out"})
        assert neighbors.data[0].dst == "New Name"
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("get_node", {"id": "Old Name"})


async def test_rename_node_self_referencing_edge_becomes_clean_self_loop(mcp_server):
    """Regression test for a bug found while reviewing tests/_stub_store.py:
    a node with a self-referencing edge (src=id, dst=id) must end up with
    exactly one self-loop edge (new_id, new_id) after rename -- not that
    self-loop *plus* a dangling (new_id, old_id) leftover."""
    async with Client(mcp_server) as client:
        node = await _create(client, "concept", "Self Referencing")
        await client.call_tool("create_edge", {"src": node.id, "dst": node.id, "type": "relates_to"})

        result = await client.call_tool("rename_node", {"id": node.id, "new_title": "Self Referencing Renamed"})
        new_id = result.data.new_id

        out_edges = await client.call_tool("neighbors", {"id": new_id, "direction": "out"})
        assert [(e.src, e.dst, e.type) for e in out_edges.data] == [(new_id, new_id, "relates_to")]
        in_edges = await client.call_tool("neighbors", {"id": new_id, "direction": "in"})
        assert [(e.src, e.dst, e.type) for e in in_edges.data] == [(new_id, new_id, "relates_to")]


async def test_rename_node_collision_raises_collision(mcp_server):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Taken Name")
        victim = await _create(client, "concept", "Victim Name")
        with pytest.raises(ToolError, match="collision"):
            await client.call_tool("rename_node", {"id": victim.id, "new_title": "Taken Name"})


async def test_rename_node_empty_new_title_raises_invalid_argument(mcp_server):
    async with Client(mcp_server) as client:
        victim = await _create(client, "concept", "Rename Me Empty")
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("rename_node", {"id": victim.id, "new_title": "   "})


async def test_rename_node_flags_body_references_not_updated(mcp_server):
    """Inline [[wikilinks]] in body prose are not rewritten -- flagged, not silently left unmentioned."""
    async with Client(mcp_server) as client:
        target = await _create(client, "concept", "Mentioned In Prose")
        mentioner = await _create(client, "observation", "Mentions It", body="See [[Mentioned In Prose]] for details.")

        result = await client.call_tool("rename_node", {"id": target.id, "new_title": "Renamed Now"})
        assert mentioner.id in result.data.body_references_not_updated

        still_stale = await client.call_tool("get_node", {"id": mentioner.id})
        assert "[[Mentioned In Prose]]" in still_stale.data.node.body  # confirmed NOT rewritten
