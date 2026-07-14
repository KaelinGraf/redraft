"""`redraft overview`: prints a compact markdown map of the project's shape
(report.overview()'s CLI surface) -- the same text a graph's SessionStart hook (init.py)
injects into every new session's context, so it must read well as plain context and fit on
roughly one screen.

Unlike the MCP tool/resource (tools/report_tools.py, resources.py), which only ever run once
the server (and its GraphStore) is already up -- so the derived index is already fresh --
this CLI entrypoint may be the very FIRST `redraft` process to ever touch a graph: its own
SessionStart hook fires on the first session opened right after `redraft init`, quite
possibly before any MCP server has booted and built the index at all. `main` therefore
constructs a plain, no-retrieval-config GraphStore first: GraphStore.__init__
unconditionally reindexes (index.reindex()'s incremental hash-compare scan, via
`CREATE TABLE IF NOT EXISTS`), which is cheap -- no sqlite-vec, no embedding model touched --
and guarantees report.overview() below never sees a missing-schema or stale index, at the
cost that this command always briefly takes the write lock.
"""
from __future__ import annotations

import sys
from pathlib import Path

from redraft.config import GraphPaths, resolve_graph_dir
from redraft.errors import LockTimeoutError
from redraft.init import NotAGraphError
from redraft.models import ProjectOverview
from redraft.report import overview as lib_overview
from redraft.store import GraphStore


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def render_markdown(result: ProjectOverview) -> str:
    """Compact, scannable markdown: one heading per root, one bullet (+ optional excerpt
    line) per branch with its tallies, an off-spine summary line, a totals line, then a short
    open-questions list. This is literal injected session context (see module docstring), so
    it stays terse by construction -- gaps (unjustified/open) are only mentioned when nonzero.

    Every `result.roots` entry has >=1 branch by construction (report.overview()'s own
    contract), so there is no "no branches yet" case to render per root any more -- a
    parentless node with no branches of its own is off the spine entirely and shows up (only)
    in the floating_by_type summary line below, never as an empty root section.
    """
    if not result.roots and not result.floating_by_type:
        return "# Project overview\n\n_Empty graph -- no nodes yet._\n"

    lines: list[str] = ["# Project overview"]
    for root in result.roots:
        lines.append("")
        lines.append(f"## {root.title} ({root.type})")
        for b in root.branches:
            status = f", {b.status}" if b.status else ""
            tally = [_plural(b.descendant_count, "node")]
            if b.decision_counts_by_status:
                decisions = ", ".join(f"{count} {st}" for st, count in b.decision_counts_by_status.items())
                tally.append(f"decisions: {decisions}")
            if b.unjustified_decision_count:
                tally.append(_plural(b.unjustified_decision_count, "unjustified decision", "unjustified decisions"))
            if b.open_question_count:
                tally.append(_plural(b.open_question_count, "open question", "open questions"))
            lines.append(f"- **{b.title}** ({b.type}{status}) — {' · '.join(tally)}")
            if b.excerpt:
                lines.append(f"  {b.excerpt}")

    if not result.roots:
        lines.append("")
        lines.append("_No part_of hierarchy yet._")

    if result.floating_by_type:
        lines.append("")
        floating = sorted(result.floating_by_type.items(), key=lambda kv: (-kv[1], kv[0]))
        floating_summary = ", ".join(f"{count} {type_}" for type_, count in floating)
        lines.append(f"**Off the part_of spine:** {floating_summary}")

    lines.append("")
    t = result.totals
    type_summary = ", ".join(f"{count} {type_}" for type_, count in t.counts_by_type.items())
    hygiene = []
    if t.orphan_count:
        hygiene.append(_plural(t.orphan_count, "orphaned node", "orphaned nodes"))
    if t.dangling_edge_count:
        hygiene.append(_plural(t.dangling_edge_count, "dangling edge", "dangling edges"))
    totals_line = f"**Totals:** {type_summary}"
    if hygiene:
        totals_line += f" ({', '.join(hygiene)})"
    lines.append(totals_line)

    if result.top_open_questions:
        lines.append("")
        lines.append(f"**Open questions ({len(result.top_open_questions)}):**")
        for q in result.top_open_questions:
            lines.append(f"- {q.title}")

    return "\n".join(lines) + "\n"


def main(graph_dir: str | Path | None = None) -> None:
    """redraft.cli's `overview` subcommand hands its already-parsed positional graph_dir
    (or None) straight here -- no argv/argparse in this module, matching redraft.ui.app.main's
    plain-kwargs shape (this subcommand has exactly one option, not enough to justify its own
    argparse layer the way init.py's --no-git/--project-name do)."""
    try:
        resolved = resolve_graph_dir(graph_dir)
        if not GraphPaths(resolved).nodes_dir.is_dir():
            # GraphStore's constructor scaffolds graph/ and index/ unconditionally -- refuse
            # BEFORE constructing it (mirrors sync_graph's own NotAGraphError refusal in
            # init.py) instead of silently creating a graph in whatever directory this was run.
            raise NotAGraphError(
                f"{resolved} is not a redraft graph (no graph/nodes/); run 'redraft init' to create one"
            )
        store = GraphStore(resolved)  # no retrieval_config: cheap reindex only -- see module docstring
        result = lib_overview(store.con)
    except (RuntimeError, LockTimeoutError, NotAGraphError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(render_markdown(result), end="")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else None)
