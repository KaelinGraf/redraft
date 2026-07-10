"""FTS5 lexical candidate retrieval: OR-joined, hyphen/dot/underscore-preserving tokenizer
+ bm25-ranked candidates.

Tokenization happens at two levels and they compose correctly (verified empirically
against this project's pinned FTS5 build): our TOKEN_RE keeps identifiers like `sm_120`
or `RTX-5090` as one regex token so they become one OR-branch (a search for "sm_120 near"
must not degrade into the much-looser "sm OR 120 OR near"); FTS5's own unicode61
tokenizer then tokenizes each of our phrase-quoted terms identically on both the query
and the indexed-document side, so an internally-punctuated identifier still matches as an
adjacent-token phrase regardless of unicode61's own word-splitting.

Requires nothing beyond stdlib FTS5 (compiled into this project's pinned SQLite build,
confirmed at design-storage.md verification time) — no sqlite-vec extension needed.
"""

from __future__ import annotations

import re
import sqlite3

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-.]+")


def tokenize_query(query: str) -> list[str]:
    return TOKEN_RE.findall(query)


def build_match_expr(query: str) -> str | None:
    """None (not '') for a query with no matchable tokens — the caller must skip issuing
    a MATCH at all rather than pass FTS5 an empty/whitespace query string."""
    tokens = tokenize_query(query)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def fts_candidates(conn: sqlite3.Connection, query: str, limit: int) -> list[str]:
    """Node ids ranked by bm25, best first. [] for an empty/unmatchable query or limit<=0
    — never issues a MATCH with an empty expression (FTS5 syntax-errors on that)."""
    if limit <= 0:
        return []
    match_expr = build_match_expr(query)
    if match_expr is None:
        return []
    rows = conn.execute(
        "SELECT n.id FROM nodes_fts f JOIN nodes n ON n.rowid = f.rowid "
        "WHERE nodes_fts MATCH ? ORDER BY rank LIMIT ?",
        (match_expr, limit),
    ).fetchall()
    return [r[0] for r in rows]
