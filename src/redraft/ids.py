"""Title-to-filename sanitization and wikilink parsing (design §2, §3)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

from redraft.errors import MalformedFrontmatterError

ILLEGAL = '<>:"/\\|?*'  # Windows-reserved superset; covers macOS's ':' and Linux's '/'
CONTROL = {chr(c) for c in range(0x20)}

WIKILINK_RE = re.compile(r"^\[\[([^\[\]|#]+?)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]$")


def _sanitize_component(s: str, *, budget_bytes: int) -> str:
    """Shared core of sanitize_title_to_id and sanitize_attachment_filename (s6-ui.md §2.2):
    NFC-normalize, replace Windows-illegal/control characters with spaces, collapse
    whitespace, strip a trailing dot/space (Windows path-component rule), UTF-8-safe-truncate
    to budget_bytes -- re-stripping trailing dot/space *after* truncation too, since a cut can
    newly expose a trailing run that sat mid-string before the cut (DESIGN DEFECT FIX,
    design-storage.md §2 lines 74-78: a title like "." * 500 + "A" truncates to an all-dot
    prefix). Returns "" if nothing survives -- callers decide whether that's an error or a
    fallback.
    """
    t = unicodedata.normalize("NFC", s).strip()
    t = "".join(" " if ch in ILLEGAL or ch in CONTROL else ch for ch in t)
    t = re.sub(r"\s+", " ", t).strip().rstrip(". ")
    b = t.encode("utf-8")
    if len(b) > budget_bytes:
        t = b[:budget_bytes].decode("utf-8", errors="ignore").rstrip(". ")
    return t


def sanitize_title_to_id(title: str) -> str:
    # byte-length guard: cap comfortably under the 255-byte filesystem component limit,
    # UTF-8-safe truncation (titles may be multibyte-heavy, e.g. CJK/emoji)
    t = _sanitize_component(title, budget_bytes=200 - len(".md".encode()))
    if not t:
        raise ValueError("title contains no filesystem-safe characters")
    return t


def sanitize_attachment_filename(original_name: str) -> str:
    """graph/attachments/<result> (s6-ui.md §6). Strips any directory components a client
    might send (PurePosixPath(...).name defends against a crafted '../../etc/passwd'-shaped
    filename header), sanitizes stem and suffix independently through the same
    _sanitize_component core sanitize_title_to_id uses, and falls back to a generic stem
    rather than raising -- an upload's filename is often machine-generated (a phone camera's
    DCIM name, a browser-decoded multipart header), unlike a human-typed node title, so a hard
    reject here would be poor UX for a common, legitimate case.
    """
    name = PurePosixPath(original_name).name
    stem, _, suffix = name.rpartition(".")
    if not stem:  # no "." at all ("noext"), or a dotfile-shaped name (".hidden")
        stem, suffix = name, ""
    stem = _sanitize_component(stem, budget_bytes=150) or "attachment"
    suffix = _sanitize_component(suffix, budget_bytes=20)
    return f"{stem}.{suffix}" if suffix else stem


def collision_key(id: str) -> str:
    """Case-insensitive, NFC-normalized comparison key for id collisions and wikilink resolution."""
    return unicodedata.normalize("NFC", id).casefold()


def parse_wikilink(raw: str) -> str:
    m = WIKILINK_RE.match(raw)
    if not m:
        raise MalformedFrontmatterError(f"not a well-formed wikilink: {raw!r}")
    return unicodedata.normalize("NFC", m.group(1).strip())


def format_wikilink(id: str) -> str:
    return f"[[{id}]]"
