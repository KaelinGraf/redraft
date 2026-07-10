import { useState } from "react";
import { Dialog } from "./Dialog";
import { OutlineFilterBox } from "../OutlinePane/OutlineFilterBox";
import { TypeChip } from "../TypeChip";
import { useCreateEdges, useOutline } from "../../api/queries";
import { ApiError } from "../../api/client";
import { EDGE_TYPE_LABEL } from "../../lib/palette";
import { LINK_EDGE_TYPES } from "../../api/types";
import type { EdgeRequest, EdgeType } from "../../api/types";

type LinkDirection = "out" | "in";

/** Batch link/unlink's own bigger sibling (s6-ui.md §10.1's NodeEdgesPanel, "Batch link…" in
 * the Links card header, next to the per-type "+ link" -> LinkEdgeDialog buttons -- neither
 * removed nor changed). One edge type + one direction (same picker/label source
 * LINK_EDGE_TYPES/EDGE_TYPE_LABEL that NodeEdgesPanel's own group loop and LinkEdgeDialog's
 * direction field already render from -- there is no separate extractable "edge type picker"
 * component today, this reuses the exact same constants rather than inventing a second list),
 * applied to a multi-selected set of target nodes -- reuses OutlineFilterBox verbatim (the
 * app's one node-search/typeahead input) over the already-loaded `useOutline` list every other
 * node picker in this file tree already pulls from (LinkEdgeDialog/MergeDialog/CreateNodeDialog),
 * filtered with the exact same title-or-type substring predicate SpineTree uses. One
 * POST /api/edges/batch call -> useCreateEdges -> the same broad useGraphMutation invalidation
 * useCreateEdge already gets, so a success closes the dialog and refreshes everything the
 * single-link path refreshes. On failure (the whole batch is atomic -- s6-ui.md create_edges
 * contract) the selections and open dialog are left untouched so the operator can fix and
 * retry. */
export function BatchLinkDialog({ nodeId, onClose }: { nodeId: string; onClose: () => void }) {
  const { data: outline } = useOutline();
  const createEdges = useCreateEdges();
  const [type, setType] = useState<EdgeType>(LINK_EDGE_TYPES[0]);
  const [direction, setDirection] = useState<LinkDirection>("out");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);

  const trimmedFilter = filter.trim().toLowerCase();
  const candidates = (outline?.nodes ?? []).filter((n) => n.id !== nodeId);
  const visible = (
    trimmedFilter
      ? candidates.filter((n) => n.title.toLowerCase().includes(trimmedFilter) || n.type.includes(trimmedFilter))
      : candidates
  )
    .slice()
    .sort((a, b) => a.title.localeCompare(b.title));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function submit() {
    if (selected.size === 0) return;
    setError(null);
    const edges: EdgeRequest[] = Array.from(selected).map((otherId) =>
      direction === "out" ? { src: nodeId, dst: otherId, type } : { src: otherId, dst: nodeId, type },
    );
    createEdges.mutate(
      { edges },
      { onSuccess: onClose, onError: (e) => setError(e instanceof ApiError ? e.detail : String(e)) },
    );
  }

  return (
    <Dialog title="Batch link" onClose={onClose} error={error}>
      <div className="field">
        <label className="field__label" htmlFor="bl-type">
          Edge type
        </label>
        <select id="bl-type" value={type} onChange={(e) => setType(e.target.value as EdgeType)} autoFocus>
          {LINK_EDGE_TYPES.map((t) => (
            <option key={t} value={t}>
              {EDGE_TYPE_LABEL[t]}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field__label">Direction</label>
        <div style={{ display: "flex", gap: 16 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <input type="radio" name="bl-direction" checked={direction === "out"} onChange={() => setDirection("out")} />
            This node {EDGE_TYPE_LABEL[type]} → selected
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <input type="radio" name="bl-direction" checked={direction === "in"} onChange={() => setDirection("in")} />
            Selected {EDGE_TYPE_LABEL[type]} → this node
          </label>
        </div>
      </div>

      <div className="field">
        <label className="field__label">
          Target nodes{selected.size > 0 ? ` (${selected.size} selected)` : ""}
        </label>
        <OutlineFilterBox value={filter} onChange={setFilter} />
        <div className="pick-list">
          {visible.length === 0 ? (
            <div className="faint" style={{ padding: 6 }}>
              No matching nodes.
            </div>
          ) : (
            visible.map((n) => (
              <label className="hint-item" key={n.id} style={{ cursor: "pointer" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 6, overflow: "hidden" }}>
                  <input type="checkbox" checked={selected.has(n.id)} onChange={() => toggle(n.id)} />
                  <TypeChip type={n.type} compact />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.title}</span>
                </span>
              </label>
            ))
          )}
        </div>
      </div>

      <div className="dialog__actions">
        <button className="btn" onClick={onClose} disabled={createEdges.isPending}>
          Cancel
        </button>
        <button className="btn btn--primary" onClick={submit} disabled={selected.size === 0 || createEdges.isPending}>
          {createEdges.isPending ? "Linking…" : `Create ${selected.size} link${selected.size === 1 ? "" : "s"}`}
        </button>
      </div>
    </Dialog>
  );
}
