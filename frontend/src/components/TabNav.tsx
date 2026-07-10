import { Link } from "react-router-dom";
import { useActiveTab, useSelectedNodeId, type TabKey } from "../lib/nav";

const TABS: { key: TabKey; label: string; nodeScoped: boolean }[] = [
  { key: "outline", label: "Outline", nodeScoped: true },
  { key: "doc", label: "Doc", nodeScoped: true },
  { key: "tables", label: "Tables", nodeScoped: true },
  { key: "map", label: "Map", nodeScoped: false },
  { key: "timeline", label: "Timeline", nodeScoped: false },
  { key: "reports", label: "Reports", nodeScoped: false },
];

/** Outline | Doc | Tables | Map | Timeline | Reports (s6-ui.md §10.1 + Timeline addendum).
 * Switching between node-scoped tabs keeps the currently selected node id (it's a shared route
 * param, §10.2); Map/Timeline/Reports have no per-node route. */
export function TabNav() {
  const active = useActiveTab();
  const selectedId = useSelectedNodeId();

  return (
    <nav className="tabnav">
      {TABS.map((tab) => {
        const to = tab.nodeScoped && selectedId ? `/${tab.key}/${encodeURIComponent(selectedId)}` : `/${tab.key}`;
        return (
          <Link key={tab.key} to={to} className={`tabnav__link${active === tab.key ? " active" : ""}`}>
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
