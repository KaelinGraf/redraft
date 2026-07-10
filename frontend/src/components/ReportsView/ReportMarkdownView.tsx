import { Link, useParams } from "react-router-dom";
import { useReport } from "../../api/queries";
import { EmptyState } from "../EmptyState";

/** CENTER, Reports tab: /reports/:filename (s6-ui.md §10.1). Raw markdown, preserved
 * whitespace -- same "no rendering dependency was pinned" call as NodeBodyEditor. */
export function ReportMarkdownView() {
  const { filename } = useParams<{ filename: string }>();
  const { data, isPending, isError, error } = useReport(filename);

  if (!filename) return <EmptyState>Select a report.</EmptyState>;
  if (isPending) return <div className="spinner-line">Loading report…</div>;
  if (isError) return <EmptyState>{error instanceof Error ? error.message : "Failed to load report."}</EmptyState>;

  return (
    <div>
      <div style={{ marginBottom: 10 }}>
        <Link to="/reports">← All reports</Link>
      </div>
      <h2>{data.filename}</h2>
      <div className="body-view">{data.content}</div>
    </div>
  );
}
