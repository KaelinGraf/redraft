// TanStack Query hooks, one per endpoint family, keyed under the shared query-key hierarchy
// (s6-ui.md §8/§10.2). Every mutation goes through useGraphMutation, which invalidates the
// ENTIRE cache on success -- broad by design (§8): rename/merge/reparent touch more than the
// one node id they were called on (referrer frontmatter, outline shape), and at this
// project's own stated scale a full invalidateQueries() is simple, always correct, and cheap
// enough that hand-computing a minimal per-mutation invalidation set isn't worth the bug
// surface it would add.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "./client";
import type { CreateNodeRequest, EdgeBatchRequest, EdgeRequest, EdgeType, NodeType, UpdateNodeRequest } from "./types";

export const qk = {
  schema: ["schema"] as const,
  outline: ["outline"] as const,
  attention: ["attention"] as const,
  timeline: ["timeline"] as const,
  node: (id: string, neighborDepth: number) => ["node", id, neighborDepth] as const,
  search: (q: string, types?: NodeType[], status?: string, k?: number) =>
    ["search", q, types, status, k] as const,
  dedupHints: (title: string, k: number) => ["dedup-hints", title, k] as const,
  doc: (rootId: string, depth?: number, includeEdgeTypes?: EdgeType[]) =>
    ["doc", rootId, depth, includeEdgeTypes] as const,
  reports: ["reports"] as const,
  report: (filename: string) => ["reports", filename] as const,
  status: ["status"] as const,
  gitStatus: ["git-status"] as const,
};

export function useSchema() {
  return useQuery({ queryKey: qk.schema, queryFn: api.schema, staleTime: Infinity });
}

export function useOutline() {
  return useQuery({ queryKey: qk.outline, queryFn: api.outline });
}

export function useAttention() {
  return useQuery({ queryKey: qk.attention, queryFn: api.attention });
}

/** CENTER, Timeline tab: GET /api/timeline (Planning dates convention). Invalidated like every
 * other query by useGraphMutation's broad invalidateQueries() (§8) -- a date edit through
 * NodePropertiesEditor's PATCH .../nodes/{id} is just another node mutation, no separate
 * invalidation wiring needed here. */
export function useTimeline() {
  return useQuery({ queryKey: qk.timeline, queryFn: api.timeline });
}

export function useNode(id: string | undefined, neighborDepth = 1) {
  return useQuery({
    queryKey: qk.node(id ?? "", neighborDepth),
    queryFn: () => api.node(id as string, neighborDepth),
    enabled: id !== undefined,
  });
}

export function useSearch(q: string, opts?: { types?: NodeType[]; status?: string; k?: number }) {
  return useQuery({
    queryKey: qk.search(q, opts?.types, opts?.status, opts?.k),
    queryFn: () => api.search(q, opts),
    enabled: q.trim().length > 0,
  });
}

/** s6-ui.md §5.3: caller debounces `title` 300ms before this fires (useDebouncedValue). */
export function useDedupHints(title: string, k = 5) {
  return useQuery({
    queryKey: qk.dedupHints(title, k),
    queryFn: () => api.dedupHints(title, k),
    enabled: title.trim().length > 0,
  });
}

/** Backs BOTH DocView (.sections) and TablesView (.decision_tables) from one cached call
 * (s6-ui.md §10.1) -- both views call this same hook with the same rootId. */
export function useDoc(rootId: string | undefined, depth = 4, includeEdgeTypes?: EdgeType[]) {
  return useQuery({
    queryKey: qk.doc(rootId ?? "", depth, includeEdgeTypes),
    queryFn: () => api.doc(rootId as string, { depth, includeEdgeTypes }),
    enabled: rootId !== undefined,
  });
}

export function useReports() {
  return useQuery({ queryKey: qk.reports, queryFn: api.reports });
}

export function useReport(filename: string | undefined) {
  return useQuery({
    queryKey: qk.report(filename ?? ""),
    queryFn: () => api.report(filename as string),
    enabled: filename !== undefined,
  });
}

/** s6-ui.md §4.2 point 4: refetchInterval 5000ms; on a `generation` change (a mutation landed
 * -- ours, the MCP server's, or a hand-edit picked up by the backend's own reindex poll) this
 * fires ONE broad invalidateQueries(), the same "simplest correct response to something
 * changed somewhere" posture as §8's mutation invalidation. Compared here, not by each
 * caller, so it can never be duplicated or forgotten at a call site. */
export function useStatus() {
  const queryClient = useQueryClient();
  const seenGeneration = useRef<number | null>(null);
  const query = useQuery({ queryKey: qk.status, queryFn: api.status, refetchInterval: 5000 });
  useEffect(() => {
    const gen = query.data?.generation;
    if (gen === undefined) return;
    if (seenGeneration.current !== null && seenGeneration.current !== gen) {
      queryClient.invalidateQueries();
    }
    seenGeneration.current = gen;
  }, [query.data?.generation, queryClient]);
  return query;
}

export function useGitStatus() {
  return useQuery({ queryKey: qk.gitStatus, queryFn: api.gitStatus, refetchInterval: 5000 });
}

/** s6-ui.md §8: the one shared mutation helper every write hook is built from -- broad
 * invalidateQueries() on success, uniformly, so the strategy can never be forgotten at one of
 * the ~10 call sites. */
function useGraphMutation<TArgs, TResult>(mutationFn: (args: TArgs) => Promise<TResult>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries();
    },
  });
}

export function useCreateNode() {
  return useGraphMutation((body: CreateNodeRequest) => api.createNode(body));
}

export function useUpdateNode() {
  return useGraphMutation(({ id, body }: { id: string; body: UpdateNodeRequest }) => api.updateNode(id, body));
}

export function useRenameNode() {
  return useGraphMutation(({ id, newTitle }: { id: string; newTitle: string }) => api.renameNode(id, newTitle));
}

export function useDeleteNode() {
  return useGraphMutation((id: string) => api.deleteNode(id));
}

export function useMergeNodes() {
  return useGraphMutation(({ id, dropId }: { id: string; dropId: string }) => api.mergeNodes(id, dropId));
}

export function useCreateEdge() {
  return useGraphMutation((body: EdgeRequest) => api.createEdge(body));
}

/** POST /api/edges/batch, s6-ui.md §9 -- one StoreWorker call, one generation bump, so it goes
 * through the SAME useGraphMutation helper as every other mutation (§8): the one broad
 * invalidateQueries() on success already covers outline/node/neighbors, no separate/narrower
 * invalidation needed for the batch case. */
export function useCreateEdges() {
  return useGraphMutation((body: EdgeBatchRequest) => api.createEdges(body));
}

export function useDeleteEdge() {
  return useGraphMutation((body: EdgeRequest) => api.deleteEdge(body));
}

export function useReparentNode() {
  return useGraphMutation(({ id, newParent }: { id: string; newParent: string | null }) =>
    api.reparentNode(id, newParent),
  );
}

export function useUploadAttachment() {
  return useGraphMutation(
    ({ file, title, partOf }: { file: File; title: string; partOf?: string | null }) =>
      api.uploadAttachment(file, title, partOf),
  );
}

export function useSnapshot() {
  return useGraphMutation(({ message, push }: { message: string; push?: boolean }) => api.snapshot(message, push));
}

export function useReindex() {
  return useGraphMutation(() => api.reindex());
}
