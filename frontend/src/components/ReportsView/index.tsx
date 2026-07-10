import { Link } from "react-router-dom";
import { useReports } from "../../api/queries";
import { EmptyState } from "../EmptyState";

/** CENTER, Reports tab: /reports -- lists saved report-bundle-v2 markdown files (s6-ui.md
 * §10.1/§9). */
export function ReportsView() {
  const { data, isPending, isError } = useReports();

  if (isPending) return <div className="spinner-line">Loading reports…</div>;
  if (isError) return <EmptyState>Failed to load reports.</EmptyState>;
  if (data.length === 0)
    return (
      <EmptyState>
        No saved reports yet. Ask your assistant for a project summary — it writes one to reports/, and it appears
        here.
      </EmptyState>
    );

  return (
    <ul className="report-list">
      {data.map((r) => (
        <li key={r.filename}>
          <Link to={`/reports/${encodeURIComponent(r.filename)}`}>
            <span>{r.filename}</span>
            <span className="faint">
              {(r.size / 1024).toFixed(1)} KB · {r.modified_at}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
