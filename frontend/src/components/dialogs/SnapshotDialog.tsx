import { useState } from "react";
import { Dialog } from "./Dialog";
import { useGitStatus, useSnapshot } from "../../api/queries";
import { ApiError } from "../../api/client";
import type { SnapshotResult } from "../../api/types";

/** SnapshotButton -> SnapshotDialog (s6-ui.md §10.1) -- commit message + optional push ->
 * POST /api/snapshot -> GraphStore.snapshot. */
export function SnapshotDialog({ onClose }: { onClose: () => void }) {
  const { data: gitStatus } = useGitStatus();
  const snapshot = useSnapshot();
  const [message, setMessage] = useState("");
  const [push, setPush] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SnapshotResult | null>(null);

  function submit() {
    if (!message.trim()) return;
    setError(null);
    snapshot.mutate(
      { message, push },
      { onSuccess: setResult, onError: (e) => setError(e instanceof ApiError ? e.detail : String(e)) },
    );
  }

  if (result) {
    return (
      <Dialog title="Snapshot" onClose={onClose}>
        {result.committed ? (
          <p>
            Committed <code>{result.sha?.slice(0, 10)}</code>
            {result.initialized_repo ? " (initialized a new git repo)" : ""}
            {result.pushed ? " and pushed." : "."}
          </p>
        ) : (
          <p>Nothing to commit — the working tree was already clean.</p>
        )}
        <div className="dialog__actions">
          <button className="btn btn--primary" onClick={onClose}>
            Close
          </button>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title="Snapshot" onClose={onClose} error={error}>
      {gitStatus && gitStatus.changed_paths.length > 0 ? (
        <div className="card">
          <div className="card__title">Changed paths ({gitStatus.changed_paths.length})</div>
          {gitStatus.changed_paths.slice(0, 12).map((p) => (
            <div key={p} className="faint">
              {p}
            </div>
          ))}
        </div>
      ) : (
        <p className="faint">No changes detected.</p>
      )}
      <div className="field">
        <label className="field__label" htmlFor="snap-message">
          Commit message
        </label>
        <textarea
          id="snap-message"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          rows={3}
          autoFocus
        />
      </div>
      <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
        <input id="snap-push" type="checkbox" checked={push} onChange={(e) => setPush(e.target.checked)} />
        <label htmlFor="snap-push">Push after commit</label>
      </div>
      <div className="dialog__actions">
        <button className="btn" onClick={onClose} disabled={snapshot.isPending}>
          Cancel
        </button>
        <button className="btn btn--primary" onClick={submit} disabled={!message.trim() || snapshot.isPending}>
          {snapshot.isPending ? "Committing…" : "Commit"}
        </button>
      </div>
    </Dialog>
  );
}
