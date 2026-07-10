// TypeScript mirrors of src/redraft/models.py and src/redraft/ui/models.py wire shapes
// (s6-ui.md §9). One file, same posture as the two Python model modules: this is the one
// place a component looks for "what does the wire shape for X look like." Field names and
// optionality match the Pydantic models byte-for-byte -- see the module docstrings cited
// above each group for the exact source.

export type NodeType =
  | "concept"
  | "decision"
  | "rationale"
  | "requirement"
  | "constraint"
  | "idea"
  | "question"
  | "artifact"
  | "observation"
  | "milestone";

export type EdgeType =
  | "part_of"
  | "justifies"
  | "supersedes"
  | "addresses"
  | "depends_on"
  | "contradicts"
  | "references"
  | "derived_from"
  | "relates_to";

// Edge types other than part_of -- part_of is reparent-only (PUT /api/nodes/{id}/parent),
// never a generic link/unlink target (schema.py's LIST_EDGE_TYPES, mirrored here as a
// compile-time literal list; CreateNodeRequest/EdgeRequest both reject "part_of" as a plain
// edge -- see ui/models.py's model_validator).
export const LINK_EDGE_TYPES: EdgeType[] = [
  "justifies",
  "supersedes",
  "addresses",
  "depends_on",
  "contradicts",
  "references",
  "derived_from",
  "relates_to",
];

export type Direction = "out" | "in" | "both";

// redraft.models

export interface NodeOut {
  id: string;
  type: NodeType;
  title: string;
  body: string;
  status: string | null;
  properties: Record<string, unknown>;
  created: string;
  updated: string;
}

export interface EdgeOut {
  src: string;
  dst: string;
  type: EdgeType;
  dst_exists: boolean;
  warnings: string[];
}

export interface NeighborEdge {
  src: string;
  dst: string;
  type: EdgeType;
  direction: "out" | "in";
}

export interface NodeWithNeighbors {
  node: NodeOut;
  neighbors: NodeOut[];
  edges: NeighborEdge[];
}

export interface DeleteResult {
  ok: boolean;
  existed: boolean;
  orphaned_inbound_edges: NeighborEdge[];
}

export interface RenameResult {
  old_id: string;
  new_id: string;
  relinked: NeighborEdge[];
  body_references_not_updated: string[];
}

export interface MergeResult {
  kept: NodeOut;
  dropped_id: string;
  dropped_body_preview: string;
  warnings: string[];
}

export interface ReindexStats {
  scanned: number;
  upserted: number;
  deleted: number;
  malformed: [string, string][];
}

export interface SnapshotResult {
  committed: boolean;
  sha: string | null;
  pushed: boolean;
  initialized_repo: boolean;
  message: string | null;
}

export interface SearchHit {
  node: NodeOut;
  score: number;
  matched_fts: boolean;
  matched_vector: boolean;
}

export interface DanglingEdge {
  id: string;
  src: string;
  dst: string;
  type: EdgeType;
  src_dangling: boolean;
  dst_dangling: boolean;
}

export interface RationaleRef {
  title: string;
  body: string;
  tradeoffs: string | null;
}

export interface DecisionTableRow {
  decision: NodeOut;
  rationale: RationaleRef[];
  supersedes_chain: string[];
  superseded_by: string[];
  tradeoffs: string | null;
}

export interface DecisionTableGroup {
  driver: NodeOut;
  rows: DecisionTableRow[];
}

export interface SectionGaps {
  open_questions: NodeOut[];
  decisions_without_rationale: NodeOut[];
}

export interface ReportSection {
  node: NodeOut;
  depth: number;
  children: ReportSection[];
  attached: Record<string, NodeOut[]>;
  gaps: SectionGaps;
}

export interface ContradictionPair {
  a: NodeOut;
  b: NodeOut;
}

export interface ReportBundle {
  root_id: string;
  generated_at: string;
  sections: ReportSection[];
  decision_tables: DecisionTableGroup[];
  open_questions: NodeOut[];
  contradictions: ContradictionPair[];
}

// redraft.ui.models (UI-only wire shapes, s6-ui.md §9)

export interface OutlineNode {
  id: string;
  type: NodeType;
  title: string;
  status: string | null;
}

export interface OutlineEdge {
  src: string;
  dst: string;
  type: EdgeType;
}

export interface OutlineOut {
  nodes: OutlineNode[];
  edges: OutlineEdge[];
}

export interface AttentionOut {
  open_questions: NodeOut[];
  unjustified_decisions: NodeOut[];
  dangling_edges: DanglingEdge[];
  stale: NodeOut[];
}

// One row for the Timeline tab (s6-ui.md addendum, GET /api/timeline): either a SCHEDULED
// item (start and/or due set) or an UNSCHEDULED milestone (both null -- the frontend's
// unscheduled tray). start/due round-trip exactly as stored -- never date-validated
// server-side (organizing-protocol.md's Planning dates convention is a convention, not a
// schema constraint), so a malformed value is a real possibility the frontend must render
// without crashing, not just a theoretical one.
export interface TimelineItem {
  id: string;
  title: string;
  type: NodeType;
  status: string | null;
  start: string | null;
  due: string | null;
  part_of: string | null;
  depends_on: string[];
}

export interface TimelineOut {
  items: TimelineItem[];
}

export interface SchemaOut {
  node_types: string[];
  edge_types: string[];
  status_by_type: Record<string, string[] | null>;
}

export interface DedupHintsOut {
  hits: SearchHit[];
  degraded: boolean;
}

export interface ReportFile {
  filename: string;
  size: number;
  modified_at: string;
}

export interface ReportContent {
  filename: string;
  content: string;
}

export interface StatusOut {
  generation: number;
  last_reindex_at: string | null;
  embedder_ready: boolean;
}

export interface GitStatusOut {
  dirty: boolean;
  changed_paths: string[];
}

// Request bodies

export interface CreateNodeRequest {
  type: NodeType;
  title: string;
  body?: string;
  status?: string | null;
  properties?: Record<string, unknown>;
  part_of?: string | null;
  edges?: Partial<Record<EdgeType, string[]>> | null;
}

export interface UpdateNodeRequest {
  body?: string | null;
  mode?: "append" | "replace";
  status?: string | null;
  properties?: Record<string, unknown> | null;
  remove_properties?: string[] | null;
}

export interface EdgeRequest {
  src: string;
  dst: string;
  type: EdgeType;
}

// POST /api/edges/batch -> GraphStore.create_edges (redraft.ui.models.EdgeBatchRequest):
// N edges in one atomic call, each item EdgeRequest's own shape reused verbatim.
export interface EdgeBatchRequest {
  edges: EdgeRequest[];
}
