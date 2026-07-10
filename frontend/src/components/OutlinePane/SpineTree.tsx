import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { TypeChip } from "../TypeChip";
import { StatusDot } from "../StatusIndicator";
import { buildNodeLink, type TabKey } from "../../lib/nav";
import type { OutlineEdge, OutlineNode } from "../../api/types";

interface Props {
  nodes: OutlineNode[];
  edges: OutlineEdge[];
  filter: string;
  selectedId: string | undefined;
  activeTab: TabKey;
}

// Sentinel toggle key for the off-spine group -- shares `collapsed`'s Set<string> rather than
// growing a second piece of state, since a real node id can never collide with it.
const OFF_SPINE_KEY = "__off-spine__";

export function SpineTree({ nodes, edges, filter, selectedId, activeTab }: Props) {
  // Off-spine starts COLLAPSED (finding 2 of the operator-UI review); every real subtree
  // starts expanded, same as before.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set([OFF_SPINE_KEY]));

  const { onSpineRoots, offSpineRoots, childrenOf } = useMemo(() => {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    // part_of edge: src = child, dst = parent (mutations.reparent_node's own create_edge call
    // order). A parent id that isn't in `byId` is a DANGLING part_of edge (s6-ui.md §4.1 /
    // AttentionOut.dangling_edges -- e.g. the parent was deleted and this child's edge was
    // never cleaned up) -- such a child is treated as a root rather than silently vanishing
    // from the tree, since it would otherwise never render anywhere.
    const parentOf = new Map<string, string>();
    for (const e of edges) {
      if (e.type === "part_of" && byId.has(e.dst)) parentOf.set(e.src, e.dst);
    }
    const childrenOf = new Map<string, OutlineNode[]>();
    const roots: OutlineNode[] = [];
    for (const n of nodes) {
      const parent = parentOf.get(n.id);
      if (parent === undefined) {
        roots.push(n);
      } else {
        const list = childrenOf.get(parent) ?? [];
        list.push(n);
        childrenOf.set(parent, list);
      }
    }
    const byTitle = (a: OutlineNode, b: OutlineNode) => a.title.localeCompare(b.title);
    for (const list of childrenOf.values()) list.sort(byTitle);

    // Mirrors report.py's overview() contract exactly (see that function's own docstring): a
    // SPINE root is a parentless node with >=1 inbound part_of child. A parentless node with
    // NO part_of children is off-spine (rationale/observation/artifact nodes are parentless BY
    // DESIGN -- organizing-protocol.md -- so without this split they sorted alphabetically
    // above the real project root on every graph with real rationale density).
    let onSpine = roots.filter((r) => (childrenOf.get(r.id) ?? []).length > 0);
    let offSpine = roots.filter((r) => (childrenOf.get(r.id) ?? []).length === 0);

    // Nascent-graph fallback (overview()'s own docstring covers this exact case): if NO
    // parentless node has ever grown a part_of child yet, there is no real spine at all --
    // list every parentless node as a root rather than burying the whole graph in "Off-spine".
    if (onSpine.length === 0) {
      onSpine = roots;
      offSpine = [];
    }

    // subtree size = branch count + all descendants, self-exclusive (overview()'s own
    // descendant_count contract) -- computed once per root, not per comparison.
    function subtreeSize(id: string, seen: Set<string>): number {
      if (seen.has(id)) return 0; // cycle guard, mirrors TreeNode's own `ancestors` guard below
      seen.add(id);
      const kids = childrenOf.get(id) ?? [];
      return kids.reduce((sum, k) => sum + 1 + subtreeSize(k.id, seen), 0);
    }
    const sizeById = new Map(onSpine.map((r) => [r.id, subtreeSize(r.id, new Set())]));

    // Order: subtree size DESC, then title ASC. overview()'s own tiebreak is created ASC, but
    // OutlineNode (api/types.ts) carries no `created` field over /api/outline's wire shape --
    // title ASC is the closest client-side equivalent (still deterministic, still cheap).
    onSpine = onSpine.slice().sort((a, b) => (sizeById.get(b.id)! - sizeById.get(a.id)!) || byTitle(a, b));
    offSpine = offSpine.slice().sort(byTitle);

    return { onSpineRoots: onSpine, offSpineRoots: offSpine, childrenOf };
  }, [nodes, edges]);

  const trimmedFilter = filter.trim().toLowerCase();
  if (trimmedFilter) {
    // Flat match list over the FULL node set (including off-spine ones) -- already "reveals"
    // every match without needing to expand anything, since filtered mode replaces the
    // grouped/collapsed tree outright rather than filtering within it.
    const matches = nodes
      .filter((n) => n.title.toLowerCase().includes(trimmedFilter) || n.type.includes(trimmedFilter))
      .sort((a, b) => a.title.localeCompare(b.title));
    return (
      <div className="spine-tree">
        <ul>
          {matches.map((n) => (
            <Row key={n.id} node={n} depth={0} hasChildren={false} activeTab={activeTab} selectedId={selectedId} />
          ))}
        </ul>
      </div>
    );
  }

  function toggle(key: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div className="spine-tree">
      <ul>
        {onSpineRoots.map((n) => (
          <TreeNode
            key={n.id}
            node={n}
            depth={0}
            childrenOf={childrenOf}
            collapsed={collapsed}
            setCollapsed={setCollapsed}
            activeTab={activeTab}
            selectedId={selectedId}
            ancestors={new Set()}
          />
        ))}
        {offSpineRoots.length > 0 ? (
          <OffSpineGroup
            nodes={offSpineRoots}
            collapsed={collapsed.has(OFF_SPINE_KEY)}
            onToggle={() => toggle(OFF_SPINE_KEY)}
            activeTab={activeTab}
            selectedId={selectedId}
          />
        ) : null}
      </ul>
    </div>
  );
}

