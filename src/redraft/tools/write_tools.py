"""Write tools: create_node, update_node, create_edge, delete_edge,
delete_node, merge_nodes, rename_node -- thin delegation onto
redraft.store.GraphStore (pin R1).

GraphStore owns all write semantics: locking, collision checks, part_of
cycle rejection, inbound-wikilink rewriting, merge/rename mechanics. No tool
here acquires a lock or re-implements any state-dependent business rule;
each is (optional cheap stateless pre-validation) -> one GraphStore call ->
convert the result -> return.

CRITICAL BUG FOUND AND WORKED AROUND, FLAGGED FOR S1 (see final report):
every @mcp.tool below is registered with run_in_thread=False. Verified live
(fastmcp==3.4.3): FastMCP's default run_in_thread=True dispatches a sync
tool's body via anyio.to_thread.run_sync onto a worker thread that is NOT
the thread that ran build_server() (confirmed with threading.get_ident() on
both sides). GraphStore is constructed once in build_server() and (per
design-storage.md's own "one process-lifetime SQLite connection" framing)
presumably holds a single sqlite3.Connection for its lifetime -- a plain
sqlite3.Connection raises "SQLite objects created in a thread can only be
used in that same thread" the moment it's touched from a different thread
than its creator. Reproduced against tests/_stub_store.py: with the default
run_in_thread=True, the *first* create_node call already raised this,
every time -- not a rare concurrency edge case. run_in_thread=False forces
these tool bodies to run inline on the event-loop thread, which is the same
thread build_server() ran on for the standard main() -> build_server() ->
mcp.run() sequence -- verified this resolves it. This does NOT fully solve
GraphStore's own thread-safety (a future concurrent-caller context, e.g.
S1's own multi-threaded tests or a Phase-4 HTTP transport, could still hit
it) -- recommend GraphStore itself either open its connection with
check_same_thread=False plus an internal lock guarding every access, or
open a fresh connection per method call. CORRECTION (I7, found against the
real GraphStore): read_tools.py's OWN traversal queries are immune by
construction (each opens its own index_read_conn), but get_node/neighbors/
get_subgraph all also call state.store.get_node() for existence checks,
which DOES touch this same shared connection -- read tools are not exempt
after all and now carry run_in_thread=False too (see that module's own
docstring for the correction).

create_node deliberately has no part_of/edges convenience parameter, even
though GraphStore.create_node accepts them (design-storage.md section 8).
This follows the brief's own literal 5-param tool signature (type, title,
body, status, properties), which design-server.md's approved write_tools.py
design also used, flagging the two-call "create, then create_edge" flow as
a known, accepted limitation (its Risk 7) rather than a gap needing
reconciliation -- and it matches the organizing protocol's own two-phase
"create, then link" step (brief section 6 step 4). Nothing in pins R1-R8
changes this.

DESIGN GAP, PARTIALLY RESOLVED AT INTEGRATION (I4): GraphStore.merge_nodes now returns a
real MergeOutcome (kept/warnings/dropped_body_preview -- design amendment A2), so
merge_nodes below reports GraphStore's own observed decision, not a pre-call prediction.
MergeResult's old relinked_inbound/migrated_outbound fields (which required predicting
exactly which edges GraphStore would touch, via a read-only pre-call query re-deriving
design-storage.md section 6.4 steps 3-4 by hand) are gone accordingly -- a caller who needs
that detail can diff neighbors()/get_node() before and after. GraphStore.rename_node is
still pinned (design-storage.md section 8) to return only a bare `Node`, with no equivalent
outcome object, so rename_node below still predicts its `relinked` list from a pre-call
query (see that function's own comment) -- this remains best-effort, not an observed fact.

BUG FOUND AND FIXED (see final report): design-storage.md section 2's own
sanitize_title_to_id raises a bare `ValueError("title must contain at least
one character")` for an empty/whitespace title -- NOT one of the 6 pin-R7
mapped exception types. Unguarded, that ValueError would propagate through
translate_store_errors() unmapped (by design -- see tool_errors.py) and then get
masked by FastMCP(mask_error_details=True) into an opaque, unhelpful error
for a very plausible caller mistake. _validate_title() below mirrors that
exact rule (NFC-normalize, strip, reject if empty) as a stateless
pre-check, same principle as _validate_status. RESIDUAL GAP, flagged for
the orchestrator: this only catches the empty/whitespace sub-case: sanitize_
title_to_id's *second* raise site ("title contains no filesystem-safe
characters", for a title that is entirely illegal characters, e.g. "////")
is not replicated here -- doing so would require duplicating ids.py's
ILLEGAL/CONTROL character sets, which is genuinely S1's domain and risks a
subtle mismatch if duplicated by hand. That sub-case still reaches
GraphStore as a bare, unmapped ValueError today. Recommend either
extending pin R7's mapped set (e.g. storage wraps both sanitize_title_to_id
raises in a shared, mappable exception type) or accepting the residual gap.
"""
from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Any, Literal

