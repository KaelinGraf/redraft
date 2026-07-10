import { useState } from "react";
import { useUpdateNode } from "../../api/queries";
import type { NodeOut } from "../../api/types";

interface Row {
  key: string;
  value: unknown;
  edited: boolean;
}

// start/due are owned by DateQuickEdit below, not the generic row editor, WHILE DateQuickEdit
// is actually shown (operator-UI review finding 4: a `concept` node has no business showing
// Start/Due inputs, so DateQuickEdit is now conditional -- see `showDates` in
// NodePropertiesEditor below). `hideDateKeys` mirrors that same condition here so there is
// always exactly ONE editing surface for start/due: when DateQuickEdit is hidden, these two
// keys fall back to the generic row editor rather than becoming uneditable. Safe to just omit
// them from a save() payload when DateQuickEdit does own them, rather than needing to
// preserve/resend them: GraphStore.update_node MERGES properties onto the existing dict
// (`new_properties.update(properties)`, store.py) -- it's not a replace -- so a row-editor Save
// that never mentions start/due leaves whatever DateQuickEdit most recently wrote completely
// untouched.
const DATE_KEYS = new Set(["start", "due"]);

function toRows(properties: Record<string, unknown>, hideDateKeys: boolean): Row[] {
  return Object.entries(properties)
    .filter(([key]) => !hideDateKeys || !DATE_KEYS.has(key))
    .map(([key, value]) => ({ key, value, edited: false }));
}

function displayValue(row: Row): string {
  return typeof row.value === "string" ? row.value : JSON.stringify(row.value);
}

/** Start/Due date quick-edit (organizing-protocol.md's Planning dates convention,
 * `properties.start`/`.due`, read by the Timeline tab). Always live -- committed on change,
 * not gated behind the batch row editor's Edit/Save toggle below -- the same pattern
 * NodeHeader's status <select> already uses for a single well-defined field. Reuses the SAME
 * PATCH /api/nodes/{id} properties update the row editor below uses; no new endpoint (the
 * backend added none for writes).
 *
 * Clearing a date sends `remove_properties: [key]` so the key is actually deleted server-side
 * (GraphStore.update_node now supports this -- see store.py's update_node docstring), not left
 * behind as `""`. Setting a date still goes through the ordinary `properties` merge. */
function DateQuickEdit({ node }: { node: NodeOut }) {
  const updateNode = useUpdateNode();
  const start = typeof node.properties.start === "string" ? node.properties.start : "";
  const due = typeof node.properties.due === "string" ? node.properties.due : "";

  function setDate(key: "start" | "due", value: string) {
    const body = value ? { properties: { [key]: value } } : { remove_properties: [key] };
    updateNode.mutate({ id: node.id, body });
  }

  return (
    <>
      <div className="kv-row">
        <div className="field" style={{ marginBottom: 0, flex: 1 }}>
          <label className="field__label" htmlFor="np-start">
            Start
          </label>
          <input id="np-start" type="date" value={start} onChange={(e) => setDate("start", e.target.value)} />
        </div>
        <div className="field" style={{ marginBottom: 0, flex: 1 }}>
          <label className="field__label" htmlFor="np-due">
            Due
          </label>
          <input id="np-due" type="date" value={due} onChange={(e) => setDate("due", e.target.value)} />
        </div>
      </div>
      {updateNode.isError ? (
        <div className="faint" style={{ color: "var(--danger)", marginBottom: 6 }}>
          {updateNode.error.message}
        </div>
      ) : null}
    </>
  );
}

/** Key/value editor over `properties` (s6-ui.md §10.1). Untouched values round-trip with
 * their original JSON type (number/bool/list/dict) exactly as stored; editing or adding a row
 * always writes a plain string -- simplest predictable behavior for a free-form `dict[str,
 * Any]` field edited through plain text inputs, without a full per-field type picker. */
