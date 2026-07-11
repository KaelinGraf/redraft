import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useOutline, useTimeline } from "../../api/queries";
import { buildNodeLink } from "../../lib/nav";
import { statusColor } from "../../lib/palette";
import { EmptyState } from "../EmptyState";
import { TypeChip } from "../TypeChip";
import { AXIS_HEIGHT, BAR_HEIGHT, DIAMOND_R, LANE_GAP, LANE_HEADER_HEIGHT, ROW_HEIGHT, buildLayout, type PositionedItem } from "./layout";
import type { TimelineItem } from "../../api/types";

/** CENTER, Timeline tab: /timeline -- Gantt-style milestone planning over the Planning dates
 * convention (organizing-protocol.md §2 addendum), reading GET /api/timeline.
 *
 * Hand-rolled SVG/CSS, not a third-party Gantt library -- library-decision note (mirrors
 * s6-ui.md §11's react-force-graph-2d evaluation for Map): every actual React Gantt component
 * on npm either pins a React <=18 peer range (gantt-task-react, react-gantt-chart,
 * wx-react-gantt -- all verified live against the npm registry) or, for the one candidate that
 * claims React 19 (react-calendar-timeline), does so only via a peer range pinned to a
 * specific 19.0.0 RC BUILD TAG while the package itself ships as a long-running 0.x "beta" and
 * pulls in five extra runtime dependencies (lodash, dayjs, interactjs,
 * element-resize-detector, classnames) for a single supplementary tab -- and none of them has
 * a native concept of swimlanes-by-arbitrary-parent, a due-only-vs-ranged marker distinction,
 * or cross-item dependency arrows, so adopting one would still mean hand-building most of this
 * IA on top of a foreign, heavier rendering engine. No candidate cleared the "well-maintained
 * AND React-19-compatible AND fits the IA" bar the build brief set, so no live smoke-mount was
 * run (unlike Map's accepted react-force-graph-2d, which passed that bar) -- going straight to
 * the pre-authorized fallback: a themeable SVG/CSS timeline, which for this IA (swimlanes,
 * diamonds, bars, dependency arrows) is not much code (see ./layout.ts) and adds zero new
 * dependencies.
 *
 * Route-lazy-loaded like MapView (routes.tsx) -- this tab's code is never fetched until
 * opened. */
