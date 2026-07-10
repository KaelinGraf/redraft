import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import ForceGraph2D, { type NodeObject } from "react-force-graph-2d";
import { useOutline } from "../../api/queries";
import { buildNodeLink } from "../../lib/nav";
import { nodeTypeColor } from "../../lib/palette";
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
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 600, height: 400 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize({ width: Math.max(width, 200), height: Math.max(height, 200) });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const graphData = useMemo(() => {
    if (!data) return { nodes: [] as MapNode[], links: [] };
    return {
      nodes: data.nodes.map((n) => ({ id: n.id, name: n.title, type: n.type }) satisfies MapNode),
      links: data.edges
        .filter((e) => e.type === "part_of") // structural spine only -- keeps the map readable
        .map((e) => ({ source: e.src, target: e.dst })),
    };
  }, [data]);

  if (isPending) return <div className="spinner-line">Loading map…</div>;
  if (isError) return <EmptyState>Failed to load the graph.</EmptyState>;
  if (graphData.nodes.length === 0) return <EmptyState>No nodes yet.</EmptyState>;

  return (
    <div className="map-view" ref={containerRef}>
      <ForceGraph2D<MapNode>
        graphData={graphData}
        width={size.width}
        height={size.height}
        nodeLabel="name"
        nodeColor={(n: NodeObject<MapNode>) => nodeTypeColor(n.type)}
        linkColor={() => "rgba(150, 158, 170, 0.55)"}
        onNodeClick={(n: NodeObject<MapNode>) => navigate(buildNodeLink("outline", String(n.id)))}
      />
    </div>
  );
}

interface MapNode {
  id: string;
  name: string;
  type: string;
}
