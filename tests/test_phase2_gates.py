"""Phase 2 acceptance gates (brief section 8), driven entirely over an in-memory FastMCP
Client against a real, real-model-backed server -- no stubs, no direct GraphStore/library
calls. Client is opened fresh inside each test, never in a fixture (same documented FastMCP
event-loop caveat test_write_tools.py's module docstring calls out).

(a)-(d) below map 1:1 onto the brief's own four Phase 2 acceptance bullets. The write-path
integration tests that follow prove the embedding seam wired into GraphStore (S3b scope) is
actually load-bearing for those four: create/delete/rename/merge all keep node_vectors in
sync, synchronously, inside the write lock.

Fixture content for (a)/(b) is reused verbatim from tests/test_hybrid_search.py, whose
rankings were already verified empirically against the real bge-small-en-v1.5 model --
reusing proven fixtures avoids re-verifying new ranking assertions from scratch.
"""
from __future__ import annotations

import time

from fastmcp import Client

from redraft.retrieval.embeddings import RetrievalConfig
from redraft.server import ServerConfig, build_server
from redraft.tools.read_tools import index_read_conn
from redraft.tools.retrieval_tools import open_conn

# Mirrors tests/test_vector_index.py's SWAP_MODEL_ID/DIMS -- a second real model with a
# *different* vector width (512 vs bge-small's 384), already warm in ~/.cache/fastembed, so
# the model-swap gate proves a genuine re-embed at a new width, not a relabeled model string.
SWAP_MODEL_ID = "jinaai/jina-embeddings-v2-small-en"
SWAP_MODEL_DIMS = 512


async def _create(client: Client, type: str, title: str, **kw):
    result = await client.call_tool("create_node", {"type": type, "title": title, **kw})
    return result.data


# -- (a) find_similar surfaces a paraphrased near-duplicate over MCP ------------------------


async def test_a_find_similar_surfaces_paraphrase_over_mcp(mcp_server):
    async with Client(mcp_server) as client:
        await _create(
            client, "concept", "GPU Requirement",
            body="The project requires an NVIDIA GPU with compute capability sm_120 to support Blackwell.",
        )
        await _create(
            client, "concept", "Compute Capability Note",
            body="We need a graphics card supporting the sm_120 architecture generation for our CUDA kernels.",
        )
        await _create(client, "concept", "Unrelated Topic", body="The weather today is sunny with a light breeze.")

        hits = (await client.call_tool("find_similar", {"text_or_id": "GPU Requirement", "k": 2})).data

    assert hits != []
    assert hits[0].node.id == "Compute Capability Note"
    assert all(h.node.id != "GPU Requirement" for h in hits)  # exact-id match excludes self
    assert all(h.matched_vector and not h.matched_fts for h in hits)


# -- (b) search_nodes finds a node by exact identifier in its body (sm_120) -----------------


async def test_b_search_nodes_finds_exact_identifier_over_mcp(mcp_server):
    async with Client(mcp_server) as client:
        await _create(
            client, "decision", "Backbone Choice", status="accepted",
            body="We picked sm_120 as the minimum compute capability target for the registration backbone.",
        )
        await _create(client, "concept", "Cooking Notes", body="Simmer the sauce for twenty minutes then add fresh basil.")
        await _create(
            client, "concept", "Travel Plan", body="We are flying to Tokyo next month for the robotics conference."
        )

        hits = (await client.call_tool("search_nodes", {"query": "sm_120", "k": 5})).data

    assert hits[0].node.id == "Backbone Choice"
    assert hits[0].matched_fts is True


# -- (c) decisions_without_rationale and orphans return correct sets over MCP ---------------


async def test_c_decisions_without_rationale_and_orphans_over_mcp(mcp_server):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Root Concept")
        await _create(client, "decision", "Decision With Rationale", status="accepted")
        await client.call_tool("create_edge", {"src": "Decision With Rationale", "dst": "Root Concept", "type": "part_of"})
        await _create(client, "rationale", "Rationale One")
        await client.call_tool("create_edge", {"src": "Rationale One", "dst": "Decision With Rationale", "type": "justifies"})
        # Anchored by part_of, like every other non-orphan fixture node here -- otherwise
        # it would ALSO (correctly) show up in orphans(), which is not what this test is
        # isolating: decisions_without_rationale and orphans are independent conditions.
        await _create(client, "decision", "Decision Without Rationale", status="proposed")
        await client.call_tool("create_edge", {"src": "Decision Without Rationale", "dst": "Root Concept", "type": "part_of"})
        await _create(client, "idea", "Orphan Idea")  # deliberately zero edges

        without_rationale = (await client.call_tool("decisions_without_rationale", {})).data
        orphan_nodes = (await client.call_tool("orphans", {})).data

    assert {n.id for n in without_rationale} == {"Decision Without Rationale"}
    assert {n.id for n in orphan_nodes} == {"Orphan Idea"}


