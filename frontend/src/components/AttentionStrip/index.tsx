import { Link } from "react-router-dom";
import { useAttention } from "../../api/queries";
import { useActiveTab } from "../../lib/nav";
import { buildNodeLink } from "../../lib/nav";
import { TypeChip } from "../TypeChip";
import type { DanglingEdge, NodeOut } from "../../api/types";
import { EDGE_TYPE_LABEL } from "../../lib/palette";

/** RIGHT pane -- the hygiene strip, one useQuery(['attention']) call (s6-ui.md §9/§10.1):
 * open questions, decisions lacking a justifying rationale, dangling edges, stale nodes. */
export function AttentionStrip() {
  const { data, isPending, isError } = useAttention();
  const activeTab = useActiveTab();

  if (isPending) return <div className="spinner-line">Loading…</div>;
  if (isError) return <div className="spinner-line">Failed to load attention.</div>;

  return (
    <>
      <NodeSection title="Open questions" nodes={data.open_questions} activeTab={activeTab} />
      <NodeSection title="Decisions lacking rationale" nodes={data.unjustified_decisions} activeTab={activeTab} />
      <DanglingSection edges={data.dangling_edges} activeTab={activeTab} />
      <NodeSection title="Stale (30+ days)" nodes={data.stale} activeTab={activeTab} />
    </>
  );
}

function NodeSection({
  title,
  nodes,
  activeTab,
}: {
  title: string;
  nodes: NodeOut[];
  activeTab: ReturnType<typeof useActiveTab>;
}) {
  return (
    <div className="attention-section">
      <div className="attention-section__title">
        {title} ({nodes.length})
      </div>
      {nodes.length === 0 ? (
        <div className="attention-empty">None</div>
      ) : (
        nodes.map((n) => (
          <Link key={n.id} to={buildNodeLink(activeTab, n.id)} className="attention-item">
            <TypeChip type={n.type} compact /> {n.title}
          </Link>
        ))
      )}
    </div>
  );
}

function DanglingSection({
  edges,
  activeTab,
}: {
  edges: DanglingEdge[];
  activeTab: ReturnType<typeof useActiveTab>;
}) {
  return (
    <div className="attention-section">
      <div className="attention-section__title">Dangling edges ({edges.length})</div>
      {edges.length === 0 ? (
        <div className="attention-empty">None</div>
      ) : (
        edges.map((e) => (
          <div key={e.id} className="attention-item" style={{ cursor: "default" }}>
            <EndpointRef id={e.src} dangling={e.src_dangling} activeTab={activeTab} />
            {" → "}
            <span className="faint">{EDGE_TYPE_LABEL[e.type] ?? e.type}</span>
            {" → "}
            <EndpointRef id={e.dst} dangling={e.dst_dangling} activeTab={activeTab} />
          </div>
        ))
      )}
    </div>
  );
}

function EndpointRef({
  id,
  dangling,
  activeTab,
}: {
  id: string;
  dangling: boolean;
  activeTab: ReturnType<typeof useActiveTab>;
}) {
  if (dangling) return <span style={{ color: "var(--danger)" }}>{id} (missing)</span>;
  return <Link to={buildNodeLink(activeTab, id)}>{id}</Link>;
}
