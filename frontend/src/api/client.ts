// Thin fetch wrapper -- base URL, JSON, multipart -- ONE place every endpoint call goes
// through, not N (s6-ui.md §2's own module layout comment for this file). Same-origin empty
// base ("") in production (the SPA is served by the same FastAPI process, s6-ui.md §7.1) and
// under the Vite dev proxy (vite.config.ts forwards /api/* to 127.0.0.1:8420) -- so this file
// never needs an environment-specific base URL.
import type {
  AttentionOut,
  CreateNodeRequest,
  DedupHintsOut,
  DeleteResult,
  Direction,
  EdgeBatchRequest,
  EdgeOut,
  EdgeRequest,
  EdgeType,
  GitStatusOut,
  MergeResult,
  NeighborEdge,
  NodeOut,
  NodeType,
  NodeWithNeighbors,
  OutlineOut,
  ReindexStats,
  RenameResult,
  ReportBundle,
  ReportContent,
  ReportFile,
  SchemaOut,
  SearchHit,
  SnapshotResult,
  StatusOut,
  TimelineOut,
  UpdateNodeRequest,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

/** Repeated-key query string, matching FastAPI's Query(list[X]) binding (?k=a&k=b), not CSV. */
function qs(params: Record<string, string | number | boolean | string[] | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const v of value) search.append(key, v);
    } else {
      search.append(key, String(value));
    }
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body !== undefined && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json", ...init?.headers }
      : init?.headers,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body (rare) -- fall back to statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) });
const put = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) });
const del = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "DELETE", body: body === undefined ? undefined : JSON.stringify(body) });

// One function per endpoint (s6-ui.md §9's table, 23 rows) -- paths/methods/params match
// exactly; response types are the mirrors in ./types.ts.
export const api = {
  schema: () => get<SchemaOut>("/api/schema"),
  outline: () => get<OutlineOut>("/api/outline"),
  attention: () => get<AttentionOut>("/api/attention"),
  timeline: () => get<TimelineOut>("/api/timeline"),

  node: (id: string, neighborDepth = 0) =>
    get<NodeWithNeighbors>(`/api/nodes/${encodeURIComponent(id)}${qs({ neighbor_depth: neighborDepth })}`),
  nodeNeighbors: (id: string, edgeTypes?: EdgeType[] | null, direction: Direction = "both") =>
    get<NeighborEdge[]>(
      `/api/nodes/${encodeURIComponent(id)}/neighbors${qs({ edge_types: edgeTypes ?? undefined, direction })}`,
    ),
  createNode: (body: CreateNodeRequest) => post<NodeOut>("/api/nodes", body),
  updateNode: (id: string, body: UpdateNodeRequest) => patch<NodeOut>(`/api/nodes/${encodeURIComponent(id)}`, body),
  renameNode: (id: string, newTitle: string) =>
    post<RenameResult>(`/api/nodes/${encodeURIComponent(id)}/rename`, { new_title: newTitle }),
  deleteNode: (id: string) => del<DeleteResult>(`/api/nodes/${encodeURIComponent(id)}`),
  mergeNodes: (id: string, dropId: string) =>
    post<MergeResult>(`/api/nodes/${encodeURIComponent(id)}/merge`, { drop_id: dropId }),

  createEdge: (body: EdgeRequest) => post<EdgeOut>("/api/edges", body),
  createEdges: (body: EdgeBatchRequest) => post<EdgeOut[]>("/api/edges/batch", body),
  deleteEdge: (body: EdgeRequest) => del<DeleteResult>("/api/edges", body),
  reparentNode: (id: string, newParent: string | null) =>
    put<NodeOut>(`/api/nodes/${encodeURIComponent(id)}/parent`, { new_parent: newParent }),

  search: (q: string, opts?: { types?: NodeType[]; status?: string; k?: number }) =>
    get<SearchHit[]>(`/api/search${qs({ q, types: opts?.types, status: opts?.status, k: opts?.k })}`),
  dedupHints: (title: string, k = 5) => get<DedupHintsOut>(`/api/dedup-hints${qs({ title, k })}`),

  doc: (rootId: string, opts?: { depth?: number; includeEdgeTypes?: EdgeType[] }) =>
    get<ReportBundle>(
      `/api/doc/${encodeURIComponent(rootId)}${qs({ depth: opts?.depth, include_edge_types: opts?.includeEdgeTypes })}`,
    ),
  reports: () => get<ReportFile[]>("/api/reports"),
  report: (filename: string) => get<ReportContent>(`/api/reports/${encodeURIComponent(filename)}`),

  uploadAttachment: (file: File, title: string, partOf?: string | null) => {
    const form = new FormData();
    form.set("file", file);
    form.set("title", title);
    if (partOf) form.set("part_of", partOf);
    return request<NodeOut>("/api/attachments", { method: "POST", body: form });
  },

  snapshot: (message: string, push = false) => post<SnapshotResult>("/api/snapshot", { message, push }),
  reindex: () => post<ReindexStats>("/api/reindex"),
  status: () => get<StatusOut>("/api/status"),
  gitStatus: () => get<GitStatusOut>("/api/git-status"),
};
