import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Dialog } from "./Dialog";
import { useCreateNode, useDedupHints, useOutline, useSchema } from "../../api/queries";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { useActiveTab, buildNodeLink } from "../../lib/nav";
import { ApiError } from "../../api/client";
import { TypeChip } from "../TypeChip";
import type { NodeType } from "../../api/types";

/** Typed create form with live debounced dedup hints (s6-ui.md §5.3, ruling: 300ms). */
export function CreateNodeDialog({ defaultParentId, onClose }: { defaultParentId?: string; onClose: () => void }) {
  const { data: schema } = useSchema();
  const { data: outline } = useOutline();
  const createNode = useCreateNode();
  const navigate = useNavigate();
  const activeTab = useActiveTab();

  const [type, setType] = useState<NodeType>("concept");
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState("");
  const [body, setBody] = useState("");
  const [partOf, setPartOf] = useState(defaultParentId ?? "");
  const [error, setError] = useState<string | null>(null);

  const debouncedTitle = useDebouncedValue(title, 300);
  const dedup = useDedupHints(debouncedTitle);

  const legalStatuses = schema?.status_by_type[type] ?? null;

  function submit() {
    setError(null);
    createNode.mutate(
      {
        type,
        title,
        body,
        status: legalStatuses ? status || undefined : undefined,
        part_of: partOf || undefined,
      },
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
    <Dialog title="New node" onClose={onClose} error={error}>
      <div className="field">
        <label className="field__label" htmlFor="cn-type">
          Type
        </label>
        <select
          id="cn-type"
          value={type}
          onChange={(e) => {
            setType(e.target.value as NodeType);
            setStatus("");
          }}
        >
          {(schema?.node_types ?? []).map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field__label" htmlFor="cn-title">
          Title
        </label>
        <input id="cn-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        {debouncedTitle.trim() ? (
          <div className="hint-list">
            {dedup.isFetching ? <span className="faint">checking for duplicates…</span> : null}
            {dedup.data?.degraded ? <span className="degraded-badge">text-match only</span> : null}
            {dedup.data?.hits.slice(0, 5).map((h) => (
              <div className="hint-item" key={h.node.id}>
                <span>
                  <TypeChip type={h.node.type} compact /> {h.node.title}
                </span>
                <span className="faint">{h.score.toFixed(2)}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>

      {legalStatuses ? (
        <div className="field">
          <label className="field__label" htmlFor="cn-status">
            Status
          </label>
          <select id="cn-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">(default)</option>
            {legalStatuses.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className="field">
        <label className="field__label" htmlFor="cn-parent">
          Parent (part of)
        </label>
        <select id="cn-parent" value={partOf} onChange={(e) => setPartOf(e.target.value)}>
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

      <div className="field">
        <label className="field__label" htmlFor="cn-body">
          Body
        </label>
        <textarea id="cn-body" value={body} onChange={(e) => setBody(e.target.value)} rows={6} />
      </div>

      <div className="dialog__actions">
        <button className="btn" onClick={onClose} disabled={createNode.isPending}>
          Cancel
        </button>
        <button className="btn btn--primary" onClick={submit} disabled={!title.trim() || createNode.isPending}>
          {createNode.isPending ? "Creating…" : "Create"}
        </button>
      </div>
    </Dialog>
  );
}
