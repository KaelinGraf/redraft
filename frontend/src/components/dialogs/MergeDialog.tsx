import { useState } from "react";
import { Dialog } from "./Dialog";
import { useMergeNodes, useOutline } from "../../api/queries";
import { ApiError } from "../../api/client";
import type { MergeResult } from "../../api/types";

type Step = "pick" | "confirm" | "done";

/** Merge two-step (s6-ui.md brief): pick the node to drop, confirm the irreversible delete,
 * then show what GraphStore actually did (warnings + a preview of the discarded body -- body
 * is never auto-merged, s1-storage.md's own merge semantics) so the operator can see anything
 * surprising rather than the dialog just silently closing. */
export function MergeDialog({ nodeId, onClose }: { nodeId: string; onClose: () => void }) {
  const { data: outline } = useOutline();
  const mergeNodes = useMergeNodes();
  const [step, setStep] = useState<Step>("pick");
  const [dropId, setDropId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MergeResult | null>(null);

  const keepTitle = outline?.nodes.find((n) => n.id === nodeId)?.title ?? nodeId;
  const dropTitle = outline?.nodes.find((n) => n.id === dropId)?.title ?? dropId;

  function confirmMerge() {
    setError(null);
    mergeNodes.mutate(
      { id: nodeId, dropId },
      {
        onSuccess: (r) => {
          setResult(r);
          setStep("done");
        },
        onError: (e) => setError(e instanceof ApiError ? e.detail : String(e)),
      },
    );
  }

  if (step === "done" && result) {
    return (
      <Dialog title="Merge complete" onClose={onClose}>
        <p>
          Kept <strong>{result.kept.title}</strong>; deleted <strong>{result.dropped_id}</strong>.
        </p>
        {result.warnings.length > 0 ? (
          <div className="card">
            <div className="card__title">Warnings</div>
            {result.warnings.map((w, i) => (
              <div key={i} className="faint" style={{ color: "var(--danger)" }}>
                {w}
              </div>
            ))}
          </div>
        ) : null}
        {result.dropped_body_preview ? (
          <div className="card">
            <div className="card__title">Discarded body preview</div>
            <div className="body-view">{result.dropped_body_preview}</div>
          </div>
        ) : null}
        <div className="dialog__actions">
          <button className="btn btn--primary" onClick={onClose}>
            Close
          </button>
        </div>
      </Dialog>
    );
  }

  if (step === "confirm") {
    return (
      <Dialog title="Confirm merge" onClose={onClose} error={error}>
        <p>
          Merge <strong>{dropTitle}</strong> into <strong>{keepTitle}</strong>?
        </p>
        <p className="faint">
          {dropTitle} will be permanently deleted. Its outbound edges migrate to {keepTitle} (deduplicated); its
          body is discarded (never auto-merged). This cannot be undone.
        </p>
        <div className="dialog__actions">
          <button className="btn" onClick={() => setStep("pick")} disabled={mergeNodes.isPending}>
            Back
          </button>
          <button className="btn btn--danger" onClick={confirmMerge} disabled={mergeNodes.isPending}>
            {mergeNodes.isPending ? "Merging…" : "Merge and delete"}
          </button>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title="Merge into this node" onClose={onClose} error={error}>
      <p className="faint">Pick the node to absorb into "{keepTitle}". The picked node will be deleted.</p>
      <div className="field">
        <label className="field__label" htmlFor="merge-drop">
          Node to drop
        </label>
        <select id="merge-drop" value={dropId} onChange={(e) => setDropId(e.target.value)} autoFocus>
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
        <button className="btn" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn--primary" onClick={() => setStep("confirm")} disabled={!dropId}>
          Next
        </button>
      </div>
    </Dialog>
  );
}
