"""Phase 3 acceptance gates (docs/protocol/report-bundle-v2.md, organizing-protocol.md):
assemble_report/briefing driven over an in-memory FastMCP Client against a real,
real-model-backed server -- no stubs. Client is opened fresh inside each test, never in a
fixture (same documented FastMCP event-loop caveat test_phase2_gates.py's module docstring
calls out).

"Bundle validates against the wire schema": FastMCP's Client deserializes every tool result
against that tool's declared Pydantic return type (ReportBundle/BriefingResult) before
`result.data` is even constructible -- a shape mismatch would raise a ValidationError before
any assertion below runs, so reaching those assertions at all already is the schema-validity
gate; the assertions further check the *content* is actually correct, not just shaped right.

seeded_server loads the REAL docs/seed/seed-nodes.yaml verbatim via scripts/seed_graph.
load_seed (module-scoped: seeding + embedding 60 real nodes once and reusing it read-only
across every test in this module, matching that none of these tests mutate the graph).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastmcp import Client

from redraft.prompts import ORGANIZING_PROTOCOL_TEXT
from redraft.server import ServerConfig, build_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
from seed_graph import load_seed  # noqa: E402

DOCS_PROTOCOL_PATH = _REPO_ROOT / "docs" / "protocol" / "organizing-protocol.md"
PACKAGED_PROTOCOL_PATH = _REPO_ROOT / "src" / "redraft" / "organizing_protocol.md"
CLAUDE_MD_PATH = _REPO_ROOT / "CLAUDE.md"

DRIVER_TITLE = "Which memory architecture stores project design knowledge"
ACCEPTED_TITLE = "Adopt a thin custom MCP server with text-canonical storage"
REJECTED_GRAPHITI_TITLE = "Adopt Graphiti as the memory backend"
GRAPHITI_RATIONALE_TITLE = "Graphiti has no text-canonical path and calls an LLM on write"


@pytest.fixture(scope="module")
def seeded_server(tmp_path_factory):
    graph_dir = tmp_path_factory.mktemp("seed_graph")
    load_seed(graph_dir)  # retrieval_config=None here -- build_server's own construction
    return build_server(ServerConfig(graph_dir=graph_dir))  # reindexes + embeds all 60 nodes


# -- assemble_report -------------------------------------------------------------------


async def test_assemble_report_memory_architecture_decision_table(seeded_server):
    async with Client(seeded_server) as client:
        result = await client.call_tool("assemble_report", {"root_id": "Architecture", "depth": 4})
    bundle = result.data

    assert bundle.root_id == "Architecture"
    assert bundle.sections[0].node.id == "Architecture"

    group = next(g for g in bundle.decision_tables if g.driver.id == DRIVER_TITLE)
    assert len(group.rows) == 5  # 1 accepted + 4 rejected alternatives (the seed's showcase)
    assert group.rows[0].decision.status == "accepted"  # section 2.2 rule 4: accepted-first

    rows_by_id = {r.decision.id: r for r in group.rows}
    accepted_row = rows_by_id[ACCEPTED_TITLE]
    assert accepted_row.decision.status == "accepted"
    assert accepted_row.rationale != []

    graphiti_row = rows_by_id[REJECTED_GRAPHITI_TITLE]
    assert graphiti_row.decision.status == "rejected"
    assert [r.title for r in graphiti_row.rationale] == [GRAPHITI_RATIONALE_TITLE]
    assert graphiti_row.tradeoffs is not None  # seed sets properties.tradeoffs on this rationale

    # every row addresses the same driver, regardless of the decision's own part_of branch
    # (section 2.2 rule 2) -- all 5 of these decisions are part_of "Architecture" in the seed,
    # so this also implicitly covers the common case; the cross-branch case is exercised by
    # the "sections and decision_tables are two views over the same edges" note in the doc.
    for row in group.rows:
        assert row.decision.status in ("accepted", "rejected", "superseded", "proposed")


async def test_assemble_report_data_model_gaps_and_supersession(seeded_server):
    async with Client(seeded_server) as client:
        result = await client.call_tool("assemble_report", {"root_id": "Data model", "depth": 2})
    bundle = result.data
    root = bundle.sections[0]
    assert root.node.id == "Data model"

    # deliberate gaps (docs/seed/seed-nodes.yaml is hand-authored so these 3 decisions and
    # 1 question are exactly the ones with no edges block at all / status=open+unaddressed)
    assert {n.id for n in root.gaps.decisions_without_rationale} == {
        "Node ids are ULIDs stored in a redundant id frontmatter key",
        "part_of is a scalar frontmatter key, never a list",
        "justifies edges are stored on the rationale node, pointing at the decision it supports",
    }
    assert {n.id for n in root.gaps.open_questions} == {
        "Should rename_node rewrite wikilinks inside body prose, not just frontmatter"
    }

    # supersedes chain rendering, on the seed's one real supersession example (section 2.3)
    superseded_section = next(
        c for c in root.children if c.node.id == "Node ids are ULIDs stored in a redundant id frontmatter key"
    )
    assert superseded_section.node.status == "superseded"
    supersedes_attached = {n.id for n in superseded_section.attached.get("supersedes", [])}
    assert supersedes_attached == {"Filename stem is the node id; no separate ULID"}


async def test_assemble_report_open_questions_consistency_invariant(seeded_server):
    """report-bundle-v2.md section 2.5: bundle.open_questions must equal the deduped concat
    of every section's gaps.open_questions -- "implement this as a test, not just a rule.\""""
    async with Client(seeded_server) as client:
        result = await client.call_tool("assemble_report", {"root_id": "redraft", "depth": 4})
    bundle = result.data

    def flatten(sections):
        for section in sections:
            yield section
            yield from flatten(section.children)

    from_gaps: list[str] = []
    seen: set[str] = set()
    for section in flatten(bundle.sections):
        for q in section.gaps.open_questions:
            if q.id not in seen:
                seen.add(q.id)
                from_gaps.append(q.id)

    assert [q.id for q in bundle.open_questions] == from_gaps
    assert len(bundle.open_questions) == 2  # the seed's two status=open questions, total


