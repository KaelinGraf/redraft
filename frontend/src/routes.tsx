import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { NodeDetail } from "./components/NodeDetail";
import { DocView } from "./components/DocView";
import { TablesView } from "./components/TablesView";
import { ReportsView } from "./components/ReportsView";
import { ReportMarkdownView } from "./components/ReportsView/ReportMarkdownView";

// MapView and TimelineView both render lazily -- only mounted (and only fetched as their own
// JS chunk) when their route is actually active (s6-ui.md §10.1/§11 for Map; same reasoning
// extends to Timeline, a supplementary planning view, not part of the core Outline/Doc/Tables
// authoring path): neither tab's bundle cost is paid on first load of the higher-priority tabs.
const MapView = lazy(() => import("./components/MapView"));
const TimelineView = lazy(() => import("./components/TimelineView"));

/** react-router-dom route table (s6-ui.md §2's frontend layout). Node-scoped tabs
 * (outline/doc/tables) have both a bare and an :id-suffixed route so the center pane can show
 * an empty state before anything is selected. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/outline" replace />} />
      <Route path="/outline" element={<NodeDetail />} />
      <Route path="/outline/:nodeId" element={<NodeDetail />} />
      <Route path="/doc" element={<DocView />} />
      <Route path="/doc/:rootId" element={<DocView />} />
      <Route path="/tables" element={<TablesView />} />
      <Route path="/tables/:rootId" element={<TablesView />} />
      <Route
        path="/map"
        element={
          <Suspense fallback={<div className="spinner-line">Loading map…</div>}>
            <MapView />
          </Suspense>
        }
      />
      <Route
        path="/timeline"
        element={
          <Suspense fallback={<div className="spinner-line">Loading timeline…</div>}>
            <TimelineView />
          </Suspense>
        }
      />
      <Route path="/reports" element={<ReportsView />} />
      <Route path="/reports/:filename" element={<ReportMarkdownView />} />
      <Route path="*" element={<Navigate to="/outline" replace />} />
    </Routes>
  );
}
