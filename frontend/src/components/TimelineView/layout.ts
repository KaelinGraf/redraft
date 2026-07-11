// Pure layout/scale math for the Gantt-style Timeline view -- index.tsx renders what this
// computes, nothing more. Kept in its own file for the same reason OutlinePane splits
// SpineTree's recursive logic out of index.tsx: this is genuinely non-trivial computation, not
// view code, and is easiest to reason about (and re-check) in isolation.
import type { TimelineItem } from "../../api/types";

const DAY_MS = 24 * 60 * 60 * 1000;

export const ROW_HEIGHT = 26;
export const BAR_HEIGHT = 14;
export const DIAMOND_R = 6;
export const LANE_GAP = 14;
export const AXIS_HEIGHT = 30;
export const LABEL_BUFFER = 190; // trailing px reserved past the last tick for item labels
const MAX_PLOT_WIDTH = 20000; // safety ceiling, well under common browser SVG rasterization limits
const MIN_PX_PER_DAY = 5; // a multi-year domain scrolls horizontally at this density rather than compressing to an unreadable sliver
const MAX_PX_PER_DAY = 220; // a near-zero-day domain still fills its container without stretching a single bar absurdly wide

/** "YYYY-MM-DD" (or anything Date can parse) -> epoch ms, or null if missing/unparseable.
 * organizing-protocol.md's Planning dates convention is a CONVENTION -- nothing server-side
 * validates the string (confirmed live against the backend: a "not-a-real-date" due value
 * round-trips through GET /api/timeline unchanged, HTTP 200) -- so a malformed value is a
 * real possibility this must absorb, not a theoretical one. Never throws; never returns NaN
 * (a NaN here would poison every Math.min/max domain reduction downstream, corrupting the
 * WHOLE chart's scale over one bad node, not just that node's own marker). */
export function parseISODate(s: string | null): number | null {
  if (!s) return null;
  const t = new Date(s).getTime();
  return Number.isNaN(t) ? null : t;
}

export interface PositionedItem {
  item: TimelineItem;
  kind: "bar" | "diamond";
  x1: number;
  x2: number; // === x1 for a diamond
  y: number; // absolute mid-row y within the whole chart, px
  labelWidth: number; // px available for the trailing label before the next item in this sub-row
}

export interface Lane {
  key: string; // item.part_of, or "" for the Ungrouped lane
  title: string;
  y: number; // absolute top y within the chart, px
  height: number;
  items: PositionedItem[];
}

export interface Tick {
  x: number;
  label: string;
}

export interface Arrow {
  key: string;
  d: string; // SVG path "d" -- prerequisite -> dependent
}

export interface TimelineLayout {
  chartWidth: number;
  chartHeight: number;
  lanes: Lane[];
  ticks: Tick[];
  arrows: Arrow[];
  /** Scheduled (start or due was a non-empty string) but every present date string failed to
   * parse -- can't be placed on the axis at all. Routed to the unscheduled tray with a
   * distinct badge rather than dropped or crashing the chart (s6-ui.md's "do not drop them"
   * extended to this adjacent failure mode -- see actor report). */
  invalidDate: TimelineItem[];
}

const EMPTY_LAYOUT: TimelineLayout = { chartWidth: 0, chartHeight: 0, lanes: [], ticks: [], arrows: [], invalidDate: [] };

/** `scheduled` = every item with at least one non-null start/due (caller filters; see
 * index.tsx). `titleFor` resolves a part_of id to its display title -- reuses the already-
 * loaded outline (id -> title), the same "look up a title, fall back to the raw id" idiom
 * NodeEdgesPanel already uses for edge endpoints. `containerWidth` is .timeline-scroll's
 * measured pixel width (index.tsx, ResizeObserver) -- the scale is derived from it so a short
 * domain fills the tab instead of rendering as a compressed strip in a sea of dead space. */
