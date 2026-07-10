"""Integrity / hygiene queries — the six from design-server.md section 8, plus
case_collisions(), which design-storage.md's own Risks section 2 asked for as a safety
net ("Suggest adding a case_collisions() diagnostic alongside dangling_edges()") but
didn't specify: node ids identical under Unicode NFC+casefold — the diagnostic for a
human dropping e.g. both "Foo.md" and "foo.md" directly into graph/nodes/ on a
case-sensitive filesystem, bypassing GraphStore's own create-time collision check
(which only guards its own API, not direct filesystem writes reindex() then picks up).

Every function takes a plain sqlite3.Connection (no sqlite-vec extension needed — these
are all nodes/edges queries, no vector table touched) and returns plain dicts/lists.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from typing import Any

from ._util import get_node_dict, rows_to_dicts

DEFAULT_STALE_TYPES = ("question", "decision", "milestone")
DEFAULT_STALE_STATUSES = ("open", "proposed", "planned")


def decisions_without_rationale(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Decisions (any status, including superseded/rejected — the brief's zero-arg
    signature has no filter param) with no inbound `justifies` edge."""
    cur = conn.execute(
        """
        SELECT n.* FROM nodes n
        WHERE n.type = 'decision'
          AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.type = 'justifies' AND e.dst = n.id)
        ORDER BY n.updated DESC
        """
    )
    return rows_to_dicts(cur)


def open_questions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute("SELECT * FROM nodes WHERE type = 'question' AND status = 'open' ORDER BY updated DESC")
    return rows_to_dicts(cur)


def orphans(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Zero edges in any direction/type — a fully isolated fragment. Deliberately NOT
    "no part_of parent": a legitimate root-level concept has no parent by design and
    shouldn't be flagged for that alone."""
    cur = conn.execute(
        """
        SELECT n.* FROM nodes n
        WHERE NOT EXISTS (SELECT 1 FROM edges e WHERE e.src = n.id)
          AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.dst = n.id)
        ORDER BY n.created DESC
        """
    )
    return rows_to_dicts(cur)


def contradictions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """{a, b} pairs, enriched to full node dicts (not bare ids) for usability. A pair is
    skipped if either endpoint no longer exists (dangling `contradicts` edge — surfaced
    separately, and more completely, by dangling_edges())."""
    rows = conn.execute("SELECT e.src AS a_id, e.dst AS b_id FROM edges e WHERE e.type = 'contradicts'").fetchall()
    out = []
    for a_id, b_id in rows:
        a, b = get_node_dict(conn, a_id), get_node_dict(conn, b_id)
        if a is not None and b is not None:
            out.append({"a": a, "b": b})
    return out


def stale(
    conn: sqlite3.Connection,
    before_iso: str,
    types: list[str] | None = None,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Untouched-since-before_iso nodes, restricted by default to the four status-bearing
    types in their non-terminal states — flagging every stable, no-status `constraint`/
    `concept`, or every already-settled decision/question/milestone, would be noise, not
    a hygiene signal.

    types=None / statuses=None (omitted) means "use the default set" (design-server.md:
    "default (...) if caller omits"); an explicit types=[] / statuses=[] means "match no
    type / no status" and correctly yields an empty result (SQLite accepts `IN ()`,
    verified — it is 0 rows, not a syntax error) rather than being silently reinterpreted
    as "omitted" the way a plain `types or DEFAULT` truthiness check would (a bug: `[]` is
    falsy in Python, so `x if x else default` cannot tell "omitted" apart from "explicitly
    empty")."""
    types = list(types) if types is not None else list(DEFAULT_STALE_TYPES)
    statuses = list(statuses) if statuses is not None else list(DEFAULT_STALE_STATUSES)
    type_ph = ",".join("?" * len(types))
    status_ph = ",".join("?" * len(statuses))
    cur = conn.execute(
        f"SELECT * FROM nodes WHERE updated < ? AND type IN ({type_ph}) AND status IN ({status_ph}) "
        "ORDER BY updated ASC",
        (before_iso, *types, *statuses),
    )
    return rows_to_dicts(cur)


def dangling_edges(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Edges where src and/or dst no longer resolves to a node — checks BOTH ends (a src
    can also go dangling post-hoc via a direct filesystem delete + reindex, outside
    delete_node's own bookkeeping, not just dst as delete_node's own docstring might
    suggest)."""
    cur = conn.execute(
        """
        SELECT e.*,
          (NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.src)) AS src_dangling,
          (NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.dst)) AS dst_dangling
        FROM edges e
        WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.src)
           OR NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = e.dst)
        ORDER BY e.src, e.type
        """
    )
    rows = rows_to_dicts(cur)
    for r in rows:
        r["src_dangling"] = bool(r["src_dangling"])
        r["dst_dangling"] = bool(r["dst_dangling"])
    return rows


def _nfc_casefold(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def case_collisions(conn: sqlite3.Connection) -> list[list[str]]:
    """Groups of >=2 node ids identical under Unicode NFC+casefold — SQLite's built-in
    NOCASE collation only folds ASCII A-Z, so this needs a registered Python function,
    not a SQL collation, per design-storage.md section 2's own verification. Uses
    GROUP_CONCAT with a unit-separator (not the default comma) since titles/ids are not
    forbidden from containing a literal comma."""
    conn.create_function("nfc_casefold", 1, _nfc_casefold, deterministic=True)
    rows = conn.execute(
        "SELECT GROUP_CONCAT(id, char(31)) FROM nodes GROUP BY nfc_casefold(id) HAVING COUNT(*) > 1"
    ).fetchall()
    return [row[0].split(chr(31)) for row in rows]
