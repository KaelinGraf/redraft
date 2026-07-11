import { lazy, Suspense } from "react";
import { useParams } from "react-router-dom";
import { ReportMarkdownView } from "./ReportMarkdownView";

// .tex rendering (unified-latex + katex) is a real bundle-size cost -- lazy-chunked with this
// route, same pattern as MapView/TimelineView in routes.tsx, so .md-only sessions never pay it.
const ReportTexView = lazy(() => import("./ReportTexView"));

/** CENTER, Reports tab: /reports/:filename -- dispatches on extension. .md keeps the existing
 * raw-text rendering unchanged (ReportMarkdownView); .tex renders via unified-latex
 * (ReportTexView, organizing-protocol.md §7). */
export function ReportView() {
  const { filename } = useParams<{ filename: string }>();

  if (filename?.toLowerCase().endsWith(".tex")) {
    return (
      <Suspense fallback={<div className="spinner-line">Loading report…</div>}>
        <ReportTexView />
      </Suspense>
    );
  }
  return <ReportMarkdownView />;
}