export function buildLayout(scheduled: TimelineItem[], titleFor: (id: string) => string, containerWidth: number): TimelineLayout {
  type Prepped = { item: TimelineItem; kind: "bar" | "diamond"; startMs: number; endMs: number };
  const prepped: Prepped[] = [];
  const invalidDate: TimelineItem[] = [];

  for (const item of scheduled) {
    const s = parseISODate(item.start);
    const d = parseISODate(item.due);
    if (s === null && d === null) {
      invalidDate.push(item); // every present date string failed to parse
    } else if (s !== null && d !== null) {
      // Guard against a backwards pair (due entered before start) rather than rendering a
      // negative-width bar -- cheap, and this convention is unvalidated all the way down.
      prepped.push({ item, kind: "bar", startMs: Math.min(s, d), endMs: Math.max(s, d) });
    } else {
      // Exactly one anchor date present: due-only is the documented "point milestone" case
      // (diamond); start-only is its mirror image (a real, backend-tested shape -- see
      // test_ui_timeline.py's "Kickoff") the design brief's prose didn't separately name --
      // treated the same way, a diamond at whichever single date is known.
      const anchor = (s ?? d) as number;
      prepped.push({ item, kind: "diamond", startMs: anchor, endMs: anchor });
    }
  }

  if (prepped.length === 0) return { ...EMPTY_LAYOUT, invalidDate };

  // Domain + padding ("auto-fit ... with a little padding"). A floor on padDays keeps a
  // single-day or all-same-day domain from rendering as an unreadable sliver.
  const rawMin = prepped.reduce((m, p) => Math.min(m, p.startMs), Infinity);
  const rawMax = prepped.reduce((m, p) => Math.max(m, p.endMs), -Infinity);
  const spanDays = Math.max(1, Math.round((rawMax - rawMin) / DAY_MS));
  const padDays = Math.max(3, Math.round(spanDays * 0.08));
  const domainStart = rawMin - padDays * DAY_MS;
  const domainEnd = rawMax + padDays * DAY_MS;
  const totalDays = Math.max(1, Math.round((domainEnd - domainStart) / DAY_MS));

  // px/day is derived from the ACTUAL container width, not a fixed tier -- a short domain (a
  // 6-day sprint) should fill the tab, not render as a ~500px strip in a 1650px container. The
  // available plot width is containerWidth minus LABEL_BUFFER (the trailing strip reserved past
  // the last tick for item labels, same role it always had -- scaleX maps the domain into
  // plotWidth only, not the full chartWidth). Clamped both ways: MIN_PX_PER_DAY keeps a
  // multi-year domain from compressing to nothing (it scrolls horizontally in .timeline-scroll
  // instead, which owns overflow-x -- the OUTER page never scrolls, per the build brief);
  // MAX_PX_PER_DAY stops a degenerate near-zero-day domain from stretching a bar to hundreds of
  // px on an ultrawide monitor. Capped again at MAX_PLOT_WIDTH -- the same fat-fingered-year
  // typo that motivates MAX_TICKS below would otherwise ask for a millions-of-pixels-wide
  // <svg>, past what browsers reliably rasterize (common engine ceilings sit in the ~32,767px
  // neighborhood).
  const availablePlotWidth = Math.max(320, containerWidth - LABEL_BUFFER);
  const pxPerDay = Math.min(MAX_PX_PER_DAY, Math.max(MIN_PX_PER_DAY, availablePlotWidth / totalDays));
  const plotWidth = Math.min(MAX_PLOT_WIDTH, Math.max(availablePlotWidth, Math.round(totalDays * pxPerDay)));
  const chartWidth = plotWidth + LABEL_BUFFER;
  const scaleX = (ms: number) => ((ms - domainStart) / (domainEnd - domainStart)) * plotWidth;
  const granularity = totalDays > 120 ? "month" : totalDays > 21 ? "week" : "day";
  const ticks = buildTicks(domainStart, domainEnd, scaleX, granularity);

  // Group into swimlanes by part_of (null -> "Ungrouped", sorted last). ONE ROW PER ITEM within
  // a lane -- vertical space in a scrolling tab is cheap, and packing by BAR extent alone (the
  // previous approach) is label-blind: two items disjoint in time but close together still have
  // trailing title text, so packing them into the same sub-row let one item's label run straight
  // over the next item's bar/label (the reported mangled-overlapping-text bug). One row per item
  // sidesteps the problem entirely instead of trying to compute label pixel extents up front.
  const byKey = new Map<string, Prepped[]>();
  for (const p of prepped) {
    const key = p.item.part_of ?? "";
    const lane = byKey.get(key);
    if (lane) lane.push(p);
    else byKey.set(key, [p]);
  }
  const namedKeys = [...byKey.keys()].filter((k) => k !== "").sort((a, b) => titleFor(a).localeCompare(titleFor(b)));
  const orderedKeys = byKey.has("") ? [...namedKeys, ""] : namedKeys;

  const lanes: Lane[] = [];
  const positionedById = new Map<string, PositionedItem>();
  let y = AXIS_HEIGHT;
  const maxLabelWidth = LABEL_BUFFER - 10; // matches the foreignObject width this replaces (was a flat constant)
  for (const key of orderedKeys) {
    const laneItems = byKey.get(key)!.slice().sort((a, b) => a.startMs - b.startMs);
    const positioned: PositionedItem[] = laneItems.map((p, row) => {
      const posItem: PositionedItem = {
        item: p.item,
        kind: p.kind,
        x1: scaleX(p.startMs),
        x2: scaleX(p.endMs),
        y: y + row * ROW_HEIGHT + ROW_HEIGHT / 2,
        labelWidth: maxLabelWidth,
      };
      positionedById.set(p.item.id, posItem);
      return posItem;
    });
    const height = Math.max(1, laneItems.length) * ROW_HEIGHT;
    lanes.push({ key, title: key === "" ? "Ungrouped" : titleFor(key), y, height, items: positioned });
    y += height + LANE_GAP;
  }
  const chartHeight = y;

  // Dependency arrows: prerequisite -> dependent, drawn only when BOTH endpoints landed a
  // pixel position above (i.e. neither is unscheduled or invalid-date) -- skipped gracefully
  // otherwise, never a dangling/off-chart arrow.
  const validDeps: { from: string; to: string }[] = [];
  for (const p of prepped) {
    for (const prereqId of p.item.depends_on) {
      if (positionedById.has(prereqId)) validDeps.push({ from: prereqId, to: p.item.id });
    }
  }
  // Multiple arrows can leave the same source or converge on the same target (a milestone
  // blocking three others, or blocked by three others) -- fan each one out across its node's
  // own edge instead of every arrow meeting at one center point (the reported "pile onto the
  // same point" bug).
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();
  for (const { from, to } of validDeps) {
    const o = outgoing.get(from);
    if (o) o.push(to);
    else outgoing.set(from, [to]);
    const i = incoming.get(to);
    if (i) i.push(from);
    else incoming.set(to, [from]);
  }
  const arrows: Arrow[] = validDeps.map(({ from, to }) => {
    const src = positionedById.get(from)!;
    const dst = positionedById.get(to)!;
    const srcHalf = (src.kind === "diamond" ? DIAMOND_R : BAR_HEIGHT / 2) * 0.7;
    const dstHalf = (dst.kind === "diamond" ? DIAMOND_R : BAR_HEIGHT / 2) * 0.7;
    const outs = outgoing.get(from)!;
    const ins = incoming.get(to)!;
    const exitY = src.y + fanOffset(outs.indexOf(to), outs.length, srcHalf);
    const entryY = dst.y + fanOffset(ins.indexOf(from), ins.length, dstHalf);
    // Leave from the source's own right edge (a diamond's edge is DIAMOND_R past its center,
    // not the center itself) and enter the target's left edge -- never the bar/diamond centers,
    // which is what produced the old center-to-center diagonals stabbing through bars.
    const exitX = src.kind === "diamond" ? src.x2 + DIAMOND_R : src.x2;
    const entryX = dst.kind === "diamond" ? dst.x1 - DIAMOND_R : dst.x1;
    const key = `${from}->${to}`;
    return { key, d: arrowPath(exitX, exitY, entryX, entryY, key) };
  });

  return { chartWidth, chartHeight, lanes, ticks, arrows, invalidDate };
}

