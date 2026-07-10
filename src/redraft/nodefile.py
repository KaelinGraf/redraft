"""Round-trip (de)serialization of node files (design §1, §4) and atomic file writes (§6.1)."""

from __future__ import annotations

import contextlib
import datetime
import os
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import frontmatter

from redraft.errors import MalformedFrontmatterError
from redraft.ids import format_wikilink, parse_wikilink
from redraft.schema import LIST_EDGE_TYPES, Node, NodeType, status_error

_REQUIRED_KEYS = ("type", "title", "created", "updated")
_KNOWN_KEYS = frozenset(
    {"type", "title", "status", "created", "updated", "properties", "part_of"}
    | {e.value for e in LIST_EDGE_TYPES}
)


def _coerce_timestamp(value: object, path: Path, field: str) -> str:
    """Canonicalize created/updated: pass strings through; coerce a YAML-inferred
    datetime/date (the unquoted-timestamp footgun, design §4.3) back to our format.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, datetime.datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day, tzinfo=datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    raise MalformedFrontmatterError(f"{path}: {field} must be a string, got {type(value).__name__}")


def load_node_file(path: Path) -> Node:
    """Parse + validate + normalize a node file. Read-only; never writes to `path`."""
    post = frontmatter.load(path)
    metadata = post.metadata

    missing = [k for k in _REQUIRED_KEYS if k not in metadata]
    if missing:
        raise MalformedFrontmatterError(f"{path}: missing required key(s): {', '.join(missing)}")

    raw_type = metadata["type"]
    try:
        node_type = NodeType(raw_type)
    except ValueError:
        raise MalformedFrontmatterError(f"{path}: unknown type {raw_type!r}") from None

    title = metadata["title"]
    if not isinstance(title, str):
        raise MalformedFrontmatterError(f"{path}: title must be a string, got {type(title).__name__}")
    title = unicodedata.normalize("NFC", title)

    created = _coerce_timestamp(metadata["created"], path, "created")
    updated = _coerce_timestamp(metadata["updated"], path, "updated")

    err = status_error(node_type, metadata.get("status"), present="status" in metadata)
    if err:
        raise MalformedFrontmatterError(f"{path}: {err}")
    status = metadata.get("status")

    properties = metadata.get("properties", {})
    if not isinstance(properties, dict):
        raise MalformedFrontmatterError(f"{path}: properties must be a mapping")

    part_of: str | None = None
    if "part_of" in metadata:
        raw_part_of = metadata["part_of"]
        if not isinstance(raw_part_of, str):
            raise MalformedFrontmatterError(
                f"{path}: part_of must be a single wikilink string, not {type(raw_part_of).__name__}"
            )
        part_of = parse_wikilink(raw_part_of)

    edges: dict[str, list[str]] = {}
    for edge_type in LIST_EDGE_TYPES:
        key = edge_type.value
        if key not in metadata:
            edges[key] = []
            continue
        raw = metadata[key]
        if isinstance(raw, str):
            items = [raw]  # tolerant read: a bare scalar coerces to a 1-element list (design §1.1)
        elif isinstance(raw, list):
            items = raw
        else:
            raise MalformedFrontmatterError(f"{path}: {key} must be a string or list of strings")
        parsed: list[str] = []
        for item in items:
            if not isinstance(item, str):
                raise MalformedFrontmatterError(f"{path}: {key} entries must be wikilink strings")
            parsed.append(parse_wikilink(item))
        edges[key] = parsed

    extra = {k: v for k, v in metadata.items() if k not in _KNOWN_KEYS}
    node_id = unicodedata.normalize("NFC", path.stem)  # defensive: a foreign tool may write non-NFC

    return Node(
        id=node_id,
        type=node_type,
        title=title,
        body=post.content,
        status=status,
        properties=properties,
        part_of=part_of,
        created=created,
        updated=updated,
        extra=extra,
        **edges,
    )


def dump_node_file(node: Node) -> str:
    """Serialize a Node to canonical node-file text (fixed key order, §1.1)."""
    metadata: dict[str, Any] = {"type": str(node.type), "title": node.title}
    if node.status is not None:
        metadata["status"] = node.status
    metadata["created"] = node.created
    metadata["updated"] = node.updated
    if node.properties:
        metadata["properties"] = node.properties
    if node.part_of is not None:
        metadata["part_of"] = format_wikilink(node.part_of)
    for edge_type in LIST_EDGE_TYPES:
        values = getattr(node, edge_type.value)
        if values:
            metadata[edge_type.value] = [format_wikilink(v) for v in values]
    for key in sorted(node.extra):
        metadata[key] = node.extra[key]

    post = frontmatter.Post(node.body, **metadata)
    # sort_keys=False preserves our canonical insertion order; width=1_000_000 disables
    # PyYAML's 80-col hard-wrap (both required, verified in design §4.1 — neither is a
    # frontmatter.dumps default). dumps() never guarantees a trailing newline (it .strip()s
    # the whole template), so append one explicitly for a clean POSIX text file.
    return frontmatter.dumps(post, sort_keys=False, width=1_000_000) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX (rename(2)) and on Windows (MoveFileExW+REPLACE_EXISTING)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
