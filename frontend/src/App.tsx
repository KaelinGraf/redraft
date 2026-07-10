import { TopBar } from "./components/TopBar";
import { TabNav } from "./components/TabNav";
import { OutlinePane } from "./components/OutlinePane";
import { AttentionStrip } from "./components/AttentionStrip";
import { AppRoutes } from "./routes";
import { useActiveTab } from "./lib/nav";

/** Router root + AppShell, merged into one component (s6-ui.md §10.1's App/AppShell split
 * has no behavior of its own to separate -- App just renders the shell). Outline-first
 * three-pane: spine tree (left) | routed center content | attention strip (right), persistent
 * across every tab -- only CenterPane's content actually routes.
 *
 * Map and Timeline are the one exception (operator-UI review finding 3): neither is
 * node-scoped (lib/nav.ts's NODE_SCOPED_TABS), so neither the spine tree nor the attention
 * strip does anything useful there -- clicking a node in either view already navigates to
 * `/outline/:id` via buildNodeLink, so nothing is lost by not rendering the tree/strip chrome
 * around them. Reports stays three-pane: it's a list, not width-hungry. */
export function App() {
  const activeTab = useActiveTab();
  const isWideView = activeTab === "map" || activeTab === "timeline";

  return (
    <div className="app-shell">
      <TopBar />
      <TabNav />
      <div className={isWideView ? "three-pane three-pane--wide" : "three-pane"}>
        {isWideView ? null : <OutlinePane />}
        <div className="pane pane--center">
          <AppRoutes />
        </div>
        {isWideView ? null : (
          <div className="pane pane--attention">
            <AttentionStrip />
          </div>
        )}
      </div>
    </div>
  );
}