/** Spreads N siblings sharing one node edge across a small band centered on that edge's own
 * midpoint, so arrows fanning in/out of the same bar/diamond land at distinct points instead of
 * a single shared one. A lone arrow (n<=1) gets no offset -- the common case renders exactly as
 * before. */
function fanOffset(index: number, count: number, halfSpan: number): number {
  if (count <= 1) return 0;
  const step = (halfSpan * 2) / (count - 1);
  return -halfSpan + index * step;
}

const ARROW_STUB = 12; // px an arrow travels straight out of its source / into its target before bending
const ARROW_CLEARANCE = 10; // extra px (past the bar's own half-height) an arrow detours before crossing a row

/** Deterministic small nudge (-2..2px) from an arrow's key -- keeps two otherwise-identical
 * elbows (same source row, same target row) from rendering as one indistinguishable line. Not
 * cryptographic, just needs to be stable across renders. */
function hashOffset(key: string, mod: number): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return Math.abs(h) % mod;
}

/** Orthogonal elbow from a source's right edge to a target's left edge -- always leaves/enters
 * horizontally, never a center-to-center diagonal cutting through the bar rectangles in between.
 *
 * Same row (y1 === y2) and the target is at or after the source: a plain horizontal line, which
 * is already the cleanest possible reading.
 *
 * Otherwise, if the target sits comfortably to the right, a standard 3-segment bend (out, over,
 * in) reads cleanest for a Gantt. If it doesn't -- either a genuinely "backwards" dependency
 * (the blocking item is scheduled LATER on the axis than what it blocks, a real shape in this
 * data) or just too little horizontal room for a clean bend -- detour above or below the
 * source's own row first, so the return leg travels through the row's margin instead of back
 * through the source bar's fill. */
