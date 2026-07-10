"""Git operations via subprocess, not GitPython (design §7.1): GitPython is maintenance-mode
and specifically documented as poorly suited to long-running processes (leaks subprocess/fd
resources via non-deterministic __del__). We need ~4 git calls total; plain subprocess is
fully synchronous and self-contained per call, a cleaner match to holding a single lock across
the operation than a stateful Repo object would be.

Individual CRUD operations never touch git — only snapshot() does (design §6, §7.2).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from redraft.errors import GitOperationError

# design §7.3 — only graph/nodes/**, source, and config are ever committed.
_GITIGNORE_LINES = (
    "/index/",
    "/.redraft.lock",
    "docs/",  # local re-fetchable reference-doc cache -- see organizing protocol §2
    "__pycache__/",
    "*.pyc",
    ".venv/",
)

# I6 (was A5): snapshot() stages only these top-level paths, never an unscoped `git add
# -A` — REDRAFT_DIR may be this very project's own repo root, and an unscoped add
# would sweep any other dirty file in the tree (e.g. a WIP source edit) into a graph
# snapshot commit. reports/ is a future S4 output dir that will not exist for most of this
# project's life, so the pathspec list must be built only from paths that currently exist:
# `git add` treats ANY non-matching pathspec as a fatal error for the WHOLE call (verified
# empirically -- it aborts before staging even the earlier, valid pathspecs in the list).
# docs/ is NOT here: it's a local, gitignored cache of re-fetchable reference material
# (organizing protocol §2), never committed and never part of a snapshot.
_SNAPSHOT_PATHSPECS = ("graph", "reports", ".gitignore")


def _existing_snapshot_pathspecs(graph_dir: Path) -> list[str]:
    return [p for p in _SNAPSHOT_PATHSPECS if (graph_dir / p).exists()]


@dataclass
class WorkingTreeStatus:
    dirty: bool
    changed_paths: list[str]


def working_tree_status(graph_dir: Path) -> WorkingTreeStatus:
    """git status --porcelain, scoped to the same pathspecs snapshot() stages (s6-ui.md §2.2)
    -- reuses _existing_snapshot_pathspecs so the two can never drift apart. Read-only; does
    not require the write lock (mirrors design-storage.md §6.2's "reads never acquire the
    lock"). Safe to call before a git repo even exists yet (snapshot() hasn't run): with
    check=False, `git status` against a non-repo dir exits 128 with an empty stdout (verified
    empirically -- the "fatal: not a git repository" text goes to stderr), so `lines` below is
    simply [] and this reports the same dirty=False a genuinely clean repo would.
    """
    pathspecs = _existing_snapshot_pathspecs(graph_dir)
    if not pathspecs:
        return WorkingTreeStatus(dirty=False, changed_paths=[])
    result = _run(graph_dir, ["status", "--porcelain", "--", *pathspecs], check=False)
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    return WorkingTreeStatus(dirty=bool(lines), changed_paths=[l[3:] for l in lines])


@dataclass
class CommitResult:
    committed: bool
    sha: str | None
    initialized_repo: bool
    pushed: bool = False
    message: str | None = None


def _run(
    graph_dir: Path, args: list[str], *, check: bool = True, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args], cwd=graph_dir, check=check, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.CalledProcessError as e:
        raise GitOperationError(args, e.returncode, e.stderr) from e
    except subprocess.TimeoutExpired as e:
        raise GitOperationError(args, -1, f"timed out after {timeout}s") from e


def _ensure_repo(graph_dir: Path) -> bool:
    """git init iff .git is missing; idempotent. Returns True iff init just ran."""
    probe = _run(graph_dir, ["rev-parse", "--is-inside-work-tree"], check=False)
    if probe.returncode == 0:
        return False
    _run(graph_dir, ["init"])
    return True


def _ensure_gitignore(graph_dir: Path) -> None:
    """Idempotent; runs before every add -A, not just on init — defends against a human
    accidentally deleting .gitignore. Appends only the lines that are missing, so a human's
    own additions to .gitignore are never clobbered.
    """
    path = graph_dir / ".gitignore"
    existing = path.read_text().splitlines() if path.exists() else []
    missing = [line for line in _GITIGNORE_LINES if line not in existing]
    if not missing:
        return
    with path.open("a") as f:
        if existing and existing[-1] != "":
            f.write("\n")
        f.write("\n".join(missing) + "\n")


def snapshot(graph_dir: Path, message: str, push: bool = False) -> CommitResult:
    initialized = _ensure_repo(graph_dir)
    _ensure_gitignore(graph_dir)
    _run(graph_dir, ["add", "--", *_existing_snapshot_pathspecs(graph_dir)])
    staged = _run(graph_dir, ["diff", "--cached", "--quiet"], check=False)  # exit 1 = there ARE staged changes
    if staged.returncode == 0:
        return CommitResult(committed=False, sha=None, initialized_repo=initialized, message="nothing to commit")
    _run(graph_dir, ["commit", "-m", message])
    sha = _run(graph_dir, ["rev-parse", "HEAD"]).stdout.strip()
    pushed = False
    if push:
        _run(graph_dir, ["push"], timeout=60)  # explicit timeout: don't hold the write lock hostage to a hung push
        pushed = True
    return CommitResult(committed=True, sha=sha, pushed=pushed, initialized_repo=initialized)
