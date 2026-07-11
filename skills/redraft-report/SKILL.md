---
name: redraft-report
description: Generate a review-grade technical report or design document directly from a redraft knowledge graph. Use when the user asks for a technical report, design writeup, architecture document, or review pack for a project that has a redraft graph connected.
---

You are rendering the project's design graph into a technical document — the redraft
organizing protocol's §7 report flow. The server assembles the structure; **you write
the prose**. The graph is the only source of truth.

## Steps

1. **Assemble.** Call `assemble_report(root_id)` for the spine root the report should
   cover (list roots via the `overview` tool or `graph://project/root` if unsure; a
   topical brief uses `briefing(query)` instead). The bundle carries the section tree,
   `decision_tables` grouped by driver, open questions, and contradictions.

2. **Write LaTeX.** Author the report as a LaTeX document (`\section`/`\subsection`
   mirroring the spine, `tabular` for decision tables, itemize for requirement lists).
   Structure for a full report: executive summary → architecture → design
   specification → decision record (every table: accepted rows first, rejected rows
   WITH their tradeoffs) → requirements & constraints → timeline → open questions &
   gaps → graph statistics.

3. **Ground every claim.** Every factual statement must trace to a node body, title,
   status, or edge in the bundle. Where the graph carries no rationale, write `—` and
   name the gap in prose — a gap is a finding, not an embarrassment. NEVER present a
   `rejected` decision as adopted; statuses are load-bearing.

4. **Save + snapshot.** Write to `reports/<YYYY-MM-DD>-<short-slug>.tex` in the graph
   repo, then call `snapshot("<what the report covers>")`. The operator UI's Reports
   tab lists and renders it immediately.

## What NOT to do

- Never invent rationale, tradeoffs, or context the graph does not carry — silence in
  the graph is silence in the report (or an entry under gaps).
- Never grep or read `graph/nodes/*.md` to gather material — the bundle and retrieval
  tools are the read path (protocol: "Reading the graph").
- Don't paste raw node bodies wholesale; synthesize prose a zero-context engineering
  reviewer can read, with the graph's claims intact.
- Lightweight recurring summaries (`/summary`) stay markdown per the protocol; this
  skill is for formal technical reports.
