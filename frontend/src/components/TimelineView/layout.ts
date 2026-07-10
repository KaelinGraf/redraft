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
 * NodeEdgesPanel already uses for edge endpoints. */
export function buildLayout(scheduled: TimelineItem[], titleFor: (id: string) => string): TimelineLayout {
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

  // Denser axis for long spans, roomier for short ones; a floor width keeps a short span from
  // looking cramped in its own scrollable container (no page-level horizontal scroll -- the
  // OUTER page never scrolls; .timeline-scroll owns overflow-x, per the build brief). scaleX
  // maps the domain into plotWidth only, NOT the full chartWidth -- LABEL_BUFFER reserves a
  // trailing strip past the last tick so an item's title label (rendered after its bar/
  // diamond, per s6-ui.md IA: "truncate + tooltip") has room to draw for items near the right
  // edge instead of being clipped by the svg's own bounds.
  // Capped at MAX_PLOT_WIDTH -- the same fat-fingered-year typo that motivates MAX_TICKS
  // above (buildTicks) would otherwise ask for a millions-of-pixels-wide <svg>, which is well
  // past what browsers reliably rasterize (common engine ceilings sit in the ~32,767px
  // neighborhood) and would be unusably slow to lay out regardless. Capping here just means a
  // few pixels represent more days for an already-nonsensical range -- graceful degradation,
  // not a correctness change for any real project's span.
  const pxPerDay = totalDays > 180 ? 5 : totalDays > 60 ? 10 : totalDays > 21 ? 20 : 32;
  const plotWidth = Math.min(MAX_PLOT_WIDTH, Math.max(640, Math.round(totalDays * pxPerDay)));
  const chartWidth = plotWidth + LABEL_BUFFER;
  const scaleX = (ms: number) => ((ms - domainStart) / (domainEnd - domainStart)) * plotWidth;
  const ticks = buildTicks(domainStart, domainEnd, scaleX, spanDays > 120);

  // Group into swimlanes by part_of (null -> "Ungrouped", sorted last); within each lane,
  // greedy interval-packing assigns overlapping items to separate sub-rows so bars/diamonds
  // that share a time range never visually collide.
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
  for (const key of orderedKeys) {
    const laneItems = byKey.get(key)!.slice().sort((a, b) => a.startMs - b.startMs);
    const subRowEnds: number[] = []; // subRowEnds[i] = end ms of the last item placed in sub-row i
    const positioned: PositionedItem[] = [];
    for (const p of laneItems) {
      let row = subRowEnds.findIndex((endMs) => endMs + DAY_MS / 2 <= p.startMs);
      if (row === -1) {
        row = subRowEnds.length;
        subRowEnds.push(p.endMs);
      } else {
        subRowEnds[row] = p.endMs;
      }
      const posItem: PositionedItem = {
        item: p.item,
        kind: p.kind,
        x1: scaleX(p.startMs),
        x2: scaleX(p.endMs),
        y: y + row * ROW_HEIGHT + ROW_HEIGHT / 2,
      };
      positioned.push(posItem);
      positionedById.set(p.item.id, posItem);
    }
    const height = Math.max(1, subRowEnds.length) * ROW_HEIGHT;
    lanes.push({ key, title: key === "" ? "Ungrouped" : titleFor(key), y, height, items: positioned });
    y += height + LANE_GAP;
  }
  const chartHeight = y;

  // Dependency arrows: prerequisite -> dependent, drawn only when BOTH endpoints landed a
  // pixel position above (i.e. neither is unscheduled or invalid-date) -- skipped gracefully
  // otherwise, never a dangling/off-chart arrow.
  const arrows: Arrow[] = [];
  for (const p of prepped) {
    const to = positionedById.get(p.item.id)!;
    for (const prereqId of p.item.depends_on) {
      const from = positionedById.get(prereqId);
      if (!from) continue;
      arrows.push({ key: `${prereqId}->${p.item.id}`, d: arrowPath(from.x2, from.y, to.x1, to.y) });
    }
  }

  return { chartWidth, chartHeight, lanes, ticks, arrows, invalidDate };
}

/** A smooth S-curve between two points, horizontal at both ends -- reads cleanly whether the
 * two items share a lane (y1 === y2, degenerates to a straight horizontal line) or sit in
 * different lanes (curves gracefully across the lanes between them, per the build brief). */
function arrowPath(x1: number, y1: number, x2: number, y2: number): string {
  const midX = (x1 + x2) / 2;
  return `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`;
}

// Hard safety cap, independent of the weekly/monthly spacing choice below -- a single
// fat-fingered date (e.g. a "9999" typo for "2026" in one milestone's due) would otherwise
// hand this a multi-millennium domain and loop tens of thousands of times generating ticks
// nobody can read, sized to render that many SVG elements. Real projects never come close
// (a full year of weekly ticks is ~52); this only ever engages for already-nonsensical input.
const MAX_TICKS = 200;

function buildTicks(startMs: number, endMs: number, scaleX: (ms: number) => number, monthly: boolean): Tick[] {
  const ticks: Tick[] = [];
  const cur = new Date(startMs);
  cur.setHours(0, 0, 0, 0);
  if (monthly) {
    cur.setDate(1);
    if (cur.getTime() < startMs) cur.setMonth(cur.getMonth() + 1);
    while (cur.getTime() <= endMs && ticks.length < MAX_TICKS) {
      ticks.push({ x: scaleX(cur.getTime()), label: cur.toLocaleDateString(undefined, { month: "short", year: "numeric" }) });
      cur.setMonth(cur.getMonth() + 1);
    }
  } else {
    const mondayOffset = (cur.getDay() + 6) % 7; // Date.getDay(): 0=Sun -> align back to Monday
    cur.setDate(cur.getDate() - mondayOffset);
    while (cur.getTime() <= endMs && ticks.length < MAX_TICKS) {
      if (cur.getTime() >= startMs) {
        ticks.push({ x: scaleX(cur.getTime()), label: cur.toLocaleDateString(undefined, { month: "short", day: "numeric" }) });
      }
      cur.setDate(cur.getDate() + 7);
    }
  }
  return ticks;
}
