import { useParams, Link } from "react-router-dom";
import { useDoc } from "../../api/queries";
import { useActiveTab, buildNodeLink } from "../../lib/nav";
import { EmptyState } from "../EmptyState";
import { StatusBadge } from "../StatusIndicator";

/** CENTER, Tables tab: /tables/:rootId -- renders the SAME ReportBundle's .decision_tables
 * DocView's .sections comes from (s6-ui.md §10.1), one useQuery(['doc', rootId]) call. */
export function TablesView() {
  const { rootId } = useParams<{ rootId: string }>();
  const { data, isPending, isError, error } = useDoc(rootId);
  const activeTab = useActiveTab();

  if (!rootId) return <EmptyState>Select a node from the outline to view its decision tables.</EmptyState>;
  if (isPending) return <div className="spinner-line">Assembling report…</div>;
  if (isError) return <EmptyState>{error instanceof Error ? error.message : "Failed to load report."}</EmptyState>;

  if (data.decision_tables.length === 0) {
    return <EmptyState>No decisions found under this node.</EmptyState>;
  }

  return (
    <div>
      {data.decision_tables.map((group) => (
        <div key={group.driver.id}>
          <h3 style={{ marginBottom: 8 }}>
            <Link to={buildNodeLink(activeTab, group.driver.id)}>{group.driver.title}</Link>
          </h3>
          <table className="decision-table">
            <thead>
              <tr>
                <th>Decision</th>
                <th>Rationale</th>
                <th>Supersedes chain</th>
                <th>Superseded by</th>
                <th>Tradeoffs</th>
              </tr>
            </thead>
            <tbody>
              {group.rows.map((row) => (
                <tr key={row.decision.id}>
                  <td>
                    <Link to={buildNodeLink(activeTab, row.decision.id)}>{row.decision.title}</Link>
                    <div>
                      <StatusBadge status={row.decision.status} />
                    </div>
                  </td>
                  <td>
                    {row.rationale.length === 0 ? (
                      <span className="faint" style={{ color: "var(--danger)" }}>
                        none
                      </span>
                    ) : (
                      row.rationale.map((r, i) => <div key={i}>{r.title}</div>)
                    )}
                  </td>
                  <td>{row.supersedes_chain.join(" → ") || <span className="faint">—</span>}</td>
                  <td>{row.superseded_by.join(", ") || <span className="faint">—</span>}</td>
                  <td>{row.tradeoffs || <span className="faint">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
