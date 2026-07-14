"""Derived SQLite projector/indexer (design §5). Gitignored, rebuildable from graph/nodes/*.md."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from redraft.errors import MalformedFrontmatterError
from redraft.nodefile import load_node_file
from redraft.schema import EdgeType, Node

DDL = """
CREATE TABLE IF NOT EXISTS nodes (
  id           TEXT PRIMARY KEY,
  type         TEXT NOT NULL,
  title        TEXT NOT NULL,
  body         TEXT NOT NULL DEFAULT '',
  status       TEXT,
  properties   TEXT NOT NULL DEFAULT '{}',
  created      TEXT NOT NULL,
  updated      TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
  id   TEXT PRIMARY KEY,
  src  TEXT NOT NULL,
  dst  TEXT NOT NULL,
  type TEXT NOT NULL,
  UNIQUE(src, dst, type)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);
-- deliberately NO foreign key from edges.dst -> nodes.id: dangling-ness is computed at query
-- time (dangling_edges below), never dropped on write (design §5.1).

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(title, body, content='nodes', content_rowid='rowid');

CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
  INSERT INTO nodes_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, title, body) VALUES ('delete', old.rowid, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, title, body) VALUES ('delete', old.rowid, old.title, old.body);
  INSERT INTO nodes_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
"""
# I2: no node_vectors table here. design-storage.md section 5.1 originally defined a
# BLOB-column node_vectors table under this name; retrieval/vector_index.py's
# ensure_embedding_schema() owns the real one (INTEGER vec_rowid surrogate + a companion
# vec0 virtual table) -- the two schemas are incompatible under one name, and the
# retrieval-layer shape wins (S3a's pinned contract). Node deletion no longer needs to
# cascade a table this layer doesn't own; see _remove_node_index below.

_UPSERT_NODE_SQL = """
INSERT INTO nodes (id, type, title, body, status, properties, created, updated, content_hash)
VALUES (:id, :type, :title, :body, :status, :properties, :created, :updated, :content_hash)
ON CONFLICT(id) DO UPDATE SET
  type=excluded.type, title=excluded.title, body=excluded.body, status=excluded.status,
  properties=excluded.properties, created=excluded.created, updated=excluded.updated,
  content_hash=excluded.content_hash
"""
# ON CONFLICT DO UPDATE (a true SQL UPDATE), never INSERT OR REPLACE: the latter internally
# delete+reinserts on conflict, churning rowid, which nodes_fts's content_rowid ties to (§5.1).


@dataclass
class ReindexStats:
    scanned: int
    upserted: int
    deleted: int
    malformed: list[tuple[str, str]] = field(default_factory=list)


def edge_id(src: str, dst: str, type: str) -> str:
    return hashlib.sha256(f"{src}\x1f{dst}\x1f{type}".encode()).hexdigest()


def _check_environment(con: sqlite3.Connection) -> None:
    """Fail fast with a clear message rather than a confusing later error (design §5.3, Risk #7)."""
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        con.execute("DROP TABLE _fts5_probe")
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            "stdlib sqlite3 lacks FTS5 support; use a uv-managed Python "
            "(python-build-standalone), not a system/distro Python"
        ) from e
    if not hasattr(con, "enable_load_extension"):
        raise RuntimeError(
            "stdlib sqlite3 lacks enable_load_extension support (needed by sqlite-vec in a "
            "later phase); use a uv-managed Python, not a system/distro Python"
        )


