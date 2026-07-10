/** Client-side filter over the already-loaded outline -- not a new request (s6-ui.md §10.1).
 * Filtering itself happens in SpineTree; this is just the controlled input. */
export function OutlineFilterBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="outline-filter">
      <input
        type="search"
        placeholder="Filter outline…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Filter outline"
      />
    </div>
  );
}