from fastmcp import FastMCP

from redraft.tool_errors import InvalidArgumentError, translate_store_errors
from redraft.models import (
    STATUS_BY_TYPE,
    DeleteResult,
    EdgeIn,
    EdgeOut,
    EdgeType,
    MergeResult,
    NeighborEdge,
    NodeOut,
    NodeType,
    RenameResult,
    to_edge_out,
    to_node_out,
)
from redraft.store import MAX_BODY_BYTES, MAX_TITLE_CHARS
from redraft.tools.read_tools import index_read_conn, query_edges

if TYPE_CHECKING:
    from redraft.server import ServerState


def _validate_title(title: str) -> None:
    """Stateless pre-check mirroring design-storage.md section 2's own
    sanitize_title_to_id empty-title rule -- see module docstring's BUG
    FOUND note for why this exists. Also mirrors GraphStore's own MAX_TITLE_CHARS cap (a bare
    ValueError from GraphStore isn't one of pin R7's 6 mapped types, so unguarded it would be
    masked by FastMCP(mask_error_details=True) into an opaque error for a plain oversized-title
    mistake -- same failure class as the empty-title case this function already guards)."""
    if not unicodedata.normalize("NFC", title).strip():
        raise InvalidArgumentError("title must contain at least one non-whitespace character")
    if len(title) > MAX_TITLE_CHARS:
        raise InvalidArgumentError(f"title exceeds {MAX_TITLE_CHARS}-character limit", length=len(title))


def _validate_body_size(body: str) -> None:
    """Same rationale as _validate_title's MAX_TITLE_CHARS check, for GraphStore's
    MAX_BODY_BYTES cap (Batch-B hardening: a 40MB body measured ~554MB RSS / ~11s per write,
    with no cap anywhere on either the UI REST path or this MCP tool path). RESIDUAL GAP: this
    only catches an oversized *incoming* body chunk -- update_node's append mode can still push
    the FINAL body over the cap through accumulation without any single call's own `body`
    argument being oversized; GraphStore.update_node itself always enforces the true final
    size (never silently truncates), but a rejection reaching only that layer surfaces here as
    an unmapped, FastMCP-masked ValueError rather than this function's clearer message -- same
    accepted residual shape as sanitize_title_to_id's illegal-character-only case, documented
    above in this module's own docstring."""
    size = len(body.encode("utf-8"))
    if size > MAX_BODY_BYTES:
        raise InvalidArgumentError(f"body exceeds {MAX_BODY_BYTES}-byte limit", size=size)


def _validate_status(type_: str, status: str | None) -> None:
    """Stateless pre-check only -- GraphStore re-validates independently
    (pin R1); this exists purely to fail fast, before ever touching the
    write lock, with a clearer message than a round trip through the store
    would give. STATUS_BY_TYPE is the brief's own type table (section 3.1),
    so this can never diverge from what GraphStore itself enforces."""
    legal = STATUS_BY_TYPE.get(type_)
    if legal is None:
        if status is not None:
            raise InvalidArgumentError(f"type '{type_}' does not carry a status", type=type_, status=status)
    elif status is not None and status not in legal:
        raise InvalidArgumentError(
            f"'{status}' is not a legal status for type '{type_}'",
            type=type_, status=status, legal=sorted(legal),
        )