async def test_decision_table_row_renders_supersedes_chain_and_tradeoffs(mcp_server):
    """Synthetic fixture mirroring report-bundle-v2.md section 4.1's own worked example
    (nodes 9-13) node-for-node, on an empty graph -- the seed data never exercises
    supersedes_chain/superseded_by *inside* a decision_table row (its one supersession pair
    has no addressing driver), so this is the regression test for that field pairing,
    checked against the doc's own documented numbers rather than re-derived here."""
    async with Client(mcp_server) as client:
        await client.call_tool("create_node", {"type": "concept", "title": "Embeddings"})
        await client.call_tool(
            "create_node",
            {"type": "question", "title": "How should the embedding cache be invalidated", "status": "resolved"},
        )
        await client.call_tool(
            "create_edge",
            {"src": "How should the embedding cache be invalidated", "dst": "Embeddings", "type": "part_of"},
        )

        await client.call_tool(
            "create_node", {"type": "decision", "title": "Cache embeddings by whole-file content hash", "status": "superseded"}
        )
        await client.call_tool(
            "create_edge", {"src": "Cache embeddings by whole-file content hash", "dst": "Embeddings", "type": "part_of"}
        )
        await client.call_tool(
            "create_edge",
            {
                "src": "Cache embeddings by whole-file content hash",
                "dst": "How should the embedding cache be invalidated",
                "type": "addresses",
            },
        )
        await client.call_tool(
            "create_node",
            {"type": "rationale", "title": "Reusing the projector's existing file hash avoids a second hashing pass"},
        )
        await client.call_tool(
            "create_edge",
            {
                "src": "Reusing the projector's existing file hash avoids a second hashing pass",
                "dst": "Cache embeddings by whole-file content hash",
                "type": "justifies",
            },
        )

        await client.call_tool(
            "create_node", {"type": "decision", "title": "Cache embeddings by a type, title, and body hash", "status": "accepted"}
        )
        await client.call_tool(
            "create_edge", {"src": "Cache embeddings by a type, title, and body hash", "dst": "Embeddings", "type": "part_of"}
        )
        await client.call_tool(
            "create_edge",
            {
                "src": "Cache embeddings by a type, title, and body hash",
                "dst": "How should the embedding cache be invalidated",
                "type": "addresses",
            },
        )
        await client.call_tool(
            "create_edge",
            {
                "src": "Cache embeddings by a type, title, and body hash",
                "dst": "Cache embeddings by whole-file content hash",
                "type": "supersedes",
            },
        )
        await client.call_tool(
            "create_node",
            {
                "type": "rationale",
                "title": "A whole-file hash forces a re-embed on pure metadata edits",
                "properties": {"tradeoffs": "Costs a second, independent hash computed at embed time."},
            },
        )
        await client.call_tool(
            "create_edge",
            {
                "src": "A whole-file hash forces a re-embed on pure metadata edits",
                "dst": "Cache embeddings by a type, title, and body hash",
                "type": "justifies",
            },
        )

        result = await client.call_tool("assemble_report", {"root_id": "Embeddings", "depth": 2})

    bundle = result.data
    group = next(g for g in bundle.decision_tables if g.driver.id == "How should the embedding cache be invalidated")
    assert [r.decision.id for r in group.rows] == [
        "Cache embeddings by a type, title, and body hash",  # accepted -- sorts first despite being created later
        "Cache embeddings by whole-file content hash",  # the only remaining row
    ]
    accepted_row, superseded_row = group.rows
    assert accepted_row.supersedes_chain == ["Cache embeddings by whole-file content hash"]
    assert accepted_row.superseded_by == []
    assert accepted_row.tradeoffs == "Costs a second, independent hash computed at embed time."
    assert superseded_row.supersedes_chain == []
    assert superseded_row.superseded_by == ["Cache embeddings by a type, title, and body hash"]


