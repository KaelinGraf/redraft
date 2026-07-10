import { useEffect, useState } from "react";

/** One setTimeout/clearTimeout pair, no new npm dependency (s6-ui.md §5.3). Used by
 * CreateNodeDialog's title field (300ms) before firing useDedupHints. */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}
