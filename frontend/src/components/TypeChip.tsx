import { nodeTypeColor } from "../lib/palette";

/** `compact`: dot only (title attribute carries the type name) -- used in the narrow, deeply
 * nested spine tree rows; the full dot+text pill is used everywhere there's more room
 * (node header, search/attention lists). */
export function TypeChip({ type, compact = false }: { type: string; compact?: boolean }) {
  if (compact) {
    return <span className="type-chip__dot" style={{ background: nodeTypeColor(type) }} title={type} />;
  }
  return (
    <span className="type-chip">
      <span className="type-chip__dot" style={{ background: nodeTypeColor(type) }} />
      {type}
    </span>
  );
}
