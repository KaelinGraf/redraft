"""Exception types raised across the redraft store (design §8)."""

from __future__ import annotations

from pathlib import Path


class CollisionError(Exception):
    """A computed id collides with an existing node (case-insensitive, NFC-normalized compare);
    also raised by create_edge when explicitly reparenting part_of onto a different existing
    parent without first calling delete_edge (design amendment A1) — existing_id/existing_path
    then name the current parent, not a colliding candidate id.
    """

    def __init__(self, existing_id: str, existing_path: Path, *, message: str | None = None) -> None:
        self.existing_id = existing_id
        self.existing_path = existing_path
        super().__init__(message or f"{existing_id!r} already exists at {existing_path}")


class CycleError(Exception):
    """A part_of edge would create a cycle in the parent-pointer forest."""

    def __init__(self, node_id: str, new_parent_id: str) -> None:
        self.node_id = node_id
        self.new_parent_id = new_parent_id
        super().__init__(f"part_of {node_id!r} -> {new_parent_id!r} would create a cycle")


class NotFoundError(Exception):
    """A referenced node id does not exist in the store."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"no such node: {node_id!r}")


class MalformedFrontmatterError(Exception):
    """A node file's frontmatter violates the schema (§1.1) or a wikilink is malformed (§3)."""


class LockTimeoutError(Exception):
    """Failed to acquire the write lock within the timeout."""


class GitOperationError(Exception):
    """A git subprocess call failed; carries git's own stderr text verbatim."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {' '.join(cmd)} failed ({returncode}): {stderr.strip()}")