def open_index(path: Path) -> sqlite3.Connection:
    """Open (create if missing) the derived index and ensure the schema exists (design §5.3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    _check_environment(con)
    con.executescript(DDL)
    con.execute("PRAGMA user_version = 1")
    con.commit()
    return con


def _upsert_node_index(con: sqlite3.Connection, node: Node, content_hash: str) -> None:
    """Upsert one node row and materialize its outgoing edges. Caller commits."""
    con.execute(
        _UPSERT_NODE_SQL,
        {
            "id": node.id,
            "type": str(node.type),
            "title": node.title,
            "body": node.body,
            "status": node.status,
            "properties": json.dumps(node.properties, sort_keys=True),
            "created": node.created,
            "updated": node.updated,
            "content_hash": content_hash,
        },
    )
    con.execute("DELETE FROM edges WHERE src = ?", (node.id,))
    for edge_type in EdgeType:
        for dst in node.edges_of(edge_type):
            con.execute(
                "INSERT OR IGNORE INTO edges (id, src, dst, type) VALUES (?, ?, ?, ?)",
                (edge_id(node.id, dst, edge_type.value), node.id, dst, edge_type.value),
            )


def _remove_node_index(con: sqlite3.Connection, node_id: str) -> None:
    """Delete a node's row (FTS trigger fires) and its own outgoing edges. Inbound edges
    (dst=node_id) are left in place — dangling by construction. Caller commits.

    RESOLVED (S3b): this function itself still never touches the retrieval layer's
    node_vectors/vec_nodes -- that schema has no FK back to nodes(id), and index.py has no
    dependency on retrieval/ by design. The cleanup now happens one layer up, in
    store.py: GraphStore.delete_node() calls retrieval.embed_delete(id) directly, right
    after calling this function, for the single-node-delete path; GraphStore.reindex()'s
    own _sync_embeddings() separately prunes any node_vectors row whose node_id has gone
    missing, which is what catches this function's OTHER caller -- the file-scan
    bulk-delete loop in reindex() below, for a node file removed straight from disk
    (never went through GraphStore.delete_node(), so no embed_delete call fired for it).
    """
    con.execute("DELETE FROM edges WHERE src = ?", (node_id,))
    con.execute("DELETE FROM nodes WHERE id = ?", (node_id,))


def dangling_edges(con: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    """(id, src, dst, type) for every edge whose target has no corresponding node row.
    A live LEFT JOIN — always correct, no flag to go stale (design §5.1)."""
    rows = con.execute(
        "SELECT e.id, e.src, e.dst, e.type FROM edges e LEFT JOIN nodes n ON e.dst = n.id WHERE n.id IS NULL"
    ).fetchall()
    return [tuple(row) for row in rows]


def reindex(con: sqlite3.Connection, nodes_dir: Path) -> ReindexStats:
    """Incremental reindex: hash-compare graph/nodes/*.md against the index, upsert changed
    nodes, delete removed ones. Read-only against the canonical store CONTENT (design §5.2,
    §5.3) -- with one deliberate, narrow exception: a non-NFC (e.g. NFD, as macOS Finder/native
    editors commonly write) on-disk FILENAME is renamed in place to its NFC form below, before
    being indexed. Every mutator (GraphStore._node_path) derives its path from the NFC id, so
    an NFD-named file would otherwise be visible here and via get_node/search forever, yet
    permanently unreachable for update/delete/rename/merge/create_edge(as src) -- raising
    FileNotFoundError every time. The rename touches only the FILENAME, never the file's
    content, so it doesn't violate the "derived, rebuildable from content" spirit this
    docstring otherwise promises.
    """
    on_disk: dict[str, tuple[Path, str]] = {}
    for path in sorted(nodes_dir.glob("*.md")):
        node_id = unicodedata.normalize("NFC", path.stem)
        if node_id != path.stem:
            target = path.with_name(f"{node_id}{path.suffix}")
            if not target.exists():  # don't clobber a genuine pre-existing NFC-named collision
                os.replace(path, target)
                path = target
        raw = path.read_bytes()
        on_disk[node_id] = (path, hashlib.sha256(raw).hexdigest())

    indexed_hash = dict(con.execute("SELECT id, content_hash FROM nodes"))

    to_upsert = [i for i, (_, h) in on_disk.items() if indexed_hash.get(i) != h]
    to_delete = [i for i in indexed_hash if i not in on_disk]

    malformed: list[tuple[str, str]] = []
    for node_id in to_upsert:
        path, content_hash = on_disk[node_id]
        try:
            node = load_node_file(path)
        except MalformedFrontmatterError as e:
            malformed.append((node_id, str(e)))
            continue  # fault isolation: one bad file doesn't abort the scan
        _upsert_node_index(con, node, content_hash=content_hash)

    for node_id in to_delete:
        _remove_node_index(con, node_id)

    con.commit()
    return ReindexStats(
        scanned=len(on_disk),
        upserted=len(to_upsert) - len(malformed),
        deleted=len(to_delete),
        malformed=malformed,
    )
