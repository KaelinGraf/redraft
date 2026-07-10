// Categorical color assignment for the 10 redraft.schema.NodeType values and the per-type
// status vocabulary (schema.STATUS_VALUES). s6-ui.md itself specifies no color mapping (the
// design doc has zero hex/color mentions) -- this is the orchestrator brief's own fallback
// categorical set: blue #2a78d6, aqua #1baf7a, yellow #eda100, green #008300, violet #4a3aa7,
// red #e34948, magenta #e87ba4, orange #eb6834 (+ ink/grays). One swatch per node type,
// stable across light/dark (categorical accent colors don't need to invert with theme, only
// the surrounding chrome does) -- each color is used only as a small dot/ring/left-border
// swatch next to a text label, never as text color itself, so no per-type contrast tuning is
// needed against either background.
import type { EdgeType, NodeType } from "../api/types";

export const NODE_TYPE_COLOR: Record<NodeType, string> = {
  concept: "#2a78d6", // blue -- foundational/neutral idea space
  decision: "#4a3aa7", // violet -- weighty, deliberate
  rationale: "#1baf7a", // aqua -- supports/justifies a decision
  requirement: "#eb6834", // orange -- must-do, attention-worthy
  constraint: "#e34948", // red -- restrictive, cautionary
  idea: "#eda100", // yellow -- exploratory, classic "idea"
  question: "#e87ba4", // magenta -- distinct "needs an answer" marker
  artifact: "#008300", // green -- concrete, tangible output
  observation: "#6b7280", // gray (ink/grays slot) -- neutral, makes no claim
  milestone: "#3f4a5a", // dark ink (ink/grays slot) -- structural waypoint, not "content"
};

// Status colors reuse the SAME 8-swatch palette by semantic meaning (green=good/complete,
// red=rejected, gray=historical/inactive, yellow=tentative, orange=needs-attention,
// blue=scheduled) rather than inventing a second color system -- every status value across
// all three status-bearing types (decision, question, milestone; schema.STATUS_VALUES) is a
// key here, flat, since no two types share a status string.
export const STATUS_COLOR: Record<string, string> = {
  proposed: "#eda100",
  accepted: "#008300",
  superseded: "#6b7280",
  rejected: "#e34948",
  open: "#eb6834",
  resolved: "#008300",
  planned: "#2a78d6",
  done: "#008300",
};

export const EDGE_TYPE_LABEL: Record<EdgeType, string> = {
  part_of: "part of",
  justifies: "justifies",
  supersedes: "supersedes",
  addresses: "addresses",
  depends_on: "depends on",
  contradicts: "contradicts",
  references: "references",
  derived_from: "derived from",
  relates_to: "relates to",
};

export function nodeTypeColor(type: string): string {
  return NODE_TYPE_COLOR[type as NodeType] ?? "#6b7280";
}

export function statusColor(status: string | null | undefined): string {
  if (!status) return "#6b7280";
  return STATUS_COLOR[status] ?? "#6b7280";
}
