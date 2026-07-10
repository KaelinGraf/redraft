import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Dialog } from "./Dialog";
import { useOutline, useUploadAttachment } from "../../api/queries";
import { ApiError } from "../../api/client";
import { useActiveTab, buildNodeLink } from "../../lib/nav";

/** Upload (s6-ui.md §6/§10.1): file + title (Form), optional part_of (Form) -> POST
 * /api/attachments -> a new artifact node. 50 MiB cap is enforced server-side (ruling §14.2);
 * a 413's detail message (which states the limit) surfaces through the same ApiError path
 * every other dialog uses. */
export function UploadAttachmentDialog({
  defaultParentId,
  onClose,
}: {
  defaultParentId?: string;
  onClose: () => void;
}) {
  const { data: outline } = useOutline();
  const uploadAttachment = useUploadAttachment();
  const navigate = useNavigate();
  const activeTab = useActiveTab();

  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [partOf, setPartOf] = useState(defaultParentId ?? "");
  const [error, setError] = useState<string | null>(null);

  function submit() {
    if (!file) return;
    setError(null);
    uploadAttachment.mutate(
      { file, title: title || file.name, partOf: partOf || undefined },
      {
        onSuccess: (node) => {
          onClose();
          navigate(buildNodeLink(activeTab, node.id));
        },
        onError: (e) => setError(e instanceof ApiError ? e.detail : String(e)),
      },
    );
  }

  return (
    <Dialog title="Upload attachment" onClose={onClose} error={error}>
      <div className="field">
        <label className="field__label" htmlFor="ua-file">
          File
        </label>
        <input
          id="ua-file"
          type="file"
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            setFile(f);
            if (f && !title) setTitle(f.name);
          }}
        />
        <span className="field__hint">Max 50 MiB. Creates a new artifact node.</span>
      </div>
      <div className="field">
        <label className="field__label" htmlFor="ua-title">
          Title
        </label>
        <input id="ua-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="field">
        <label className="field__label" htmlFor="ua-parent">
          Parent (part of)
        </label>
        <select id="ua-parent" value={partOf} onChange={(e) => setPartOf(e.target.value)}>
          <option value="">(none — top level)</option>
          {(outline?.nodes ?? [])
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
        <button className="btn" onClick={onClose} disabled={uploadAttachment.isPending}>
          Cancel
        </button>
        <button className="btn btn--primary" onClick={submit} disabled={!file || !title.trim() || uploadAttachment.isPending}>
          {uploadAttachment.isPending ? "Uploading…" : "Upload"}
        </button>
      </div>
    </Dialog>
  );
}