export function NodePropertiesEditor({ node }: { node: NodeOut }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const updateNode = useUpdateNode();
  // Date inputs are schema-specific (finding 4): only `milestone` nodes need them, but a node
  // that already carries a start/due value (e.g. retyped away from milestone with dates still
  // set) keeps the dedicated inputs rather than losing editability of a key it still has.
  const showDates = node.type === "milestone" || "start" in node.properties || "due" in node.properties;
  const active = rows ?? toRows(node.properties, showDates);
  // BUG: two editing surfaces for one key -- DateQuickEdit above owns start/due while
  // showDates is true, so the generic row editor must reject either as a hand-typed key here.
  const dateKeyConflict = showDates && active.some((r) => DATE_KEYS.has(r.key.trim()));

  function begin() {
    setRows(toRows(node.properties, showDates));
  }

  function setKey(i: number, key: string) {
    setRows(active.map((r, idx) => (idx === i ? { ...r, key } : r)));
  }
  function setValue(i: number, value: string) {
    setRows(active.map((r, idx) => (idx === i ? { ...r, value, edited: true } : r)));
  }
  function removeRow(i: number) {
    setRows(active.filter((_, idx) => idx !== i));
  }
  function addRow() {
    setRows([...active, { key: "", value: "", edited: true }]);
  }
  function save() {
    const kept = active.filter((r) => r.key.trim());
    const properties = Object.fromEntries(kept.map((r) => [r.key, r.value]));
    // Original keys (excluding start/due while DateQuickEdit owns them) no longer present in
    // `kept` -- via the "x" remove button or by renaming the row's key -- must be deleted
    // server-side, not just omitted (omitting a key from `properties` only skips it; it does
    // not remove it -- update_node's merge is additive, see store.py).
    const keptKeys = new Set(kept.map((r) => r.key));
    const removed = Object.keys(node.properties)
      .filter((k) => !showDates || !DATE_KEYS.has(k))
      .filter((k) => !keptKeys.has(k));
    const body = removed.length ? { properties, remove_properties: removed } : { properties };
    updateNode.mutate({ id: node.id, body }, { onSuccess: () => setRows(null) });
  }

  const editing = rows !== null;

  return (
    <div className="card">
      <div className="card__title" style={{ display: "flex", justifyContent: "space-between" }}>
        <span>Properties</span>
        {!editing && (
          <button className="btn btn--sm" onClick={begin}>
            Edit
          </button>
        )}
      </div>
      {showDates ? <DateQuickEdit node={node} /> : null}
      {active.length === 0 && !editing ? <div className="faint">(none)</div> : null}
      {active.map((row, i) => (
        <div className="kv-row" key={i}>
          <input
            type="text"
            value={row.key}
            readOnly={!editing}
            placeholder="key"
            onChange={(e) => setKey(i, e.target.value)}
            style={{ maxWidth: 160 }}
          />
          <input
            type="text"
            value={displayValue(row)}
            readOnly={!editing}
            placeholder="value"
            onChange={(e) => setValue(i, e.target.value)}
          />
          {editing && (
            <button className="btn btn--sm" onClick={() => removeRow(i)} aria-label="Remove">
              ×
            </button>
          )}
        </div>
      ))}
      {editing ? (
        <>
          <button className="btn btn--sm" onClick={addRow}>
            + Add property
          </button>
          {dateKeyConflict ? (
            <div className="faint" style={{ color: "var(--danger)" }}>
              start and due are set with the date fields above
            </div>
          ) : null}
          {updateNode.isError ? (
            <div className="faint" style={{ color: "var(--danger)" }}>
              {updateNode.error.message}
            </div>
          ) : null}
          <div className="dialog__actions">
            <button className="btn" onClick={() => setRows(null)} disabled={updateNode.isPending}>
              Cancel
            </button>
            <button className="btn btn--primary" onClick={save} disabled={updateNode.isPending || dateKeyConflict}>
              {updateNode.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
