"""Admin tools: reindex, snapshot -- thin delegation onto
redraft.store.GraphStore.reindex()/.snapshot() (pin R1).

Both are GraphStore *mutating* methods (design-storage.md section 6.2 lists
reindex and snapshot alongside create_node etc. as lock-acquiring, even
though reindex never touches graph/nodes/*.md -- it acquires the lock
because rebuilding the index needs exclusivity against a concurrent writer).
Neither tool here acquires anything itself.

snapshot's pathspec scoping (pin R4: `git add -A -- graph/ reports/` from
the REDRAFT_DIR repo root, never an unscoped `add -A`) is entirely
GraphStore's responsibility; this tool passes `message`/`push` through
unchanged and does not construct or validate git arguments itself.

ReindexStats/SnapshotResult mirror GraphStore's own ReindexStats/CommitResult
field names exactly (see models.py docstring) -- model_from() converts
whatever GraphStore returns (pydantic model, dataclass, or dict) into them.

Both tools are registered with run_in_thread=False, for the same verified
reason as every write_tools.py tool -- see that module's docstring
("CRITICAL BUG FOUND AND WORKED AROUND"). reindex/snapshot touch
GraphStore's shared connection just like the write tools do.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import FastMCP

from redraft.tool_errors import translate_store_errors
from redraft.models import ReindexStats, SnapshotResult, model_from

if TYPE_CHECKING:
    from redraft.server import ServerState


def register_admin_tools(mcp: FastMCP, state: "ServerState") -> None:
    @mcp.tool(run_in_thread=False)
    def reindex() -> ReindexStats:
        """Rebuild the derived index from graph/nodes/*.md. Run after a manual
        git pull/merge, or to recover from a deleted/corrupted index/ dir."""
        with translate_store_errors():
            stats = state.store.reindex()
        return model_from(ReindexStats, stats)

    @mcp.tool(run_in_thread=False)
    def snapshot(message: str, push: bool = False) -> SnapshotResult:
        """git add (pathspec-scoped) + commit; push only if push=True (defaults
        off to avoid a surprise network operation)."""
        with translate_store_errors():
            result = state.store.snapshot(message, push=push)
        return model_from(SnapshotResult, result)
