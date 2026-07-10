import { useLocation } from "react-router-dom";

export type TabKey = "outline" | "doc" | "tables" | "map" | "timeline" | "reports";
const NODE_SCOPED_TABS: TabKey[] = ["outline", "doc", "tables"];

/** TopBar/TabNav/OutlinePane render as SIBLINGS of the routed <CenterPane> (s6-ui.md §10.1's
 * three-pane shell -- only the center content routes, the outer chrome doesn't), so they
 * can't use react-router's useParams() (it only resolves inside a matched <Route> element's
 * own subtree). Both derive what they need straight from the URL instead. */
export function useActiveTab(): TabKey {
  const first = useLocation().pathname.split("/").filter(Boolean)[0];
  return (
    first === "doc" || first === "tables" || first === "map" || first === "timeline" || first === "reports"
      ? first
      : "outline"
  );
}

export function useSelectedNodeId(): string | undefined {
  const tab = useActiveTab();
  const parts = useLocation().pathname.split("/").filter(Boolean);
  return NODE_SCOPED_TABS.includes(tab) && parts[1] ? decodeURIComponent(parts[1]) : undefined;
}

/** Where clicking a node in the spine tree / attention strip / search results / map / timeline
 * should go. Node-scoped tabs (Outline/Doc/Tables) keep you on the same tab, just retargeted;
 * Map and Timeline have no per-node detail view of their own and Reports' own node click is
 * defined as "back out of the report and onto this node" (s6-ui.md §10.1) -- all three land on
 * Outline, reusing the SAME NodeDetail rather than each growing its own. */
export function buildNodeLink(activeTab: TabKey, nodeId: string): string {
  const base = NODE_SCOPED_TABS.includes(activeTab) ? activeTab : "outline";
  return `/${base}/${encodeURIComponent(nodeId)}`;
}