export default function TimelineView() {
  const { data, isPending, isError } = useTimeline();
  const { data: outline } = useOutline(); // already loaded for OutlinePane -- reused, not refetched, for part_of -> title
  const navigate = useNavigate();

  // .timeline-scroll's own width (not the window's) -- px/day is derived from this so a short
  // domain fills the actual plot area instead of the fixed-tier scale compressing it into a
  // strip of dead space (s6-ui.md addendum: the tab is full-width now, so this container IS
  // most of the available room). A CALLBACK ref, not useRef+useEffect([]) -- .timeline-scroll
  // is gated behind the isPending early return below, so on a cold load the first commit is
  // the loading placeholder and a mount-only effect would see scrollRef.current === null and
  // never re-run once the real element appears on a later render (the bug this replaces: the
  // observer never attached, so plotAreaWidth stayed at its fallback forever).
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const [plotAreaWidth, setPlotAreaWidth] = useState(1000);
  useEffect(() => {
    if (!scrollEl) return;
    const observer = new ResizeObserver(([entry]) => setPlotAreaWidth(Math.max(320, entry.contentRect.width)));
    observer.observe(scrollEl);
    return () => observer.disconnect();
  }, [scrollEl]);

  const titleById = useMemo(() => new Map((outline?.nodes ?? []).map((n) => [n.id, n.title])), [outline]);
  const titleFor = (id: string) => titleById.get(id) ?? id;

  const items = data?.items ?? [];
  const scheduled = useMemo(() => items.filter((i) => i.start || i.due), [items]);
  const unscheduled = useMemo(() => items.filter((i) => !i.start && !i.due), [items]);
  const layout = useMemo(() => buildLayout(scheduled, titleFor, plotAreaWidth), [scheduled, titleById, plotAreaWidth]);

  // Shared between the gutter's row labels and the plot's row-highlight bands, so hovering
  // either side highlights both (optional nicety from the build brief -- cheap given both
  // already render one element per item in the same `layout.lanes[].items` order).
  const [hoverId, setHoverId] = useState<string | null>(null);

  if (isPending) return <div className="spinner-line">Loading timeline…</div>;
  if (isError) return <EmptyState>Failed to load the timeline.</EmptyState>;

  const goToNode = (id: string) => navigate(buildNodeLink("outline", id));
  const allItems = layout.lanes.flatMap((lane) => lane.items);

  return (
    <div className="timeline-view">
      {layout.lanes.length === 0 ? (
        <EmptyState>No scheduled milestones yet — add start/due dates to a milestone.</EmptyState>
      ) : (
        <div className="timeline-chart">
          <div className="timeline-lanes-col">
            <div style={{ height: AXIS_HEIGHT }} />
            {layout.lanes.map((lane) => (
              <div key={lane.key || "ungrouped"} className="timeline-lane-group">
                <div className="timeline-lane-header" style={{ height: LANE_HEADER_HEIGHT }} title={lane.title}>
                  <span className="timeline-lane-header__text">{lane.title}</span>
                </div>
                {lane.items.map((p) => (
                  <Link
                    key={p.item.id}
                    to={buildNodeLink("outline", p.item.id)}
                    className={`timeline-row-label${p.item.id === hoverId ? " timeline-row-label--hover" : ""}`}
                    style={{ height: ROW_HEIGHT }}
                    title={p.item.title}
                    onMouseEnter={() => setHoverId(p.item.id)}
                    onMouseLeave={() => setHoverId((h) => (h === p.item.id ? null : h))}
                  >
                    <TypeChip type={p.item.type} compact />
                    <span className="timeline-row-label__text">{p.item.title}</span>
                  </Link>
                ))}
                {/* Mirrors layout.ts's `y += height + LANE_GAP` between lanes -- without this
                    spacer the gutter's flex stack would drift out of alignment with the plot
                    from the second lane onward (only visible with >1 lane, e.g. the p4p graph). */}
                <div style={{ height: LANE_GAP }} />
              </div>
            ))}
          </div>
          <div className="timeline-scroll" ref={setScrollEl}>
            <svg width={layout.chartWidth} height={layout.chartHeight}>
              <defs>
                <marker id="tl-arrowhead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" className="timeline-arrowhead" />
                </marker>
              </defs>
              {layout.ticks.map((t, i) => (
                <g key={i}>
                  <line x1={t.x} x2={t.x} y1={AXIS_HEIGHT} y2={layout.chartHeight} className="timeline-gridline" />
                  <text x={t.x + 3} y={AXIS_HEIGHT - 10} className="timeline-tick-label">
                    {t.label}
                  </text>
                </g>
              ))}
              {allItems.map((p) => (
                <rect
                  key={p.item.id}
                  x={0}
                  y={p.y - ROW_HEIGHT / 2}
                  width={layout.chartWidth}
                  height={ROW_HEIGHT}
                  className={`timeline-row-band${p.item.id === hoverId ? " timeline-row-band--hover" : ""}`}
                  onMouseEnter={() => setHoverId(p.item.id)}
                  onMouseLeave={() => setHoverId((h) => (h === p.item.id ? null : h))}
                />
              ))}
              {layout.arrows.map((a) => (
                <path key={a.key} d={a.d} className="timeline-arrow" markerEnd="url(#tl-arrowhead)" />
              ))}
              {allItems.map((p) => (
                <TimelineMark key={p.item.id} p={p} onClick={() => goToNode(p.item.id)} />
              ))}
            </svg>
          </div>
        </div>
      )}
      <UnscheduledTray unscheduled={unscheduled} invalidDate={layout.invalidDate} />
    </div>
  );
}

function TimelineMark({ p, onClick }: { p: PositionedItem; onClick: () => void }) {
  const color = statusColor(p.item.status);
  const tooltip = `${p.item.title}\nstatus: ${p.item.status ?? "—"}\nstart: ${p.item.start ?? "—"}\ndue: ${p.item.due ?? "—"}`;

  return (
    <g className="timeline-mark" onClick={onClick}>
      <title>{tooltip}</title>
      {p.kind === "bar" ? (
        <rect x={p.x1} y={p.y - BAR_HEIGHT / 2} width={Math.max(2, p.x2 - p.x1)} height={BAR_HEIGHT} rx={3} fill={color} />
      ) : (
        <polygon points={diamondPoints(p.x1, p.y)} fill={color} />
      )}
    </g>
  );
}

function diamondPoints(cx: number, cy: number): string {
  return `${cx},${cy - DIAMOND_R} ${cx + DIAMOND_R},${cy} ${cx},${cy + DIAMOND_R} ${cx - DIAMOND_R},${cy}`;
}

/** Side/bottom tray for milestones GET /api/timeline returned with neither date set, PLUS
 * (s6-ui.md addendum, an adjacent failure mode the build brief's prose didn't separately name)
 * scheduled items whose only date string(s) failed to parse and so can't be placed on the
 * axis at all -- both are surfaced here rather than dropped, distinguished by a badge, each
 * still one click from the properties editor that can fix it. */
function UnscheduledTray({ unscheduled, invalidDate }: { unscheduled: TimelineItem[]; invalidDate: TimelineItem[] }) {
  const rows = [
    ...unscheduled.map((item) => ({ item, invalid: false })),
    ...invalidDate.map((item) => ({ item, invalid: true })),
  ];
  return (
    <div className="card">
      <div className="card__title">Unscheduled milestones ({rows.length})</div>
      {rows.length === 0 ? (
        <div className="faint">(none)</div>
      ) : (
        rows.map(({ item, invalid }) => (
          <Link key={item.id} to={buildNodeLink("outline", item.id)} className="attention-item">
            <TypeChip type={item.type} compact /> {item.title}
            {invalid ? <span className="degraded-badge timeline-tray__badge">invalid date</span> : null}
          </Link>
        ))
      )}
    </div>
  );
}
