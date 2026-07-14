"""GraphStore: the single public surface for the canonical store + derived index (design §6, §8).

All mutating methods acquire the single write lock for their full body and follow the
crash-consistency ordering: canonical file(s) first (atomic), then the SQLite index, then
commit, then release the lock (design §6.3). Individual CRUD operations never touch git —
only snapshot() does.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import sqlite_vec

from redraft import gitops, graphrules, index
from redraft.config import GraphPaths
from redraft.errors import CollisionError, CycleError, NotFoundError
from redraft.ids import collision_key, sanitize_title_to_id
from redraft.locking import write_lock
from redraft.nodefile import _atomic_write, dump_node_file, load_node_file
from redraft.retrieval import RetrievalConfig, embed_delete, embed_upsert, ensure_embedding_schema
from redraft.schema import EdgeType, Edge, LIST_EDGE_TYPES, Node, NodeType, validate_status


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Unbounded title/body accepted an arbitrary in-memory string all the way from a request body
# to a YAML dump + FTS index write, with no cap anywhere on either the UI REST path or the MCP
# tool path (a 40MB body measured ~554MB RSS and ~11s per write). Enforced HERE, at the
# GraphStore layer both paths funnel through -- not only in ui/models.py's Pydantic request
# models, which the MCP create_node/update_node tools never pass through at all.
MAX_TITLE_CHARS = 300  # organizing-protocol.md §2 already asks authors to keep titles "well
# under 100 characters" (detail belongs in the body); 300 is generous headroom above that
# guidance while still bounding a pathological input -- a title is a label, not prose.
MAX_BODY_BYTES = 256 * 1024  # 256 KiB. A design-note body is prose (a few hundred KB is
# already an implausibly long single node); large binary/opaque content already has a
# dedicated, purpose-built path (the attachments upload route, streamed and capped at 50 MiB
# in ui/mutations.py) -- raising this cap to match would just move the DoS surface, not close it.


def _check_title_length(title: str) -> None:
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError(f"title exceeds {MAX_TITLE_CHARS}-character limit (got {len(title)})")


def _check_body_size(body: str) -> None:
    size = len(body.encode("utf-8"))
    if size > MAX_BODY_BYTES:
        raise ValueError(f"body exceeds {MAX_BODY_BYTES}-byte limit (got {size} bytes)")


def _semantic_form(node: Node) -> str:
    """Canonical serialized form excluding created/updated, for the no-op-write short-circuit
    (design §4.5). Reuses dump_node_file itself rather than a parallel field list, so it can
    never drift out of sync with what the writer actually emits.
    """
    return dump_node_file(node.model_copy(update={"created": "", "updated": ""}))


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class MergeOutcome:
    """merge_nodes' return value (design amendment A2). warnings currently only ever carries a
    part_of adoption conflict (keep already has a different parent than drop's), but is a list
    so future non-fatal merge conditions have somewhere to go without another signature change.
    """

    kept: Node
    warnings: list[str]
    dropped_body_preview: str


class GraphStore:
    def __init__(self, graph_dir: Path, retrieval_config: RetrievalConfig | None = None) -> None:
        """retrieval_config is OPTIONAL and defaults to None (embeddings disabled): every
        direct GraphStore construction that omits it (every existing storage test) keeps
        behaving exactly as before, with no sqlite-vec load and no model touched. Passing a
        RetrievalConfig (S3b: always the case when built via server.build_server) loads
        sqlite-vec into self.con and bootstraps the embedding schema up front, so the
        self.reindex() call below can already embed on this first pass.
        """
        self.paths = GraphPaths(Path(graph_dir))
        self.paths.nodes_dir.mkdir(parents=True, exist_ok=True)
        self.con = index.open_index(self.paths.index_db)
        self._retrieval_config = retrieval_config
        if retrieval_config is not None:
            self.con.enable_load_extension(True)
            sqlite_vec.load(self.con)
            self.con.enable_load_extension(False)
            ensure_embedding_schema(self.con, retrieval_config.embedding_model_id, retrieval_config.embedding_dims)
        self.reindex()

    def _write_lock(self):
        return write_lock(self.paths.root)

    @contextmanager
    def _locked_transaction(self) -> Iterator[None]:
        """Every mutator's body runs under this instead of a bare `with self._write_lock():`
        (design §6.3's crash-consistency ordering is per-method; this closes the gap ONE
        step up, at connection-lifetime scope). self.con is a single persistent connection
        for the store's whole life, and every mutator issues its index._upsert_node_index /
        _remove_node_index calls uncommitted, only committing at the very end of a successful
        run. If a mutator raises partway through -- after some of those index writes but
        before its own self.con.commit() -- the partial writes previously stayed sitting in
        the still-open transaction, silently landing on disk whenever the NEXT unrelated
        mutation happened to call self.con.commit(). Rolling back here on ANY exception
        escaping the locked body (before the lock itself is released) discards that partial
        work instead, so a failed mutation can never leak into a later, unrelated one.
        embed_upsert/embed_delete (S3a) commit internally by design and are unaffected either
        way -- this only ever discards the plain index._* rows a failed mutator left pending.
        """
        with self._write_lock():
            try:
                yield
            except BaseException:
                self.con.rollback()
                raise

    def _node_path(self, node_id: str) -> Path:
        return self.paths.nodes_dir / f"{node_id}.md"

    def _require_exists(self, node_id: str) -> None:
        row = self.con.execute("SELECT 1 FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            raise NotFoundError(node_id)

    def _find_existing_id(self, candidate_id: str) -> str | None:
        """Case-insensitive/NFC collision probe against graph/nodes/ **on disk** (design §2
        step 3 is explicit: "if it exists on disk" — not merely in the derived index, since a
        hand-dropped file that hasn't been reindex()-ed yet must still be caught).
        """
        key = collision_key(candidate_id)
        for path in self.paths.nodes_dir.glob("*.md"):
            if collision_key(path.stem) == key:
                return path.stem
        return None

    def _load_edges(self, node_id: str) -> dict[str, Any]:
        rows = self.con.execute("SELECT dst, type FROM edges WHERE src = ?", (node_id,)).fetchall()
        result: dict[str, Any] = {"part_of": None, **{e.value: [] for e in LIST_EDGE_TYPES}}
        for dst, edge_type in rows:
            if edge_type == EdgeType.PART_OF.value:
                result["part_of"] = dst
            else:
                result[edge_type].append(dst)
        return result

    def _rewrite_inbound_links(
        self, old_id: str, new_id: str, referrers: dict[str, list[EdgeType]]
    ) -> list[Node]:
        """Load each src in `referrers` and replace old_id -> new_id in the given edge-type
        keys. Self-loop guard: if src == new_id, DROP that edge instead of rewriting it into a
        self-loop. Shared by merge_nodes and rename_node (design §6.4) — for merge, new_id is
        the surviving node's existing id, so a referrer that already pointed at both keep and
        drop hits the guard; for rename, new_id is a fresh id nothing previously referenced, so
        the guard can't fire and id's own pre-existing self-reference (if any) is correctly
        relabeled rather than dropped. Pure computation, no disk writes. Never bumps `updated`
        (a mechanical reference fix, not a semantic edit to the referrer).
        """
        touched: list[Node] = []
        for src, edge_types in referrers.items():
            node = load_node_file(self._node_path(src))
            updates: dict[str, Any] = {}
            for edge_type in edge_types:
                if edge_type is EdgeType.PART_OF:
                    updates["part_of"] = None if src == new_id else new_id
                else:
                    current = updates.get(edge_type.value, getattr(node, edge_type.value))
                    if src == new_id:
                        updates[edge_type.value] = [d for d in current if d != old_id]
                    else:
                        updates[edge_type.value] = [new_id if d == old_id else d for d in current]
            touched.append(node.model_copy(update=updates))
        return touched

    # -- write path (design §6.4) -----------------------------------------------------------

    def create_node(
        self,
        *,
        type: NodeType,
        title: str,
        body: str = "",
        status: str | None = None,
        properties: dict[str, Any] | None = None,
        part_of: str | None = None,
        edges: dict[EdgeType, str | list[str]] | None = None,
    ) -> Node:
        node_type = NodeType(type)
        resolved_status = validate_status(node_type, status)
        title = unicodedata.normalize("NFC", title)
        _check_title_length(title)
        _check_body_size(body)

        with self._locked_transaction():
            node_id = sanitize_title_to_id(title)
            existing = self._find_existing_id(node_id)
            if existing is not None:
                raise CollisionError(existing, self._node_path(existing))

            if part_of is not None:
                self._require_exists(part_of)
            edge_lists: dict[str, list[str]] = {e.value: [] for e in LIST_EDGE_TYPES}
            if edges:
                for edge_type, targets in edges.items():
                    resolved_type = EdgeType(edge_type)
                    if resolved_type is EdgeType.PART_OF:
                        # part_of is its own top-level kwarg (scalar cardinality, cycle-checked);
                        # letting it through here too would collide with that kwarg when building
                        # Node(...) below and crash with a confusing "multiple values" TypeError.
                        raise ValueError("use the part_of= parameter for part_of, not edges=")
                    target_list = [targets] if isinstance(targets, str) else list(targets)
                    for target in target_list:
                        self._require_exists(target)
                    edge_lists[resolved_type.value] = target_list

            if part_of is not None and graphrules.would_create_cycle(self.con, node_id, part_of):
                raise CycleError(node_id, part_of)

            now = _utc_now_str()
            node = Node(
                id=node_id, type=node_type, title=title, body=body, status=resolved_status,
                properties=properties or {}, part_of=part_of, created=now, updated=now,
                **edge_lists,
            )
            text = dump_node_file(node)
            _atomic_write(self._node_path(node_id), text)
            index._upsert_node_index(self.con, node, content_hash=_content_hash(text))
            if self._retrieval_config is not None:
                embed_upsert(self.con, self._retrieval_config, node.id, str(node.type), node.title, node.body)
            self.con.commit()
            return node

    def update_node(
        self,
        id: str,
        *,
        body: str | None = None,
        mode: Literal["append", "replace"] = "append",
        status: str | None = None,
        properties: dict[str, Any] | None = None,
        remove_properties: list[str] | None = None,
    ) -> Node:
        """`properties` merges onto the node's existing properties (dict.update, unchanged --
        additive/overwrite only, never removes a key). `remove_properties` is applied AFTER
        that merge: each listed key is deleted from the result; removing a key that isn't
        present is a no-op, not an error. If a key appears in BOTH `properties` and
        `remove_properties`, removal wins (the merge writes it, then the removal deletes it).
        This is the only way to delete a properties key -- `properties` alone cannot.
        """
        with self._locked_transaction():
            self._require_exists(id)
            path = self._node_path(id)
            current = load_node_file(path)

            new_body = current.body
            if body is not None:
                stripped = body.strip()
                new_body = stripped if mode == "replace" or not current.body else f"{current.body}\n\n{stripped}"
                # Checked against the FINAL new_body, not just the incoming chunk -- append mode
                # can push a body over the cap through accumulation even when no single call's
                # `body` argument is oversized on its own. A pre-existing oversized body (e.g.
                # from before this cap existed) is left alone by any update that doesn't touch
                # body at all (the `body is None` case, above this block, never reaches here).
                _check_body_size(new_body)

            new_status = validate_status(current.type, status) if status is not None else current.status
            new_properties = dict(current.properties)
            if properties:
                new_properties.update(properties)
            if remove_properties:
                for key in remove_properties:
                    new_properties.pop(key, None)

            proposed = current.model_copy(update={"body": new_body, "status": new_status, "properties": new_properties})

            if _semantic_form(proposed) == _semantic_form(current):
                return current  # no-op short-circuit: no file write, no `updated` bump (design §4.5)

            proposed = proposed.model_copy(update={"updated": _utc_now_str()})
            text = dump_node_file(proposed)
            _atomic_write(path, text)
            index._upsert_node_index(self.con, proposed, content_hash=_content_hash(text))
            if self._retrieval_config is not None:
                embed_upsert(self.con, self._retrieval_config, proposed.id, str(proposed.type), proposed.title, proposed.body)
            self.con.commit()
            return proposed

    def delete_node(self, id: str) -> None:
        with self._locked_transaction():
            self._require_exists(id)
            os.remove(self._node_path(id))
            index._remove_node_index(self.con, id)
            if self._retrieval_config is not None:
                embed_delete(self.con, id)
            self.con.commit()

    def _plan_edge(
        self,
        src: str,
        node: Node,
        dst: str,
        edge_type: EdgeType,
        *,
        parent_of: Callable[[str], str | None] | None = None,
    ) -> tuple[Node, bool]:
        """Pure (no I/O) validate-and-compute step shared by create_edge and create_edges
        (design §6.4): given src's CURRENT proposed Node and a candidate (dst, edge_type),
        enforce the A1 part_of single-parent rule + cycle rejection, or list-append dedup, and
        return (proposed_node, changed) -- never writes a file, touches the index, or commits.

        `parent_of`, passed only by create_edges' batch validation, augments the part_of cycle
        walk with that batch's own in-flight edges (see graphrules.would_create_cycle); a bare
        create_edge call always leaves it None, i.e. the unchanged pure-current-state walk.
        """
        if edge_type is EdgeType.PART_OF:
            # A1: an existing DIFFERENT parent is no longer silently overwritten — the caller
            # must delete_edge the old part_of first, making reparenting explicit (reconciliation
            # pin R2). Same parent = idempotent no-op, unchanged below.
            if node.part_of is not None and node.part_of != dst:
                raise CollisionError(
                    node.part_of,
                    self._node_path(node.part_of),
                    message=(
                        f"{src!r} already has part_of parent {node.part_of!r}; "
                        f"delete_edge({src!r}, {node.part_of!r}, EdgeType.PART_OF) "
                        "first to reparent"
                    ),
                )
            if graphrules.would_create_cycle(self.con, src, dst, parent_of=parent_of):
                raise CycleError(src, dst)
            changed = node.part_of != dst
            return (node.model_copy(update={"part_of": dst}) if changed else node), changed
        current_list = getattr(node, edge_type.value)
        changed = dst not in current_list
        proposed = node.model_copy(update={edge_type.value: [*current_list, dst]}) if changed else node
        return proposed, changed

    def create_edge(self, src: str, dst: str, type: EdgeType) -> Edge:
        edge_type = EdgeType(type)
        with self._locked_transaction():
            self._require_exists(src)
            self._require_exists(dst)

            src_node = load_node_file(self._node_path(src))
            proposed, changed = self._plan_edge(src, src_node, dst, edge_type)

            if changed:
                proposed = proposed.model_copy(update={"updated": _utc_now_str()})
                text = dump_node_file(proposed)
                _atomic_write(self._node_path(src), text)
                index._upsert_node_index(self.con, proposed, content_hash=_content_hash(text))
                self.con.commit()

            dst_type = NodeType(self.con.execute("SELECT type FROM nodes WHERE id = ?", (dst,)).fetchone()[0])
            warning = graphrules.check_edge_convention(proposed.type, dst_type, edge_type)
            return Edge(id=index.edge_id(src, dst, edge_type.value), src=src, dst=dst, type=edge_type, warning=warning)

    def create_edges(self, edges: list[tuple[str, str, str]]) -> list[Edge]:
        """Create N edges in ONE atomic operation (validate-then-apply, one write lock, one
        commit) -- the batch counterpart to create_edge, for the organizing protocol's "link
        several edges from one dump" step (docs/protocol/organizing-protocol.md §3.4). Returns
        one Edge per input edge, IN INPUT ORDER, each carrying its own convention warning.

        Every edge is validated BEFORE anything is written: on the first hard error
        (NotFoundError/CollisionError/CycleError/ValueError), nothing from this batch is
        applied -- not even the edges validated earlier in the same call. Validation and
        in-memory state ARE built together in one pass (`proposed` below accumulates each
        src's running Node as its own edges are processed), but that pass never touches disk,
        the index, or self.con.commit() -- only the second pass (after the whole batch has
        validated clean) does, which is what makes a partial failure leave zero footprint.

        `proposed` doubles as both the "load each source file at most once" cache AND the
        within-batch state _plan_edge's part_of checks read against -- so a second part_of edge
        for a src already touched earlier in this SAME batch sees that pending change (not
        stale pre-batch state), which is what makes both within-batch traps below fire:
          - collision: two edges give the same src two DIFFERENT part_of parents in one batch
            -- the second sees the first's pending parent via `proposed` and raises exactly
            like an existing-different-parent collision would.
          - cycle: a part_of cycle spread across more than one edge of the batch (e.g. A->B
            then B->A, neither of which alone cycles against pre-batch state) -- `batch_parent_of`
            below is `_plan_edge`'s `parent_of` override, consulting `proposed` first and
            falling back to committed state, so graphrules.would_create_cycle's walk sees the
            batch's own in-flight edges too.
        A duplicate edge (twice in the batch, or already present before the batch started) is a
        no-op the second time (changed=False) but still returns its own Edge+warning below --
        never double-applied, never dropped from the result.
        """
        parsed = [(src, dst, EdgeType(raw_type)) for src, dst, raw_type in edges]
        with self._locked_transaction():
            proposed: dict[str, Node] = {}
            changed_srcs: set[str] = set()

            def batch_parent_of(node_id: str) -> str | None:
                pending = proposed.get(node_id)
                return pending.part_of if pending is not None else graphrules._db_parent(self.con, node_id)

            for src, dst, edge_type in parsed:
                self._require_exists(src)
                self._require_exists(dst)
                if src not in proposed:
                    proposed[src] = load_node_file(self._node_path(src))
                new_node, changed = self._plan_edge(src, proposed[src], dst, edge_type, parent_of=batch_parent_of)
                proposed[src] = new_node
                if changed:
                    changed_srcs.add(src)

            now = _utc_now_str()
            for src in changed_srcs:
                node = proposed[src].model_copy(update={"updated": now})
                proposed[src] = node
                text = dump_node_file(node)
                _atomic_write(self._node_path(src), text)
                index._upsert_node_index(self.con, node, content_hash=_content_hash(text))

            results = []
            for src, dst, edge_type in parsed:
                node = proposed[src]
                dst_type = NodeType(self.con.execute("SELECT type FROM nodes WHERE id = ?", (dst,)).fetchone()[0])
                warning = graphrules.check_edge_convention(node.type, dst_type, edge_type)
                results.append(
                    Edge(id=index.edge_id(src, dst, edge_type.value), src=src, dst=dst, type=edge_type, warning=warning)
                )

            if changed_srcs:
                self.con.commit()
            return results

    def delete_edge(self, src: str, dst: str, type: EdgeType) -> None:
        edge_type = EdgeType(type)
        with self._locked_transaction():
            # A3: only src must exist — dst may already be gone (a dangling edge), and this is
            # precisely the call the dangling_edges() -> delete_edge repair loop needs to work.
            self._require_exists(src)
            src_node = load_node_file(self._node_path(src))
            if edge_type is EdgeType.PART_OF:
                changed = src_node.part_of == dst
                proposed = src_node.model_copy(update={"part_of": None}) if changed else src_node
            else:
                current_list = getattr(src_node, edge_type.value)
                changed = dst in current_list
                new_list = [d for d in current_list if d != dst]
                proposed = src_node.model_copy(update={edge_type.value: new_list}) if changed else src_node

            if changed:
                proposed = proposed.model_copy(update={"updated": _utc_now_str()})
                text = dump_node_file(proposed)
                _atomic_write(self._node_path(src), text)
                index._upsert_node_index(self.con, proposed, content_hash=_content_hash(text))
                self.con.commit()

    def merge_nodes(self, keep_id: str, drop_id: str) -> MergeOutcome:
        with self._locked_transaction():
            if keep_id == drop_id:
                raise ValueError("merge_nodes: keep_id and drop_id must be different")
            self._require_exists(keep_id)
            self._require_exists(drop_id)

            # drop is deleted at the end of this call, never rewritten, so reading it via the
            # index (not load_node_file) is fine here — the extra-preservation rule only binds
            # reads that feed a rewrite.
            drop_node = self.get_node(drop_id)

            rows = self.con.execute("SELECT src, type FROM edges WHERE dst = ?", (drop_id,)).fetchall()
            referrers: dict[str, list[EdgeType]] = {}
            for src, edge_type in rows:
                referrers.setdefault(src, []).append(EdgeType(edge_type))

            # dry-run cycle check for every repointed part_of edge, before any file is touched
            for src, edge_types in referrers.items():
                if EdgeType.PART_OF in edge_types and src != keep_id:
                    if graphrules.would_create_cycle(self.con, src, keep_id):
                        raise CycleError(src, keep_id)

            touched = self._rewrite_inbound_links(drop_id, keep_id, referrers)
            keep_node = next((n for n in touched if n.id == keep_id), None) or load_node_file(
                self._node_path(keep_id)
            )
            other_referrers = [n for n in touched if n.id not in (keep_id, drop_id)]

            # A2: migrate drop's own outbound edges onto keep (mechanical only — body is NOT
            # auto-merged). List edge types are unioned and deduped against keep's CURRENT list
            # (i.e. keep_node, already reflecting any inbound self-loop guard above — keep may
            # just have lost a part_of=drop_id to that guard), skipping loops back to either side
            # of this merge (keep_id: would be a self-loop; drop_id: would dangle post-delete).
            keep_updates: dict[str, Any] = {}
            for edge_type in LIST_EDGE_TYPES:
                current = getattr(keep_node, edge_type.value)
                merged = list(current)
                for target in getattr(drop_node, edge_type.value):
                    if target not in (keep_id, drop_id) and target not in merged:
                        merged.append(target)
                if merged != current:
                    keep_updates[edge_type.value] = merged

            # part_of: adopt drop's parent only if keep has none (or already the same one) —
            # checked against keep_node.part_of post-self-loop-guard, not a stale pre-merge read.
            # A different existing parent skips adoption and records a warning instead of raising
            # (mirrors A1's spirit — explicit, never silent — without blocking the merge over it).
            warnings: list[str] = []
            if drop_node.part_of is not None and keep_node.part_of != drop_node.part_of:
                if keep_node.part_of is None:
                    if graphrules.would_create_cycle(self.con, keep_id, drop_node.part_of):
                        raise CycleError(keep_id, drop_node.part_of)
                    keep_updates["part_of"] = drop_node.part_of
                else:
                    warnings.append(
                        f"drop {drop_id!r} had part_of parent {drop_node.part_of!r}; keep "
                        f"{keep_id!r} already has a different parent {keep_node.part_of!r} — "
                        "not adopted"
                    )

            # keep's semantic content changed (edges migrated/adopted from drop), so — unlike
            # the pure inbound-relink referrers below — its updated timestamp bumps.
            keep_node = keep_node.model_copy(update={**keep_updates, "updated": _utc_now_str()})

            for node in other_referrers:
                text = dump_node_file(node)
                _atomic_write(self._node_path(node.id), text)
                index._upsert_node_index(self.con, node, content_hash=_content_hash(text))

            keep_text = dump_node_file(keep_node)
            _atomic_write(self._node_path(keep_id), keep_text)
            index._upsert_node_index(self.con, keep_node, content_hash=_content_hash(keep_text))

            os.remove(self._node_path(drop_id))
            index._remove_node_index(self.con, drop_id)
            if self._retrieval_config is not None:
                # keep's own (type, title, body) never changes in a merge -- only its edges/
                # part_of do (migrated/adopted above), and embed text is title+body only
                # (vector_index.embed_upsert), so keep's embed_hash cache would no-op a
                # re-embed anyway; skip the wasted cache-hit lookup and call embed_upsert for
                # neither keep nor the mechanically-relinked other_referrers. drop_id's vector
                # must still go: node_vectors has no FK cascade off nodes(id).
                embed_delete(self.con, drop_id)
            self.con.commit()

            return MergeOutcome(
                kept=load_node_file(self._node_path(keep_id)),
                warnings=warnings,
                dropped_body_preview=drop_node.body[:280],
            )

    def rename_node(self, id: str, new_title: str) -> Node:
        """A4 (Batch-B fix): the OLD ordering rewrote every OTHER referrer FIRST and only wrote
        the renamed node's own file at the very end -- so the redirect target (new_id's file)
        didn't exist until the last step. A crash mid-referrer-loop left an already-rewritten
        referrer pointing at an id that had never come into existence: permanently dangling,
        unlike merge_nodes (crash-safe because its redirect target, keep_id, pre-exists
        throughout). Fixed by making new_id's file exist FIRST, before any referrer is
        rewritten: (a) write it, (b) rewrite inbound referrers old -> new, (c) retire the old
        path last. A crash mid-(b) now leaves every referrer pointing at an id that EXISTS
        (either still-old, not yet reached, or already-new) -- zero dangling. A crash after
        (a)/before (c) leaves a transient duplicate (both old and new id resolve); a bare
        reindex heals the index, and retrying this exact call finishes the job. That retry
        needs the collision check below to recognize its own abandoned duplicate rather than
        reject it as "new_id already exists" -- content can coincidentally match an unrelated
        node (two freshly-created empty nodes are indistinguishable that way), so provenance is
        proven with a small marker file dropped alongside the transient duplicate instead (see
        `marker_path` below), not by comparing bytes.
        """
        with self._locked_transaction():
            self._require_exists(id)
            new_title = unicodedata.normalize("NFC", new_title)
            _check_title_length(new_title)
            new_id = sanitize_title_to_id(new_title)

            rows = self.con.execute("SELECT src, type FROM edges WHERE dst = ?", (id,)).fetchall()
            referrers: dict[str, list[EdgeType]] = {}
            for src, edge_type in rows:
                referrers.setdefault(src, []).append(EdgeType(edge_type))

            touched = self._rewrite_inbound_links(id, new_id, referrers) if new_id != id else []
            self_node = next((n for n in touched if n.id == id), None) or load_node_file(self._node_path(id))
            renamed = self_node.model_copy(update={"id": new_id, "title": new_title, "updated": _utc_now_str()})
            other_referrers = [n for n in touched if n.id != id]

            new_path = self._node_path(new_id)
            # Not a *.md file, so invisible to _find_existing_id's/reindex's globs -- exists
            # solely to let a retry's collision check below prove "the file already at new_id is
            # MY OWN abandoned duplicate from renaming id", the one fact plain byte-comparison
            # can't establish. Written before new_path's content (so it's already durable at the
            # instant new_path becomes visible) and retired together with the old path in (c).
            marker_path = new_path.with_name(new_path.name + ".rename-from")

            if new_id != id:
                existing = self._find_existing_id(new_id)
                if existing is not None and collision_key(existing) != collision_key(id):
                    resumable = existing == new_id and marker_path.exists() and marker_path.read_text() == id
                    if not resumable:
                        raise CollisionError(existing, self._node_path(existing))

            # (a) new_id's file first -- referrers below can now always resolve it, crash or not.
            text = dump_node_file(renamed)
            if new_id != id:
                _atomic_write(marker_path, id)
            _atomic_write(new_path, text)
            index._upsert_node_index(self.con, renamed, content_hash=_content_hash(text))

            # (b) every OTHER referrer, old -> new. A retry's referrers query above naturally
            # excludes any already repointed (its file/index already show new_id).
            for referrer in other_referrers:
                r_text = dump_node_file(referrer)
                _atomic_write(self._node_path(referrer.id), r_text)
                index._upsert_node_index(self.con, referrer, content_hash=_content_hash(r_text))

            if new_id != id:
                # (c) retire the old path last, only once every referrer durably points at
                # new_id. samefile guard: a pure case/Unicode-normalization-only rename on a
                # case-insensitive-but-preserving filesystem (macOS APFS, Windows NTFS) makes
                # old_path and new_path alias the SAME directory entry -- the write above already
                # landed there, so old_path no longer names a second, removable file.
                old_path = self._node_path(id)
                if old_path.exists() and not old_path.samefile(new_path):
                    os.remove(old_path)
                with suppress(FileNotFoundError):
                    os.remove(marker_path)
                index._remove_node_index(self.con, id)

            if self._retrieval_config is not None:
                # embed_upsert/embed_delete commit internally (S3a's own contract) -- placed
                # only after BOTH index._remove_node_index(id) and index._upsert_node_index
                # (renamed) above (never between them) so any embed-triggered early commit
                # captures old-removed and new-created atomically together, never a commit point
                # where neither id resolves.
                if new_id != id:
                    embed_delete(self.con, id)  # old id's vector row is now orphaned
                # Always re-checked, even when new_id == id: title can change (e.g. a pure
                # capitalization edit) without the sanitized id changing. embed_hash catches
                # the genuine no-op case (title AND body both unchanged) as a cache hit.
                embed_upsert(self.con, self._retrieval_config, renamed.id, str(renamed.type), renamed.title, renamed.body)

            self.con.commit()

            if new_id != id:
                # Precise, not "any dangling edge touching an affected id" (design §6.4 step 9's
                # literal wording) — that broader form false-positives on a referrer's OWN
                # unrelated pre-existing dangling edge (e.g. from an earlier delete_node) and
                # would also misfire when new_id == id, where edges still pointing at id are
                # simply valid, not stale. The actual invariant being defended is narrower:
                # nothing should still reference the id we just retired.
                stale = self.con.execute("SELECT 1 FROM edges WHERE dst = ?", (id,)).fetchone()
                assert stale is None, f"postcondition failed: an edge still targets retired id {id!r}"

            return renamed

    # -- reads / admin (design §8) -----------------------------------------------------------

    def get_node(self, id: str) -> Node:
        row = self.con.execute(
            "SELECT id, type, title, body, status, properties, created, updated FROM nodes WHERE id = ?",
            (id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(id)
        node_id, node_type, title, body, status, properties_json, created, updated = row
        return Node(
            id=node_id, type=NodeType(node_type), title=title, body=body, status=status,
            properties=json.loads(properties_json), created=created, updated=updated,
            **self._load_edges(node_id),
        )

    def reindex(self) -> index.ReindexStats:
        with self._write_lock():
            if self._retrieval_config is not None:
                ensure_embedding_schema(
                    self.con, self._retrieval_config.embedding_model_id, self._retrieval_config.embedding_dims
                )
            stats = index.reindex(self.con, self.paths.nodes_dir)
            if self._retrieval_config is not None:
                self._sync_embeddings()
            return stats

    def _sync_embeddings(self) -> None:
        """Embed every node currently in the index (embed_hash cache no-ops unchanged ones;
        a model/dims change already cleared node_vectors via ensure_embedding_schema above, so
        every node is a cache miss and this is where the resulting full re-embed actually
        happens) and delete every node_vectors row whose node_id no longer has a matching
        nodes row -- the stale-vector cleanup index.py's _remove_node_index docstring flags as
        still-owed (I2/I9): a node removed by the file-scan bulk-delete path in index.reindex()
        (a node file dropped straight onto disk, not via GraphStore.delete_node()) never went
        through this store's own embed_delete call, so it is caught here instead. Caller
        (reindex) already holds the write lock.
        """
        rows = self.con.execute("SELECT id, type, title, body FROM nodes").fetchall()
        live_ids = {node_id for node_id, _, _, _ in rows}
        for node_id, node_type, title, body in rows:
            embed_upsert(self.con, self._retrieval_config, node_id, node_type, title, body)
        vector_ids = {r[0] for r in self.con.execute("SELECT node_id FROM node_vectors").fetchall()}
        for stale_id in vector_ids - live_ids:
            embed_delete(self.con, stale_id)

    def snapshot(self, message: str, push: bool = False) -> gitops.CommitResult:
        with self._write_lock():
            return gitops.snapshot(self.paths.root, message, push=push)
