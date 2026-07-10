"""overview.render_markdown() against hand-built ProjectOverview fixtures -- same
hand-built-fixture style as test_report.py, but at the render layer: these construct
ProjectOverview/OverviewRoot/OverviewBranch directly (no GraphStore, no conn) so the
markdown shape can be pinned exactly without a full graph round-trip. End-to-end CLI
coverage (real graph -> GraphStore -> report.overview() -> render_markdown() -> stdout)
stays in test_cli.py.
"""
from __future__ import annotations

from redraft.models import OverviewBranch, OverviewRoot, OverviewTotals, ProjectOverview
from redraft.overview import render_markdown


def _root(id: str, type: str = "concept", branches: list[OverviewBranch] | None = None) -> OverviewRoot:
    return OverviewRoot(id=id, title=id, type=type, branches=branches or [])


def _branch(id: str, descendant_count: int = 0) -> OverviewBranch:
    return OverviewBranch(
        id=id, title=id, type="concept", excerpt="", descendant_count=descendant_count,
        open_question_count=0, unjustified_decision_count=0,
    )


def test_render_markdown_empty_graph_prints_empty_message():
    assert render_markdown(ProjectOverview()) == "# Project overview\n\n_Empty graph -- no nodes yet._\n"


def test_render_markdown_root_section_has_no_no_branches_yet_placeholder():
    """Every root has >=1 branch by report.overview()'s own contract now (a childless
    parentless node is off the spine entirely, not an empty root) -- so the old per-root
    "no branches yet" fallback is dead and must not appear."""
    result = ProjectOverview(roots=[_root("Redraft", branches=[_branch("Architecture", descendant_count=3)])])
    out = render_markdown(result)
    assert "## Redraft (concept)" in out
    assert "- **Architecture** (concept) — 3 nodes" in out
    assert "no branches yet" not in out
    assert "No part_of hierarchy yet" not in out
    assert "Off the part_of spine" not in out


def test_render_markdown_off_spine_line_when_only_floating_nodes_exist():
    """Nascent-graph fallback: no spine roots at all, only childless parentless nodes."""
    result = ProjectOverview(roots=[], floating_by_type={"rationale": 12, "artifact": 1})
    out = render_markdown(result)
    assert "_No part_of hierarchy yet._" in out
    assert "**Off the part_of spine:** 12 rationale, 1 artifact" in out
    # the noisy per-node "## X (rationale)" sections this replaces must be fully gone
    assert "## " not in out


def test_render_markdown_off_spine_line_alongside_real_roots():
    """The common real-graph case: a genuine spine root renders normally, and the floating
    nodes it doesn't account for (e.g. well-attached rationale nodes) collapse into one
    summary line instead of their own noisy sections."""
    result = ProjectOverview(
        roots=[_root("Redraft", branches=[_branch("Architecture", descendant_count=5)])],
        floating_by_type={"rationale": 12},
    )
    out = render_markdown(result)
    assert "## Redraft (concept)" in out
    assert "**Off the part_of spine:** 12 rationale" in out
    assert "_No part_of hierarchy yet._" not in out  # roots is non-empty -- no fallback needed


def test_render_markdown_off_spine_sorted_by_count_desc_then_type_name():
    result = ProjectOverview(roots=[], floating_by_type={"concept": 2, "artifact": 2, "idea": 5})
    out = render_markdown(result)
    assert "**Off the part_of spine:** 5 idea, 2 artifact, 2 concept" in out


def test_render_markdown_no_off_spine_line_when_floating_by_type_empty():
    result = ProjectOverview(roots=[_root("Redraft", branches=[_branch("Architecture")])], floating_by_type={})
    out = render_markdown(result)
    assert "Off the part_of spine" not in out
    assert "No part_of hierarchy yet" not in out


def test_render_markdown_totals_and_open_questions_unaffected():
    """Sanity guard: the totals/open-questions tail (untouched by this change) still renders
    after the new off-spine line."""
    result = ProjectOverview(
        roots=[_root("Redraft", branches=[_branch("Architecture")])],
        floating_by_type={"rationale": 1},
        totals=OverviewTotals(counts_by_type={"concept": 2, "rationale": 1}, orphan_count=1),
    )
    out = render_markdown(result)
    assert "**Totals:** 2 concept, 1 rationale (1 orphaned node)" in out