async def test_decision_table_excludes_idea_nodes_that_also_address_the_driver(mcp_server):
    """graphrules.py's own `addresses` convention allows an `idea` src, not just `decision`
    (decision|idea -> question|requirement); report-bundle-v2.md section 2.2 scopes
    decision_tables to "every decision node" specifically. An idea addressing the same
    driver must be excluded from the table, not admitted as a row with a nonsensical
    status=None (idea carries no status field at all)."""
    async with Client(mcp_server) as client:
        await client.call_tool("create_node", {"type": "question", "title": "Driver Question", "status": "open"})
        await client.call_tool("create_node", {"type": "decision", "title": "Real Decision", "status": "accepted"})
        await client.call_tool("create_edge", {"src": "Real Decision", "dst": "Driver Question", "type": "addresses"})
        await client.call_tool("create_node", {"type": "idea", "title": "Unresolved Idea"})
        await client.call_tool("create_edge", {"src": "Unresolved Idea", "dst": "Driver Question", "type": "addresses"})

        result = await client.call_tool("assemble_report", {"root_id": "Driver Question", "depth": 1})

    bundle = result.data
    group = next(g for g in bundle.decision_tables if g.driver.id == "Driver Question")
    assert [r.decision.id for r in group.rows] == ["Real Decision"]


async def test_assemble_report_negative_depth_raises_invalid_argument(mcp_server):
    from fastmcp.exceptions import ToolError

    async with Client(mcp_server) as client:
        await client.call_tool("create_node", {"type": "concept", "title": "Root"})
        with pytest.raises(ToolError, match="invalid_argument"):
            await client.call_tool("assemble_report", {"root_id": "Root", "depth": -1})


async def test_assemble_report_unknown_root_raises_not_found(mcp_server):
    from fastmcp.exceptions import ToolError

    async with Client(mcp_server) as client:
        with pytest.raises(ToolError, match="not_found"):
            await client.call_tool("assemble_report", {"root_id": "Does Not Exist"})


# -- briefing ---------------------------------------------------------------------------


async def test_briefing_graphiti_query_surfaces_the_rejection_decision(seeded_server):
    async with Client(seeded_server) as client:
        result = await client.call_tool("briefing", {"query": "graphiti", "k": 5})
    briefing = result.data

    assert briefing.query == "graphiti"
    assert briefing.hits != []
    assert any(h.node.id == REJECTED_GRAPHITI_TITLE for h in briefing.hits)
    # scope (hits + 1-hop neighbors) must reach the rationale via the neighborhood entry
    graphiti_entry = next(e for e in briefing.neighborhood if e.anchor == REJECTED_GRAPHITI_TITLE)
    assert any(n.dst == GRAPHITI_RATIONALE_TITLE or n.src == GRAPHITI_RATIONALE_TITLE for n in graphiti_entry.neighbors)


# -- organizing protocol prompt ----------------------------------------------------------


async def test_organizing_protocol_prompt_registered_and_returns_packaged_text(mcp_server):
    async with Client(mcp_server) as client:
        names = {p.name for p in await client.list_prompts()}
        assert "organizing_protocol" in names
        result = await client.get_prompt("organizing_protocol")
    text = result.messages[0].content.text
    assert text == ORGANIZING_PROTOCOL_TEXT


def test_packaged_protocol_copy_is_byte_identical_to_docs_source():
    """Single-source rule: the packaged copy loaded by prompts.py must never drift from
    docs/protocol/organizing-protocol.md -- this is the test that fails the build if they
    ever do. The full protocol text ships as *graph* repos' CLAUDE.md, written verbatim by
    redraft init from this same packaged copy (tests/test_init.py) -- this engine
    repo's own CLAUDE.md is a short dev-facing doc that points here instead of embedding a
    second copy (v1.1: engine/graph repo split, see docs/protocol/ pointer below)."""
    doc_bytes = DOCS_PROTOCOL_PATH.read_bytes()
    assert PACKAGED_PROTOCOL_PATH.read_bytes() == doc_bytes
    assert "docs/protocol" in CLAUDE_MD_PATH.read_text(encoding="utf-8")
