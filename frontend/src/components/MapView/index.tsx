import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import ForceGraph2D, { type ForceGraphMethods, type NodeObject } from "react-force-graph-2d";
import { useOutline } from "../../api/queries";
import { buildNodeLink } from "../../lib/nav";
import { nodeTypeColor } from "../../lib/palette";
import type { NodeType } from "../../api/types";
import { EmptyState } from "../EmptyState";

/** CENTER, Map tab: /map -- react-force-graph-2d fed /api/outline's existing {nodes, edges}
 * shape directly (s6-ui.md §11/§9). Route-lazy-loaded (routes.tsx wraps this in React.lazy),
 * so the force-graph/d3 bundle is never fetched until this tab is actually opened -- "Map"
 * stays the demoted tab in bundle cost too, not just in the product's own stated priority.
 * React-19 compatibility verified live before this file was written (ruling §14.3): a 3-node
 * ForceGraph2D mounts cleanly under react@19.2.7 with zero thrown errors (jsdom + real
 * <canvas> 2D context, react-dom/client createRoot) -- no vanilla force-graph fallback
 * needed. */
export default function MapView() {
  const { data, isPending, isError } = useOutline();
  const navigate = useNavigate();
  // A CALLBACK ref (not useRef+useEffect([])) because this div is gated behind isPending/
  // isError/empty-state early returns below -- on a cold load the FIRST commit is one of those
  // placeholders, so a mount-only effect would see containerRef.current === null and never
  // re-run once the real .map-view div appears on a later render. A callback ref re-fires
  // exactly when the node attaches, whichever render that happens on (the bug this replaces:
  // the ResizeObserver never actually attached, so `size` stayed at its 600x400 fallback
  // forever -- the graph rendering into a fixed small canvas pinned in the container's corner).
  const [container, setContainer] = useState<HTMLDivElement | null>(null);
  const fgRef = useRef<ForceGraphMethods<NodeObject<MapNode>> | undefined>(undefined);
  const fittedRef = useRef(false); // re-arm on a fresh data load; NOT on every engine-stop (a
  // node drag reheats the sim and re-fires onEngineStop too -- fitting then would yank the
  // view out from under whatever the user was just doing)
  const [size, setSize] = useState({ width: 600, height: 400 });

  useEffect(() => {
    fittedRef.current = false;
  }, [data]);

  useEffect(() => {
    if (!container) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width: Math.max(width, 200), height: Math.max(height, 200) });
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [container]);

  const graphData = useMemo(() => {
    if (!data) return { nodes: [] as MapNode[], links: [] };
    return {
      nodes: data.nodes.map((n) => ({ id: n.id, name: n.title, type: n.type }) satisfies MapNode),
      links: data.edges
        .filter((e) => e.type === "part_of") // structural spine only -- keeps the map readable
        .map((e) => ({ source: e.src, target: e.dst })),
    };
  }, [data]);

  // Legend types: only what's actually on the map (not every NodeType.NODE_TYPE_COLOR key),
  // so a graph that never used e.g. "milestone" doesn't show a swatch for it. Sorted for a
  // stable render order across re-fetches.
  const presentTypes = useMemo(
    () => [...new Set(graphData.nodes.map((n) => n.type))].sort() as NodeType[],
    [graphData.nodes],
  );

  if (isPending) return <div className="spinner-line">Loading map…</div>;
  if (isError) return <EmptyState>Failed to load the graph.</EmptyState>;
  if (graphData.nodes.length === 0) return <EmptyState>No nodes yet.</EmptyState>;

  return (
    <div className="map-view" ref={setContainer}>
      <ForceGraph2D<MapNode>
        ref={fgRef}
        graphData={graphData}
        width={size.width}
        height={size.height}
        nodeLabel="name"
        nodeColor={(n: NodeObject<MapNode>) => nodeTypeColor(n.type)}
        linkColor={() => "rgba(150, 158, 170, 0.55)"}
        onNodeClick={(n: NodeObject<MapNode>) => navigate(buildNodeLink("outline", String(n.id)))}
        cooldownTicks={200} // force-graph's alpha-based early-stop is off by default (d3AlphaMin=0),
        // so onEngineStop otherwise only fires after its 15s wall-clock cooldownTime ceiling --
        // an explicit tick cap settles (and fits) the view in ~3s at 60fps instead
        onEngineStop={() => {
          if (fittedRef.current) return;
          fittedRef.current = true;
          fgRef.current?.zoomToFit(0, 40); // instant (0ms) -- the app has no animations; this is a one-time layout fit, not a flourish
        }}
      />
      <div className="map-legend">
        {presentTypes.map((type) => (
          <div className="map-legend__item" key={type}>
            <span className="map-legend__swatch" style={{ background: nodeTypeColor(type) }} />
            {type}
          </div>
        ))}
        <div className="map-legend__caption">edges: part_of (hierarchy) only</div>
      </div>
    </div>
  );
}

interface MapNode {
  id: string;
  name: string;
  type: string;
}
