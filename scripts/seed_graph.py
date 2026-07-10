#!/usr/bin/env python3
"""Seed the dogfood graph (docs/seed/seed-nodes.yaml) into a real GraphStore.

Two-pass load per the seed file's own header contract: create every node (pass 1), then
every part_of/edge (pass 2) -- order-independent within each pass, since edges target
titles that must all already exist by the time pass 2 runs.

load_seed() is the reusable, verbatim loader (also used by tests/test_phase3_gates.py to
build its fixture graph from the real seed data) -- it applies NO status overrides, exactly
what the seed file's header contract specifies. main() is the real-repo entrypoint: it
layers this run's actual milestone-status reality on top via plain update_node calls (not
baked into load_seed, since that's a fact about *this run*, not a property of the seed data
itself), then snapshots and prints integrity-check numbers.

Usage: env -u PYTHONPATH uv run python scripts/seed_graph.py
(REDRAFT_DIR must be set -- see redraft.config.resolve_graph_dir.)
"""
from __future__ import annotations

from pathlib import Path

import yaml

from redraft.config import resolve_graph_dir
from redraft.retrieval import RetrievalConfig, integrity
from redraft.schema import EdgeType
from redraft.store import GraphStore

SEED_PATH = Path(__file__).resolve().parent.parent / "docs" / "seed" / "seed-nodes.yaml"

# Reality as of this slice (S4b): Phase 0-2 are gated and merged onto main; Phase 3 is
# this very slice and is still in flight while this script runs, so it -- like Phase 4 --
# stays "planned" exactly as authored in the seed file. Only Phase 0-2 need overriding.
_MILESTONE_STATUS_OVERRIDES: dict[str, str] = {
    "Phase 0 — canonical store and projector": "done",
    "Phase 1 — MCP server and admin tools": "done",
    "Phase 2 — retrieval": "done",
}


def load_seed(graph_dir: Path, retrieval_config: RetrievalConfig | None = None) -> GraphStore:
    """Two-pass load of docs/seed/seed-nodes.yaml into a fresh GraphStore at graph_dir,
    verbatim per the seed file's own header contract. Refuses to run if graph/nodes/
    already holds any node file -- no merge/sync logic, this is a one-shot seed of an
    empty graph.
    """
    nodes_dir = graph_dir / "graph" / "nodes"
    if any(nodes_dir.glob("*.md")):
        raise RuntimeError(
            f"{nodes_dir} is not empty -- refusing to seed onto an existing graph "
            "(this script has no merge/sync logic; clear it first if a re-seed is "
            "genuinely intended)"
        )

    entries = yaml.safe_load(SEED_PATH.read_text())["nodes"]
    store = GraphStore(graph_dir, retrieval_config=retrieval_config)

    for entry in entries:
        store.create_node(
            type=entry["type"], title=entry["title"], body=entry["body"],
            status=entry.get("status"), properties=entry.get("properties"),
        )
    for entry in entries:
        if entry.get("part_of"):
            store.create_edge(entry["title"], entry["part_of"], EdgeType.PART_OF)
        for edge_type, targets in entry.get("edges", {}).items():
            for target in targets:
                store.create_edge(entry["title"], target, EdgeType(edge_type))

    return store


def main() -> None:
    graph_dir = resolve_graph_dir()
    store = load_seed(graph_dir, retrieval_config=RetrievalConfig())
    for title, status in _MILESTONE_STATUS_OVERRIDES.items():
        store.update_node(title, status=status)

    result = store.snapshot("seed: dogfood graph from docs/seed/seed-nodes.yaml")
    print(f"snapshot: committed={result.committed} sha={result.sha} initialized_repo={result.initialized_repo}")

    con = store.con
    dangling = integrity.dangling_edges(con)
    collisions = integrity.case_collisions(con)
    orphaned = integrity.orphans(con)
    without_rationale = integrity.decisions_without_rationale(con)
    open_unaddressed = con.execute(
        "SELECT title FROM nodes WHERE type = 'question' AND status = 'open' "
        "AND id NOT IN (SELECT dst FROM edges WHERE type = 'addresses')"
    ).fetchall()

    print(f"nodes seeded: {con.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]}")
    print(f"dangling_edges: {len(dangling)}")
    print(f"case_collisions: {len(collisions)}")
    print(f"orphans: {len(orphaned)}")
    print(f"decisions_without_rationale ({len(without_rationale)}):")
    for d in without_rationale:
        print(f"  - {d['title']}")
    print(f"open_unaddressed_questions ({len(open_unaddressed)}):")
    for (title,) in open_unaddressed:
        print(f"  - {title}")


if __name__ == "__main__":
    main()