def register_write_tools(mcp: FastMCP, state: "ServerState") -> None:
    @mcp.tool(run_in_thread=False)
    def create_node(
        type: NodeType,
        title: str,
        body: str = "",
        status: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> NodeOut:
        """Create a new node. Does not attach it anywhere -- call create_edge next."""
        _validate_title(title)
        _validate_status(type, status)
        _validate_body_size(body)
        with translate_store_errors():
            node = state.store.create_node(type=type, title=title, body=body, status=status, properties=properties)
        return to_node_out(node)

    @mcp.tool(run_in_thread=False)
    def update_node(
        id: str,
        body: str | None = None,
        mode: Literal["append", "replace"] = "append",
        status: str | None = None,
        properties: dict[str, Any] | None = None,
        remove_properties: list[str] | None = None,
    ) -> NodeOut:
        """Update a node's body/status/properties. Cannot change title or edges
        (use rename_node / create_edge / delete_edge for those) -- pin R5.
        `properties` MERGES onto the existing dict (it cannot delete a key); to clear a
        properties key, list it in `remove_properties` instead -- removal is applied after the
        merge, so it wins if the same key appears in both, and removing an absent key is a
        no-op."""
        if body is not None:
            _validate_body_size(body)
        with translate_store_errors():
            node = state.store.update_node(
                id, body=body, mode=mode, status=status, properties=properties, remove_properties=remove_properties
            )
        return to_node_out(node)

    @mcp.tool(run_in_thread=False)
    def create_edge(src: str, dst: str, type: EdgeType) -> EdgeOut:
        """Create a directed edge. STRICT (pin R2): dst must already exist --
        raises not_found otherwise, no dangling-edge creation via this tool.
        A part_of edge when src already has a *different* part_of parent
        raises collision; call delete_edge first to re-parent."""
        with translate_store_errors():
            edge = state.store.create_edge(src, dst, type)
        return to_edge_out(edge)

    @mcp.tool(run_in_thread=False)
    def create_edges(edges: list[EdgeIn]) -> list[EdgeOut]:
        """Create many edges in ONE atomic operation (GraphStore.create_edges): every edge is
        validated together -- including part_of single-parent/cycle rules considering the
        OTHER edges in this same batch -- before anything is written. On any hard error the
        whole call fails, with the store's own message, and NOTHING from this batch is
        applied (no partial report). Prefer this over looping create_edge when linking several
        edges from one dump (organizing-protocol.md §3.4)."""
        with translate_store_errors():
            results = state.store.create_edges([(e.src, e.dst, e.type) for e in edges])
        return [to_edge_out(edge) for edge in results]

    @mcp.tool(run_in_thread=False)
    def delete_edge(src: str, dst: str, type: EdgeType) -> DeleteResult:
        """Remove an edge."""
        with index_read_conn(state.config.graph_dir) as conn:
            existed = conn.execute(
                "SELECT 1 FROM edges WHERE src = ? AND dst = ? AND type = ?", (src, dst, type)
            ).fetchone() is not None
        with translate_store_errors():
            state.store.delete_edge(src, dst, type)
        return DeleteResult(ok=True, existed=existed)

    @mcp.tool(run_in_thread=False)
    def delete_node(id: str) -> DeleteResult:
        """Remove a node's file (Git retains history). Inbound edges from other
        nodes are left dangling by design -- surfaced here, not silently dropped."""
        with index_read_conn(state.config.graph_dir) as conn:
            orphaned = query_edges(conn, anchor_col="dst", anchor_val=id, edge_types=None, direction="in")
        with translate_store_errors():
            state.store.delete_node(id)
        return DeleteResult(ok=True, existed=True, orphaned_inbound_edges=orphaned)

    @mcp.tool(run_in_thread=False)
    def merge_nodes(keep_id: str, drop_id: str) -> MergeResult:
        """Dedup: repoint drop's inbound edges to keep, migrate drop's outbound
        edges onto keep (deduped; a part_of conflict is a warning, never a
        failure), delete drop's file. Body is NOT auto-merged -- inspect
        dropped_body_preview and update_node(keep_id, ..., mode='append')
        first if unique content from drop should survive (pin R3)."""
        if keep_id == drop_id:
            raise InvalidArgumentError("keep_id and drop_id must differ", keep_id=keep_id, drop_id=drop_id)
        # I4: GraphStore.merge_nodes returns a real MergeOutcome (design amendment A2) --
        # observed fact, not the pre-call prediction this tool used to reconstruct by hand.
        with translate_store_errors():
            outcome = state.store.merge_nodes(keep_id, drop_id)
        return MergeResult(
            kept=to_node_out(outcome.kept),
            dropped_id=drop_id,
            dropped_body_preview=outcome.dropped_body_preview,
            warnings=outcome.warnings,
        )

    @mcp.tool(run_in_thread=False)
    def rename_node(id: str, new_title: str) -> RenameResult:
        """Rename a node, rewriting every inbound frontmatter wikilink to the
        new id. Inline [[wikilinks]] in Markdown body prose are NOT rewritten
        (the index only tracks frontmatter edges) -- flagged in
        body_references_not_updated, not silently left unmentioned."""
        _validate_title(new_title)
        # I4: GraphStore.rename_node returns only a bare Node (no outcome object), so
        # `relinked` below is still a pre-call prediction from this snapshot of inbound
        # edges, not an observation of what the store actually rewrote.
        with index_read_conn(state.config.graph_dir) as conn:
            inbound = query_edges(conn, anchor_col="dst", anchor_val=id, edge_types=None, direction="out")
        with translate_store_errors():
            node = state.store.rename_node(id, new_title)
        new_id = getattr(node, "id", None) or new_title
        relinked = [NeighborEdge(src=e.src, dst=new_id, type=e.type, direction="out") for e in inbound]
        with index_read_conn(state.config.graph_dir) as conn:
            literal = f"[[{id}]]"
            body_refs = [
                row[0]
                for row in conn.execute("SELECT id, body FROM nodes").fetchall()
                if literal in (row[1] or "")
            ]
        return RenameResult(old_id=id, new_id=new_id, relinked=relinked, body_references_not_updated=body_refs)