/** Bottom-of-tree, collapsed-by-default group for parentless nodes with no part_of children
 * (rationale/observation/artifact, by protocol design -- see the size-sort useMemo above).
 * Flat, not recursive: an off-spine root by definition has zero children. */
function OffSpineGroup({
  nodes,
  collapsed,
  onToggle,
  activeTab,
  selectedId,
}: {
  nodes: OutlineNode[];
  collapsed: boolean;
  onToggle: () => void;
  activeTab: TabKey;
  selectedId: string | undefined;
}) {
  return (
    <li>
      {/* A real <button>, not a Link like Row's -- there's no node to navigate to, just a
       * group to expand/collapse, so the whole row (not only a small nested toggle glyph) is
       * the click/keyboard target, natively focusable and covered by the app's existing
       * :focus-visible rule. */}
      <button
        type="button"
        className="spine-row spine-row--group"
        style={{ paddingLeft: 6 }}
        onClick={onToggle}
        aria-expanded={!collapsed}
      >
        <span className="spine-row__toggle" aria-hidden="true">
          {collapsed ? "▸" : "▾"}
        </span>
        <span className="spine-row__title faint">Off-spine ({nodes.length})</span>
      </button>
      {collapsed ? null : (
        <ul>
          {nodes.map((n) => (
            <Row key={n.id} node={n} depth={1} hasChildren={false} activeTab={activeTab} selectedId={selectedId} />
          ))}
        </ul>
      )}
    </li>
  );
}

function TreeNode({
  node,
  depth,
  childrenOf,
  collapsed,
  setCollapsed,
  activeTab,
  selectedId,
  ancestors,
}: {
  node: OutlineNode;
  depth: number;
  childrenOf: Map<string, OutlineNode[]>;
  collapsed: Set<string>;
  setCollapsed: (updater: (prev: Set<string>) => Set<string>) => void;
  activeTab: TabKey;
  selectedId: string | undefined;
  ancestors: Set<string>;
}) {
  const children = childrenOf.get(node.id) ?? [];
  const isCollapsed = collapsed.has(node.id);
  // Cycle guard: hand-edited/externally-merged frontmatter (s6-ui.md §4.1) could in principle
  // introduce a part_of cycle the write path itself would have rejected -- never redescend
  // into an id already on this render path, so a corrupted graph degrades to "one bad
  // subtree" rather than an infinite render loop / crashed tab.
  const cyclic = ancestors.has(node.id);

  return (
    <li>
      <Row
        node={node}
        depth={depth}
        hasChildren={children.length > 0 && !cyclic}
        collapsed={isCollapsed}
        onToggle={() =>
          setCollapsed((prev) => {
            const next = new Set(prev);
            if (next.has(node.id)) next.delete(node.id);
            else next.add(node.id);
            return next;
          })
        }
        activeTab={activeTab}
        selectedId={selectedId}
      />
      {children.length > 0 && !isCollapsed && !cyclic ? (
        <ul>
          {children.map((c) => (
            <TreeNode
              key={c.id}
              node={c}
              depth={depth + 1}
              childrenOf={childrenOf}
              collapsed={collapsed}
              setCollapsed={setCollapsed}
              activeTab={activeTab}
              selectedId={selectedId}
              ancestors={new Set(ancestors).add(node.id)}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function Row({
  node,
  depth,
  hasChildren,
  collapsed,
  onToggle,
  activeTab,
  selectedId,
}: {
  node: OutlineNode;
  depth: number;
  hasChildren: boolean;
  collapsed?: boolean;
  onToggle?: () => void;
  activeTab: TabKey;
  selectedId: string | undefined;
}) {
  return (
    <Link
      to={buildNodeLink(activeTab, node.id)}
      className={`spine-row${selectedId === node.id ? " active" : ""}`}
      style={{ paddingLeft: 6 + depth * 14 }}
      title={node.title}
    >
      {hasChildren ? (
        <button
          className="spine-row__toggle"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onToggle?.();
          }}
          aria-label={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? "▸" : "▾"}
        </button>
      ) : (
        <span className="spine-row__toggle" />
      )}
      <TypeChip type={node.type} compact />
      <StatusDot status={node.status} />
      <span className="spine-row__title">{node.title}</span>
    </Link>
  );
}
