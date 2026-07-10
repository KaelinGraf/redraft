import { useState } from "react";
import { Dialog } from "./Dialog";
import { useCreateEdge, useOutline } from "../../api/queries";
import { ApiError } from "../../api/client";
import { EDGE_TYPE_LABEL } from "../../lib/palette";
import { LINK_EDGE_TYPES } from "../../api/types";
import type { EdgeType } from "../../api/types";

type LinkDirection = "out" | "in";

/** Link/unlink with an edge-type picker (s6-ui.md §10.1) -- opened from NodeEdgesPanel's single
 * "+ Add link" control. Finding 1 of the operator-UI review: the type is no longer pre-scoped
 * by which per-type group's button was clicked (there is no such button anymore) -- this
 * dialog now owns its own type <select>, built from the exact same LINK_EDGE_TYPES/
 * EDGE_TYPE_LABEL source BatchLinkDialog's type picker already reuses, so there is still only
 * one list of edge types in the codebase. */
export function LinkEdgeDialog({ nodeId, onClose }: { nodeId: string; onClose: () => void }) {
  const { data: outline } = useOutline();
  const createEdge = useCreateEdge();
  const [edgeType, setEdgeType] = useState<EdgeType>(LINK_EDGE_TYPES[0]);
  const [direction, setDirection] = useState<LinkDirection>("out");
  const [target, setTarget] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit() {
    if (!target) return;
    setError(null);
    const body =
      direction === "out"
        ? { src: nodeId, dst: target, type: edgeType }
        : { src: target, dst: nodeId, type: edgeType };
    createEdge.mutate(body, { onSuccess: onClose, onError: (e) => setError(e instanceof ApiError ? e.detail : String(e)) });
  }

  return (
    <Dialog title="Add link" onClose={onClose} error={error}>
      <div className="field">
        <label className="field__label" htmlFor="le-type">
          Edge type
        </label>
        <select id="le-type" value={edgeType} onChange={(e) => setEdgeType(e.target.value as EdgeType)} autoFocus>
          {LINK_EDGE_TYPES.map((t) => (
            <option key={t} value={t}>
              {EDGE_TYPE_LABEL[t]}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label className="field__label">Direction</label>
        <select value={direction} onChange={(e) => setDirection(e.target.value as LinkDirection)}>
          <option value="out">This node {EDGE_TYPE_LABEL[edgeType]} →</option>
          <option value="in">→ this node {EDGE_TYPE_LABEL[edgeType]}</option>
        </select>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="le-target">
          Target node
        </label>
        <select id="le-target" value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">(choose a node)</option>
          {(outline?.nodes ?? [])
            .filter((n) => n.id !== nodeId)
            .slice()
            .sort((a, b) => a.title.localeCompare(b.title))
            .map((n) => (
              <option key={n.id} value={n.id}>
                {n.title}
              </option>
            ))}
        </select>
      </div>
      <div className="dialog__actions">
        <button className="btn" onClick={onClose} disabled={createEdge.isPending}>
          Cancel
        </button>
        <button className="btn btn--primary" onClick={submit} disabled={!target || createEdge.isPending}>
          {createEdge.isPending ? "Linking…" : "Link"}
        </button>
      </div>
    </Dialog>
  );
}
