import { useState } from "react";
import { useOutline } from "../../api/queries";
import { useActiveTab, useSelectedNodeId } from "../../lib/nav";
import { OutlineFilterBox } from "./OutlineFilterBox";
import { NewNodeButton } from "./NewNodeButton";
import { SpineTree } from "./SpineTree";

/** LEFT pane -- s6-ui.md §10.1. Rendered as a persistent sibling of the routed center pane
 * (every tab keeps the spine tree visible), so it reads the selected id/tab straight off the
 * URL (see lib/nav.ts) rather than through route-nested props. */
export function OutlinePane() {
  const { data, isPending, isError } = useOutline();
  const [filter, setFilter] = useState("");
  const activeTab = useActiveTab();
  const selectedId = useSelectedNodeId();

  return (
    <div className="pane pane--outline">
      <OutlineFilterBox value={filter} onChange={setFilter} />
      <div className="outline-actions">
        <NewNodeButton defaultParentId={selectedId} />
      </div>
      {isPending ? (
        <div className="spinner-line">Loading outline…</div>
      ) : isError ? (
        <div className="spinner-line">Failed to load outline.</div>
      ) : (
        <SpineTree
          nodes={data.nodes}
          edges={data.edges}
          filter={filter}
          selectedId={selectedId}
          activeTab={activeTab}
        />
      )}
    </div>
  );
}
