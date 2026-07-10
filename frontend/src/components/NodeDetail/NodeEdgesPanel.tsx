import { useState } from "react";
import { Link } from "react-router-dom";
import { useDeleteEdge } from "../../api/queries";
import { useActiveTab, buildNodeLink } from "../../lib/nav";
import { EDGE_TYPE_LABEL } from "../../lib/palette";
import { LinkEdgeDialog } from "../dialogs/LinkEdgeDialog";
import { BatchLinkDialog } from "../dialogs/BatchLinkDialog";
import { LINK_EDGE_TYPES } from "../../api/types";
import type { NeighborEdge, NodeOut } from "../../api/types";

/** Only the edge types that actually have >=1 link for this node get a group (operator-UI
 * review finding 1): rendering all 8 link-capable edge types regardless of content buried real
 * content under ~450px of "none" scaffolding on a typical node. A single "+ Add link" control
 * opens LinkEdgeDialog, which now owns its own type <select> rather than being pre-scoped by a
 * per-group button. "Batch link…" in the card header -> BatchLinkDialog is unchanged. */
export function NodeEdgesPanel({ node, edges, neighbors }: { node: NodeOut; edges: NeighborEdge[]; neighbors: NodeOut[] }) {
  const [linking, setLinking] = useState(false);
  const [showBatch, setShowBatch] = useState(false);
  const titleById = new Map(neighbors.map((n) => [n.id, n.title]));
  const deleteEdge = useDeleteEdge();
  const activeTab = useActiveTab();

  const populatedTypes = LINK_EDGE_TYPES.filter((type) => edges.some((e) => e.type === type));

  return (
    <div className="card">
      <div className="card__title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>Links</span>
        <button className="btn btn--sm" onClick={() => setShowBatch(true)}>
          Batch link…
        </button>
      </div>
      {populatedTypes.length === 0 ? (
        <div className="faint">No links yet — connect this node to the graph.</div>
      ) : (
        populatedTypes.map((type) => {
          const rows = edges.filter((e) => e.type === type);
          return (
            <div className="edge-group" key={type}>
              <div className="edge-group__label">{EDGE_TYPE_LABEL[type]}</div>
              {rows.map((e) => {
                const otherId = e.direction === "out" ? e.dst : e.src;
                return (
                  <div className="edge-row" key={`${e.src}|${e.dst}|${e.type}`}>
                    <span>
                      {e.direction === "out" ? "→ " : "← "}
                      <Link to={buildNodeLink(activeTab, otherId)}>{titleById.get(otherId) ?? otherId}</Link>
                    </span>
                    <button
                      className="btn btn--sm"
                      onClick={() =>
                        window.confirm(`Unlink ${type} to ${titleById.get(otherId) ?? otherId}?`) &&
                        deleteEdge.mutate({ src: e.src, dst: e.dst, type: e.type })
                      }
                      aria-label="Unlink"
                    >
                      ×
                    </button>
                  </div>
                );
              })}
            </div>
          );
        })
      )}
      <button className="btn btn--sm" onClick={() => setLinking(true)}>
        + Add link
      </button>
      {linking ? <LinkEdgeDialog nodeId={node.id} onClose={() => setLinking(false)} /> : null}
      {showBatch ? <BatchLinkDialog nodeId={node.id} onClose={() => setShowBatch(false)} /> : null}
    </div>
  );
}
