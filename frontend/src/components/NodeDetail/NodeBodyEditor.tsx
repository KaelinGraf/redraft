import { useState } from "react";
import { useUpdateNode } from "../../api/queries";
import type { NodeOut } from "../../api/types";

/** Markdown textarea, Edit/Save (s6-ui.md §10.1). No markdown *renderer* is wired -- §10.3's
 * package table pins no markdown library, so body text renders as preserved plain text
 * (white-space: pre-wrap) rather than parsed HTML; adding a rendering dependency the design
 * never called for would be scope creep. A separate quick-append box exposes update_node's
 * `mode="append"` path (the full editor below always uses mode="replace" -- it's editing the
 * complete current text, not an addendum). */
export function NodeBodyEditor({ node }: { node: NodeOut }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(node.body);
  const [appendText, setAppendText] = useState("");
  const [appending, setAppending] = useState(false);
  const updateNode = useUpdateNode();

  function startEdit() {
    setDraft(node.body);
    setEditing(true);
  }

  function save() {
    updateNode.mutate(
      { id: node.id, body: { body: draft, mode: "replace" } },
      { onSuccess: () => setEditing(false) },
    );
  }

  function submitAppend() {
    if (!appendText.trim()) return;
    updateNode.mutate(
      { id: node.id, body: { body: appendText, mode: "append" } },
      {
        onSuccess: () => {
          setAppendText("");
          setAppending(false);
        },
      },
    );
  }

  return (
    <div className="card">
      <div className="card__title" style={{ display: "flex", justifyContent: "space-between" }}>
        <span>Body</span>
        <span style={{ display: "flex", gap: 6 }}>
          {!editing && (
            <>
              <button className="btn btn--sm" onClick={() => setAppending((v) => !v)}>
                Append
              </button>
              <button className="btn btn--sm" onClick={startEdit}>
                Edit
              </button>
            </>
          )}
        </span>
      </div>

      {editing ? (
        <>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={16}
            style={{ width: "100%" }}
            autoFocus
          />
          {updateNode.isError ? (
            <div className="faint" style={{ color: "var(--danger)" }}>
              {updateNode.error.message}
            </div>
          ) : null}
          <div className="dialog__actions">
            <button className="btn" onClick={() => setEditing(false)} disabled={updateNode.isPending}>
              Cancel
            </button>
            <button className="btn btn--primary" onClick={save} disabled={updateNode.isPending}>
              {updateNode.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      ) : (
        <div className="body-view">{node.body || <span className="faint">(empty)</span>}</div>
      )}

      {appending ? (
        <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          <textarea
            value={appendText}
            onChange={(e) => setAppendText(e.target.value)}
            rows={4}
            style={{ width: "100%" }}
            placeholder="Text to append to the end of the body…"
            autoFocus
          />
          <div className="dialog__actions">
            <button className="btn" onClick={() => setAppending(false)} disabled={updateNode.isPending}>
              Cancel
            </button>
            <button className="btn btn--primary" onClick={submitAppend} disabled={updateNode.isPending}>
              {updateNode.isPending ? "Appending…" : "Append"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
