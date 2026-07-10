import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useGitStatus, useSearch } from "../api/queries";
import { useActiveTab, buildNodeLink } from "../lib/nav";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { TypeChip } from "./TypeChip";
import { Logo } from "./Logo";
import { SnapshotDialog } from "./dialogs/SnapshotDialog";

/** TopBar (graph dir name, GitStatusBadge, SnapshotButton -> SnapshotDialog, s6-ui.md
 * §10.1) plus a global search box -- GET /api/search (s6-ui.md §9's table) has no other
 * consumer in this design's own named component tree, and a "find anything" box is core
 * operator capability the organizing protocol itself leads with, so it lives here rather than
 * shipping the endpoint with zero UI trigger.
 *
 * DEVIATION, flagged: the design's own TopBar description names "graph dir name" as a
 * display element, but no endpoint in s6-ui.md §9's table returns the graph directory path
 * (StatusOut/GitStatusOut/SchemaOut all omit it) -- out of this frontend actor's scope to add
 * a backend field to already-gated backend code, so a static app title is shown instead. */
export function TopBar() {
  const { data: gitStatus } = useGitStatus();
  const [showSnapshot, setShowSnapshot] = useState(false);

  return (
    <div className="topbar">
      <Logo size={24} />
      <span className="topbar__title">redraft</span>
      <GlobalSearch />
      <span className="topbar__spacer" />
      <span className={`dirty-badge${gitStatus?.dirty ? " dirty-badge--dirty" : ""}`}>
        <span className="dirty-badge__dot" />
        {gitStatus?.dirty ? `${gitStatus.changed_paths.length} uncommitted` : "clean"}
      </span>
      <button className="btn btn--primary btn--sm" onClick={() => setShowSnapshot(true)}>
        Snapshot
      </button>
      {showSnapshot ? <SnapshotDialog onClose={() => setShowSnapshot(false)} /> : null}
    </div>
  );
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const debouncedQ = useDebouncedValue(q, 300);
  const { data: hits, isFetching } = useSearch(debouncedQ);
  const activeTab = useActiveTab();
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  return (
    <div className="search-box" ref={boxRef}>
      <input
        type="search"
        placeholder="Search graph…"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        style={{ width: 260 }}
        aria-label="Search graph"
      />
      {open && debouncedQ.trim() ? (
        <div className="search-results">
          {isFetching ? <div className="faint" style={{ padding: 6 }}>searching…</div> : null}
          {!isFetching && hits?.length === 0 ? (
            <div className="faint" style={{ padding: 6 }}>
              No matches.
            </div>
          ) : null}
          {hits?.map((h) => (
            <Link
              key={h.node.id}
              to={buildNodeLink(activeTab, h.node.id)}
              className="search-results__item"
              onClick={() => {
                setOpen(false);
                setQ("");
              }}
            >
              <TypeChip type={h.node.type} compact />
              {h.node.title}
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}
