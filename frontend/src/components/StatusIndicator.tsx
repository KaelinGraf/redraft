import { statusColor } from "../lib/palette";

/** Compact colored dot for tight rows (spine tree). Title attribute carries the status text
 * so it's still discoverable without spending row width on a text label. */
export function StatusDot({ status }: { status: string | null }) {
  if (!status) return null;
  return (
    <span
      className="status-ring status-ring--filled"
      style={{ background: statusColor(status) }}
      title={status}
    />
  );
}

/** Dot + text label for the node header, where there's room to spell it out. */
export function StatusBadge({ status }: { status: string | null }) {
  if (!status) return null;
  return (
    <span className="status-badge">
      <span className="status-ring status-ring--filled" style={{ background: statusColor(status) }} />
      {status}
    </span>
  );
}
