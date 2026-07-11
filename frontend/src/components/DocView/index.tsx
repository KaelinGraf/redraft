import { useParams } from "react-router-dom";
import { useDoc } from "../../api/queries";
import { useActiveTab, buildNodeLink } from "../../lib/nav";
import { Link } from "react-router-dom";
import { EmptyState } from "../EmptyState";
import { TypeChip } from "../TypeChip";
import { StatusBadge } from "../StatusIndicator";
import type { ReportSection } from "../../api/types";

/** CENTER, Doc tab: /doc/:rootId -- renders ReportBundle.sections recursively (s6-ui.md
 * §10.1). Same useQuery(['doc', rootId]) call TablesView uses for the same rootId (one
 * request, two views). */
export function DocView() {
  const { rootId } = useParams<{ rootId: string }>();
  const { data, isPending, isError, error } = useDoc(rootId);
  const activeTab = useActiveTab();

  if (!rootId) return <EmptyState>Select a node from the outline to view its assembled doc.</EmptyState>;
  if (isPending) return <div className="spinner-line">Assembling report…</div>;
  if (isError) return <EmptyState>{error instanceof Error ? error.message : "Failed to load report."}</EmptyState>;

  return (
    <div>
      <div className="faint" style={{ marginBottom: 14 }}>
        generated {data.generated_at}
      </div>
      {data.contradictions.length > 0 ? (
        <div className="card" style={{ borderColor: "var(--danger)" }}>
          <div className="card__title" style={{ color: "var(--danger)" }}>
            Contradictions ({data.contradictions.length})
          </div>
          {data.contradictions.map((c, i) => (
            <div key={i} className="edge-row">
              <Link to={buildNodeLink(activeTab, c.a.id)}>{c.a.title}</Link>
              <span className="faint">contradicts</span>
              <Link to={buildNodeLink(activeTab, c.b.id)}>{c.b.title}</Link>
            </div>
          ))}
        </div>
      ) : null}
      {data.sections.map((s) => (
        <SectionNode key={s.node.id} section={s} activeTab={activeTab} />
      ))}
    </div>
  );
}

function SectionNode({ section, activeTab }: { section: ReportSection; activeTab: ReturnType<typeof useActiveTab> }) {
  const hasGaps = section.gaps.open_questions.length > 0 || section.gaps.decisions_without_rationale.length > 0;
  const fontSize = Math.max(19 - section.depth * 2, 13);
  return (
    <div className="section-node">
      <div style={{ fontSize, fontWeight: 700, margin: "4px 0", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 7 }}>
        <TypeChip type={section.node.type} />
        <Link to={buildNodeLink(activeTab, section.node.id)}>{section.node.title}</Link>
        <StatusBadge status={section.node.status} />
      </div>
      {section.node.body ? <div className="body-view">{section.node.body}</div> : null}
      {Object.entries(section.attached).map(([edgeType, nodes]) =>
        nodes.length > 0 ? (
          <div key={edgeType} className="faint" style={{ margin: "4px 0", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4 }}>
            {edgeType}:
            {nodes.map((n, i) => (
              <span key={n.id} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                {n.title}
                {i < nodes.length - 1 ? "," : ""}
                <StatusBadge status={n.status} />
              </span>
            ))}
          </div>
        ) : null,
      )}
      {hasGaps ? (
        <div className="section-node__gaps">
          {section.gaps.open_questions.length > 0 ? `${section.gaps.open_questions.length} open question(s) ` : ""}
          {section.gaps.decisions_without_rationale.length > 0
            ? `${section.gaps.decisions_without_rationale.length} decision(s) without rationale`
            : ""}
        </div>
      ) : null}
      {section.children.map((c) => (
        <SectionNode key={c.node.id} section={c} activeTab={activeTab} />
      ))}
    </div>
  );
}
