"""SQLite DDL for building fixture databases, per the S3a task brief: "Your tests build
fixture SQLite DBs directly from the DDL in design-storage section 5.1 (copy it verbatim
into tests/fixtures_ddl.py with a comment naming the source) and insert synthetic nodes."

Source: design-storage.md ("Project Memory Graph — Canonical Store + Projector/Indexer
Design"), section "5.1 SQLite schema (adapted from brief §3.5)", scratchpad path
/tmp/claude-1000/-home-kaelin-PRO-ject/3d667d53-3dc5-433a-9291-cfe7fe19d329/scratchpad/design-storage.md,
as pinned by the orchestrator for S1's storage-layer contract. STORAGE_DDL_VERBATIM below
is that block byte-for-byte (raw string, so the `\\x1f` in its SQL comments stays four
literal characters rather than being escape-processed into a control character).

DESIGN DEFECT, RESOLVED AT INTEGRATION (I2) -- receipts below kept for history:
design-storage.md section 5.1 defined its own `node_vectors` table:

    CREATE TABLE node_vectors (
      node_id   TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
      embedding BLOB NOT NULL,
      model     TEXT NOT NULL
    );

design-server.md section 6 (this slice's pinned contract, reconciliation R6) defines an
INCOMPATIBLE table under the *same name*:

    CREATE TABLE IF NOT EXISTS node_vectors (
      node_id     TEXT PRIMARY KEY,
      vec_rowid   INTEGER NOT NULL UNIQUE,
      embed_hash  TEXT NOT NULL,
      embedded_at TEXT NOT NULL
    );

These cannot coexist under one name in one database — whichever runs `CREATE TABLE IF NOT
EXISTS node_vectors` second silently no-ops against the other's columns, and the first
INSERT/SELECT referencing the missing columns raises `sqlite3.OperationalError: no such
column`. This slice's task brief explicitly assigns it the design-server.md shape
("vector_index.py: embedding_meta + node_vectors + vec0 table ... INTEGER surrogate
vec_rowid mapping"), so `build_fixture_db()` executes STORAGE_DDL_VERBATIM in full (true
to "verbatim") and then drops storage's `node_vectors` immediately after — the retrieval
layer's own `vector_index.ensure_embedding_schema()` creates the real one.

RESOLUTION (I2): src/redraft/index.py's real DDL no longer defines a `node_vectors`
table at all — the collision is resolved at the source, retrieval's shape wins outright.
STORAGE_DDL_VERBATIM above is intentionally left as a frozen, byte-for-byte copy of the
*original* design-storage.md text (including its now-superseded node_vectors block) rather
than re-synced to index.py's current DDL, so the `DROP TABLE node_vectors` line below
remains load-bearing for this fixture (it still creates and immediately drops storage's old
table) even though the collision it originally guarded against can no longer happen against
the real index.py. Harmless either way; kept verbatim-faithful to what it documents.
"""

from __future__ import annotations

import sqlite3

STORAGE_DDL_VERBATIM = r"""
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;                 -- cheap built-in schema-version marker for future migrations

CREATE TABLE nodes (
  id           TEXT PRIMARY KEY,          -- filename stem (was ULID in the original brief)
  type         TEXT NOT NULL,
  title        TEXT NOT NULL,
  body         TEXT NOT NULL DEFAULT '',
  status       TEXT,
  properties   TEXT NOT NULL DEFAULT '{}', -- JSON, json.dumps(..., sort_keys=True)
  created      TEXT NOT NULL,              -- renamed from created_at per locked decisions
  updated      TEXT NOT NULL,              -- renamed from updated_at
  content_hash TEXT NOT NULL               -- sha256 of raw file bytes
);

CREATE TABLE edges (
  id   TEXT PRIMARY KEY,                  -- sha256(f"{src}\x1f{dst}\x1f{type}")
  src  TEXT NOT NULL,
  dst  TEXT NOT NULL,
  type TEXT NOT NULL,
  UNIQUE(src, dst, type)
);
CREATE INDEX idx_edges_src ON edges(src);
CREATE INDEX idx_edges_dst ON edges(dst);
-- deliberately NO foreign key from edges.dst -> nodes.id: an FK would make it
-- impossible to ever INSERT a dangling edge, which is the opposite of "flag,
-- don't drop" (§3.6 of the brief). Dangling-ness is computed at query time.

CREATE VIRTUAL TABLE nodes_fts USING fts5(title, body, content='nodes', content_rowid='rowid');
CREATE TRIGGER nodes_ai AFTER INSERT ON nodes BEGIN
  INSERT INTO nodes_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
CREATE TRIGGER nodes_ad AFTER DELETE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, title, body) VALUES ('delete', old.rowid, old.title, old.body);
END;
CREATE TRIGGER nodes_au AFTER UPDATE ON nodes BEGIN
  INSERT INTO nodes_fts(nodes_fts, rowid, title, body) VALUES ('delete', old.rowid, old.title, old.body);
  INSERT INTO nodes_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;

CREATE TABLE node_vectors (
  node_id   TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
  embedding BLOB NOT NULL,
  model     TEXT NOT NULL
);
"""


def build_fixture_db(conn: sqlite3.Connection) -> None:
    """Execute STORAGE_DDL_VERBATIM against conn (true to "verbatim"), then drop
    storage's `node_vectors` — see the DESIGN DEFECT note in this module's docstring.
    Callers still need to load the sqlite-vec extension and call
    redraft.retrieval.vector_index.ensure_embedding_schema() before touching
    anything vector-related; this function alone gives you the storage-owned
    nodes/edges/nodes_fts surface only.
    """
    conn.executescript(STORAGE_DDL_VERBATIM)
    conn.execute("DROP TABLE node_vectors")
    conn.commit()
