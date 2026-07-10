import { useParams } from "react-router-dom";
import { useNode } from "../../api/queries";
import { EmptyState } from "../EmptyState";
import { NodeHeader } from "./NodeHeader";
import { NodeActions } from "./NodeActions";
import { NodeBodyEditor } from "./NodeBodyEditor";
import { NodePropertiesEditor } from "./NodePropertiesEditor";
import { NodeEdgesPanel } from "./NodeEdgesPanel";

/** CENTER, Outline tab: /outline/:nodeId (s6-ui.md §10.1). */
export function NodeDetail() {
  const { nodeId } = useParams<{ nodeId: string }>();
  const { data, isPending, isError, error } = useNode(nodeId, 1);

  if (!nodeId) return <EmptyState>Select a node from the outline to view its details.</EmptyState>;
  if (isPending) return <div className="spinner-line">Loading node…</div>;
  if (isError) return <EmptyState>{error instanceof Error ? error.message : "Failed to load node."}</EmptyState>;

  const { node, neighbors, edges } = data;

  // key={node.id}: force a full remount on navigation between different nodes, so every
  // child's local state (edit-mode toggles, draft text, a previous mutation's error) resets
  // cleanly rather than leaking from the node you just left onto the one you just opened.
  return (
    <div key={node.id}>
      <NodeHeader node={node} />
      <NodeActions node={node} edges={edges} />
      <NodeBodyEditor node={node} />
      <NodePropertiesEditor node={node} />
      <NodeEdgesPanel node={node} edges={edges} neighbors={neighbors} />
    </div>
  );
}
