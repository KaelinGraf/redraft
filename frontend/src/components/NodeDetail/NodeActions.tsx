import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDeleteNode, useOutline, useRenameNode, useReparentNode } from "../../api/queries";
import { ApiError } from "../../api/client";
import { MergeDialog } from "../dialogs/MergeDialog";
import { UploadAttachmentDialog } from "../dialogs/UploadAttachmentDialog";
import type { NeighborEdge, NodeOut } from "../../api/types";

type Mode = null | "rename" | "reparent";

/** Rename, Reparent, Merge -> MergeDialog, Delete, Upload -> UploadAttachmentDialog
 * (s6-ui.md §10.1). Rename/Reparent are simple enough to stay inline (a text input / a
 * select swapped in for the button row) rather than earning their own modal -- Merge and
 * Upload need real forms (a second-node picker with consequences to review; a file input),
 * which is exactly why the design names dialogs for those two specifically. */
export function NodeActions({ node, edges }: { node: NodeOut; edges: NeighborEdge[] }) {
  // NodeOut (redraft.models) carries no `part_of` field -- part_of is edge-shaped, not a
  // node property (schema.Node.part_of is store-internal; the wire model deliberately omits
  // it, s6-ui.md §9's NodeOut reuse). The current parent is the OUT part_of edge in this
  // node's own direct-edges list, exactly what NodeEdgesPanel already receives.
  const currentParentId = edges.find((e) => e.type === "part_of" && e.direction === "out")?.dst ?? null;
  const [mode, setMode] = useState<Mode>(null);
  const [showMerge, setShowMerge] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const renameNode = useRenameNode();
  const reparentNode = useReparentNode();
  const deleteNode = useDeleteNode();
  const { data: outline } = useOutline();

  const [renameDraft, setRenameDraft] = useState(node.title);
  const [reparentDraft, setReparentDraft] = useState(currentParentId ?? "");

  function reportError(e: unknown) {
    setError(e instanceof ApiError ? e.detail : String(e));
  }

  if (mode === "rename") {
    return (
      <div className="node-actions">
        <input
          type="text"
          value={renameDraft}
          onChange={(e) => setRenameDraft(e.target.value)}
          autoFocus
          style={{ minWidth: 220 }}
        />
        <button
          className="btn btn--primary btn--sm"
          disabled={renameNode.isPending}
          onClick={() => {
            setError(null);
            renameNode.mutate(
              { id: node.id, newTitle: renameDraft },
              {
                onSuccess: (result) => {
                  setMode(null);
                  navigate(`/outline/${encodeURIComponent(result.new_id)}`);
                },
                onError: reportError,
              },
            );
          }}
        >
          {renameNode.isPending ? "Renaming…" : "Save"}
        </button>
        <button className="btn btn--sm" onClick={() => setMode(null)}>
          Cancel
        </button>
        {error ? <span className="faint" style={{ color: "var(--danger)" }}>{error}</span> : null}
      </div>
    );
  }

  if (mode === "reparent") {
    return (
      <div className="node-actions">
        <select value={reparentDraft} onChange={(e) => setReparentDraft(e.target.value)} autoFocus>
          <option value="">(none — top level)</option>
          {outline?.nodes
            .filter((n) => n.id !== node.id)
            .sort((a, b) => a.title.localeCompare(b.title))
            .map((n) => (
              <option key={n.id} value={n.id}>
                {n.title}
              </option>
            ))}
        </select>
        <button
          className="btn btn--primary btn--sm"
          disabled={reparentNode.isPending}
          onClick={() => {
            setError(null);
            reparentNode.mutate(
              { id: node.id, newParent: reparentDraft || null },
              { onSuccess: () => setMode(null), onError: reportError },
            );
          }}
        >
          {reparentNode.isPending ? "Moving…" : "Save"}
        </button>
        <button className="btn btn--sm" onClick={() => setMode(null)}>
          Cancel
        </button>
        {error ? <span className="faint" style={{ color: "var(--danger)" }}>{error}</span> : null}
      </div>
    );
  }

  return (
    <div className="node-actions">
      <button
        className="btn btn--sm"
        onClick={() => {
          setRenameDraft(node.title);
          setError(null);
          setMode("rename");
        }}
      >
        Rename
      </button>
      <button
        className="btn btn--sm"
        onClick={() => {
          setReparentDraft(currentParentId ?? "");
          setError(null);
          setMode("reparent");
        }}
      >
        Reparent
      </button>
      <button className="btn btn--sm" onClick={() => setShowMerge(true)}>
        Merge…
      </button>
      <button className="btn btn--sm" onClick={() => setShowUpload(true)}>
        Upload attachment…
      </button>
      <button
        className="btn btn--sm btn--danger"
        disabled={deleteNode.isPending}
        onClick={() => {
          if (!window.confirm(`Delete "${node.title}"? This cannot be undone.`)) return;
          deleteNode.mutate(node.id, { onSuccess: () => navigate("/outline") });
        }}
      >
        Delete
      </button>
      {error ? <span className="faint" style={{ color: "var(--danger)" }}>{error}</span> : null}
      {showMerge ? <MergeDialog nodeId={node.id} onClose={() => setShowMerge(false)} /> : null}
      {showUpload ? (
        <UploadAttachmentDialog defaultParentId={node.id} onClose={() => setShowUpload(false)} />
      ) : null}
    </div>
  );
}
