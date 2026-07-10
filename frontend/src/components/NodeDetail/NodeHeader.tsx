import { useSchema, useUpdateNode } from "../../api/queries";
import { TypeChip } from "../TypeChip";
import type { NodeOut } from "../../api/types";

export function NodeHeader({ node }: { node: NodeOut }) {
  const { data: schema } = useSchema();
  const updateNode = useUpdateNode();
  const legalStatuses = schema?.status_by_type[node.type] ?? null;

  return (
    <div className="node-header">
      <div>
        <div className="node-header__meta">
          <TypeChip type={node.type} />
          <span className="faint">{node.id}</span>
        </div>
        <h1 className="node-header__title">{node.title}</h1>
        {legalStatuses ? (
          <select
            value={node.status ?? ""}
            disabled={updateNode.isPending}
            onChange={(e) => updateNode.mutate({ id: node.id, body: { status: e.target.value } })}
            aria-label="Status"
          >
            {legalStatuses.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        ) : null}
        {updateNode.isError ? (
          <div className="faint" style={{ color: "var(--danger)" }}>
            {updateNode.error.message}
          </div>
        ) : null}
      </div>
      <div className="faint">
        created {node.created}
        <br />
        updated {node.updated}
      </div>
    </div>
  );
}