# -- (d) model swap: rebuild server with a different embedding config, reindex, re-embed ----


async def test_d_model_swap_reembeds_all_and_find_similar_still_works(graph_dir):
    fixtures = {
        "Cats": "Cats are small domesticated felines.",
        "Dogs": "Dogs are loyal domesticated canines.",
        "Rockets": "Rockets use combustion to reach orbit.",
    }
    server1 = build_server(ServerConfig(graph_dir=graph_dir))
    async with Client(server1) as client:
        for nid, body in fixtures.items():
            await _create(client, "concept", nid, body=body)

    default_dims = RetrievalConfig().embedding_dims
    with index_read_conn(graph_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == len(fixtures)
        assert conn.execute("SELECT model, dims FROM embedding_meta WHERE id=1").fetchone() == (
            RetrievalConfig().embedding_model_id, default_dims,
        )

    swapped_config = RetrievalConfig(embedding_model_id=SWAP_MODEL_ID, embedding_dims=SWAP_MODEL_DIMS)
    t0 = time.time()
    server2 = build_server(ServerConfig(graph_dir=graph_dir, retrieval_config=swapped_config))
    build_elapsed = time.time() - t0

    async with Client(server2) as client:
        t1 = time.time()
        reindex_result = await client.call_tool("reindex", {})
        reindex_elapsed = time.time() - t1
        assert reindex_result.data.scanned == len(fixtures)

        hits = (await client.call_tool("find_similar", {"text_or_id": "small furry pet animal", "k": 3})).data

    print(
        f"\n[model_swap] build_server(new model, incl. implicit reindex+re-embed of "
        f"{len(fixtures)} nodes): {build_elapsed:.3f}s; explicit reindex tool call after: "
        f"{reindex_elapsed:.3f}s"
    )

    assert hits[0].node.id == "Cats"
    with index_read_conn(graph_dir) as conn:
        assert conn.execute("SELECT model, dims FROM embedding_meta WHERE id=1").fetchone() == (
            SWAP_MODEL_ID, SWAP_MODEL_DIMS,
        )
        assert conn.execute("SELECT COUNT(*) FROM node_vectors").fetchone()[0] == len(fixtures)
    # vec_nodes is a sqlite-vec virtual table -- index_read_conn (above) never loads that
    # extension (read_tools.py's plain nodes/edges queries don't need it); open_conn does.
    with open_conn(graph_dir) as conn:
        for nid in fixtures:
            (vec_blob,) = conn.execute(
                "SELECT v.embedding FROM node_vectors nv JOIN vec_nodes v ON v.vec_rowid = nv.vec_rowid "
                "WHERE nv.node_id = ?",
                (nid,),
            ).fetchone()
            assert len(vec_blob) == SWAP_MODEL_DIMS * 4  # float32 = 4 bytes/dim


# -- write-path integration: the embedding seam itself -------------------------------------


async def test_create_over_mcp_is_immediately_find_similar_able(mcp_server, graph_dir):
    async with Client(mcp_server) as client:
        await _create(
            client, "concept", "Zzyzx Widget",
            body="The zzyzx quantum widget requires calibration before its first use.",
        )
        with index_read_conn(graph_dir) as conn:
            assert conn.execute("SELECT COUNT(*) FROM node_vectors WHERE node_id='Zzyzx Widget'").fetchone()[0] == 1

        hits = (
            await client.call_tool(
                "find_similar", {"text_or_id": "a zzyzx quantum widget that needs calibration", "k": 3}
            )
        ).data
    assert any(h.node.id == "Zzyzx Widget" for h in hits)


async def test_delete_over_mcp_removes_vector_row(mcp_server, graph_dir):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Doomed Node", body="This node will be deleted shortly.")
        with index_read_conn(graph_dir) as conn:
            assert conn.execute("SELECT COUNT(*) FROM node_vectors WHERE node_id='Doomed Node'").fetchone()[0] == 1

        await client.call_tool("delete_node", {"id": "Doomed Node"})

    with index_read_conn(graph_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM node_vectors WHERE node_id='Doomed Node'").fetchone()[0] == 0


async def test_rename_over_mcp_reembeds_renamed_node_not_referrers(mcp_server, graph_dir):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Old Title", body="Body text that stays the same.")
        await _create(client, "concept", "Referrer Node", body="Points at the other node.")
        await client.call_tool("create_edge", {"src": "Referrer Node", "dst": "Old Title", "type": "relates_to"})

        with index_read_conn(graph_dir) as conn:
            referrer_before = conn.execute(
                "SELECT embed_hash, embedded_at FROM node_vectors WHERE node_id='Referrer Node'"
            ).fetchone()

        await client.call_tool("rename_node", {"id": "Old Title", "new_title": "New Title"})

    with index_read_conn(graph_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM node_vectors WHERE node_id='Old Title'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM node_vectors WHERE node_id='New Title'").fetchone()[0] == 1
        referrer_after = conn.execute(
            "SELECT embed_hash, embedded_at FROM node_vectors WHERE node_id='Referrer Node'"
        ).fetchone()

    assert referrer_after == referrer_before  # referrer's own (type, title, body) never changed


async def test_remaining_integrity_tools_wired_correctly_over_mcp(mcp_server):
    """(c) above only exercises decisions_without_rationale/orphans (the brief's two named
    tools); this closes the wiring-verification gap for the other five. Every state here is
    reachable through the real write-tool API (never a direct SQL fixture): create_edge is
    strict about dst existing, so the dangling_edges() case is set up via delete_node's own
    documented "leaves inbound edges dangling by design" behavior, and case_collisions() is
    asserted empty since create_node's own collision check makes a colliding pair
    unreachable through this API in the first place (see integrity.py's own docstring)."""
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Root Concept")

        await _create(client, "question", "Open Question", status="open")
        await client.call_tool("create_edge", {"src": "Open Question", "dst": "Root Concept", "type": "part_of"})
        await _create(client, "question", "Resolved Question", status="resolved")
        await client.call_tool("create_edge", {"src": "Resolved Question", "dst": "Root Concept", "type": "part_of"})
        await _create(client, "question", "Stale Open Question", status="open")
        await client.call_tool("create_edge", {"src": "Stale Open Question", "dst": "Root Concept", "type": "part_of"})

        await _create(client, "decision", "Contradiction A", status="accepted")
        await _create(client, "decision", "Contradiction B", status="accepted")
        await client.call_tool("create_edge", {"src": "Contradiction A", "dst": "Contradiction B", "type": "contradicts"})
        await client.call_tool("create_edge", {"src": "Contradiction A", "dst": "Root Concept", "type": "part_of"})
        await client.call_tool("create_edge", {"src": "Contradiction B", "dst": "Root Concept", "type": "part_of"})

        await _create(client, "concept", "About To Be Deleted")
        await _create(client, "decision", "Dangling Source", status="accepted")
        await client.call_tool("create_edge", {"src": "Dangling Source", "dst": "About To Be Deleted", "type": "references"})
        await client.call_tool("create_edge", {"src": "Dangling Source", "dst": "Root Concept", "type": "part_of"})
        await client.call_tool("delete_node", {"id": "About To Be Deleted"})

        open_q = (await client.call_tool("open_questions", {})).data
        pairs = (await client.call_tool("contradictions", {})).data
        stale_nodes = (await client.call_tool("stale", {"before_iso": "2099-01-01T00:00:00Z"})).data
        dangling = (await client.call_tool("dangling_edges", {})).data
        collisions = (await client.call_tool("case_collisions", {})).data

    assert {n.id for n in open_q} == {"Open Question", "Stale Open Question"}
    assert len(pairs) == 1
    assert {pairs[0].a.id, pairs[0].b.id} == {"Contradiction A", "Contradiction B"}
    assert {n.id for n in stale_nodes} == {"Open Question", "Stale Open Question"}  # accepted decisions excluded by default
    assert len(dangling) == 1
    assert dangling[0].src == "Dangling Source"
    assert dangling[0].dst == "About To Be Deleted"
    assert dangling[0].src_dangling is False
    assert dangling[0].dst_dangling is True
    assert collisions == []


async def test_merge_over_mcp_removes_drop_vector(mcp_server, graph_dir):
    async with Client(mcp_server) as client:
        await _create(client, "concept", "Keep Node", body="Canonical version.")
        await _create(client, "concept", "Drop Node", body="Duplicate version to be merged away.")
        await client.call_tool("merge_nodes", {"keep_id": "Keep Node", "drop_id": "Drop Node"})

    with index_read_conn(graph_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM node_vectors WHERE node_id='Drop Node'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM node_vectors WHERE node_id='Keep Node'").fetchone()[0] == 1