function arrowPath(x1: number, y1: number, x2: number, y2: number, key: string): string {
  if (Math.abs(y1 - y2) < 0.5 && x2 >= x1) return `M${x1},${y1} L${x2},${y2}`;

  const jitter = hashOffset(key, 5) - 2;
  if (x2 - x1 >= ARROW_STUB * 2) {
    const midX = (x1 + x2) / 2 + jitter;
    return `M${x1},${y1} L${midX},${y1} L${midX},${y2} L${x2},${y2}`;
  }
  const detourY = y1 + Math.sign(y2 - y1 || 1) * (BAR_HEIGHT / 2 + ARROW_CLEARANCE) + jitter;
  const outX = x1 + ARROW_STUB;
  const inX = x2 - ARROW_STUB;
  return `M${x1},${y1} L${outX},${y1} L${outX},${detourY} L${inX},${detourY} L${inX},${y2} L${x2},${y2}`;
}

// Hard safety cap, independent of the weekly/monthly spacing choice below -- a single
// fat-fingered date (e.g. a "9999" typo for "2026" in one milestone's due) would otherwise
// hand this a multi-millennium domain and loop tens of thousands of times generating ticks
// nobody can read, sized to render that many SVG elements. Real projects never come close
// (a full year of weekly ticks is ~52); this only ever engages for already-nonsensical input.
const MAX_TICKS = 200;

function buildTicks(startMs: number, endMs: number, scaleX: (ms: number) => number, granularity: "day" | "week" | "month"): Tick[] {
  const ticks: Tick[] = [];
  const cur = new Date(startMs);
  cur.setHours(0, 0, 0, 0);
  if (granularity === "month") {
    cur.setDate(1);
    if (cur.getTime() < startMs) cur.setMonth(cur.getMonth() + 1);
    while (cur.getTime() <= endMs && ticks.length < MAX_TICKS) {
      ticks.push({ x: scaleX(cur.getTime()), label: cur.toLocaleDateString(undefined, { month: "short", year: "numeric" }) });
      cur.setMonth(cur.getMonth() + 1);
    }
  } else if (granularity === "week") {
    const mondayOffset = (cur.getDay() + 6) % 7; // Date.getDay(): 0=Sun -> align back to Monday
    cur.setDate(cur.getDate() - mondayOffset);
    while (cur.getTime() <= endMs && ticks.length < MAX_TICKS) {
      if (cur.getTime() >= startMs) {
        ticks.push({ x: scaleX(cur.getTime()), label: cur.toLocaleDateString(undefined, { month: "short", day: "numeric" }) });
      }
      cur.setDate(cur.getDate() + 7);
    }
  } else {
    // Short domain (<= ~21 days, s6-ui.md addendum) -- one tick per calendar day, no alignment
    // needed since every day in range is shown.
    while (cur.getTime() <= endMs && ticks.length < MAX_TICKS) {
      ticks.push({ x: scaleX(cur.getTime()), label: cur.toLocaleDateString(undefined, { month: "short", day: "numeric" }) });
      cur.setDate(cur.getDate() + 1);
    }
  }
  return ticks;
}
