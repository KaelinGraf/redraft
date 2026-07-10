import type { ReactNode } from "react";
import { useEffect } from "react";

/** Shared modal shell (overlay + panel + Esc-to-close) -- the one piece genuinely reused by
 * all five dialogs (CreateNodeDialog, LinkEdgeDialog, MergeDialog, UploadAttachmentDialog,
 * SnapshotDialog), so it earns its own file per this project's own "three call sites is the
 * earliest a helper is warranted" rule (five, here). */
export function Dialog({
  title,
  onClose,
  children,
  error,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  error?: string | null;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="dialog__header">
          <h2>{title}</h2>
          <button className="dialog__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {error ? <div className="dialog__error">{error}</div> : null}
        {children}
      </div>
    </div>
  );
}
