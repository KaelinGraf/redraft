"""redraft init: birth a new graph repo anywhere (the `redraft init` subcommand).

This is the "clone the engine once, birth graphs anywhere" primitive that makes the
engine/graph split (README.md) practical: the engine repo is installed once via `uv tool
install`, then this command stamps out a graph repo -- graph/nodes/, CLAUDE.md, .mcp.json,
a SessionStart hook, and (by default) its own independent git history -- at any target
directory, on any device.

Writes exactly five things, never more:
  - <target>/graph/nodes/        -- empty; GraphStore creates node files here.
  - <target>/CLAUDE.md           -- the organizing protocol, verbatim from the same packaged
    source prompts.py uses for the MCP `organizing_protocol` prompt (ORGANIZING_PROTOCOL_TEXT)
    -- one source of truth, never a second copy (that text already documents itself as
    shippable directly as a project's CLAUDE.md).
  - <target>/.mcp.json           -- points REDRAFT_DIR at "${CLAUDE_PROJECT_DIR:-.}", never
    an absolute path baked in at write time, so the file is location-independent: it still
    works after the graph repo is cloned onto another device at a different path. Claude
    Code expands the placeholder to the session's project root; "." is the fallback for any
    other client that expands ${VAR} but doesn't set CLAUDE_PROJECT_DIR, provided it spawns
    the server with cwd = project root. Runs the server via the `redraft` PATH binary's
    `serve` subcommand -- never this engine repo's own path, so the graph repo is portable
    to a device that only has the engine installed as a tool, not checked out as a source
    tree.
  - <target>/.claude/settings.json -- a SessionStart hook that runs `redraft overview` and
    injects its markdown straight into every new session's context (see the JSON schema
    confirmed against Claude Code's own hooks reference, docstring on _claude_settings()
    below). Unconditional, independent of `git` -- unlike .gitignore this isn't git-related,
    so it's written even with --no-git. README.md's "Session-start overview" section
    documents what it runs and how to opt out (just delete the file, or the one hook entry).
  - <target>/.gitignore          -- only when git=True (inert without a git repo); via the
    same gitops._ensure_gitignore() every snapshot() runs, so the two can never drift and a
    pre-existing .gitignore is appended to, never overwritten.

Refuses (before writing anything) if the target already holds a populated graph/nodes/, a
CLAUDE.md, a .mcp.json, or a .claude/settings.json -- init births new graph repos; it never
overwrites a directory's existing files.

This module also hosts `sync_graph` (the `redraft sync` subcommand). Engine and graph are
separate repos, upgraded independently (README.md "Engine and graph are separate repos"):
three of the five things above -- CLAUDE.md, .claude/settings.json, and .gitignore -- are
written once at init time and, unlike the engine itself, never refreshed, so an engine
upgrade (`uv tool install --reinstall`: a newer protocol, a hook that didn't exist yet when
an old graph was born) never reaches a graph that already exists. `sync_graph` closes that
gap for an EXISTING graph only -- it never creates or scaffolds one (that stays init_graph's
job alone) and never touches graph/nodes/, docs/, or reports/. (.mcp.json is the remaining
one of the five; sync only creates it if entirely absent, never overwrites it -- see
sync_graph's own docstring for why.)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from redraft.config import GraphPaths, resolve_graph_dir
from redraft.errors import GitOperationError
from redraft.gitops import _ensure_gitignore, _run as _git_run
from redraft.prompts import ORGANIZING_PROTOCOL_TEXT

_MCP_SERVER_COMMAND = "redraft"
_MCP_SERVER_ARGS = ["serve"]


class GraphAlreadyExistsError(Exception):
    """target_dir already holds a non-empty graph/nodes/, a CLAUDE.md, a .mcp.json, or a
    .claude/settings.json; init refuses to touch it."""


class NotAGraphError(Exception):
    """target_dir is not an existing redraft graph -- graph/nodes/ is missing. Unlike
    GraphAlreadyExistsError (init refusing to clobber something), this is sync refusing to
    scaffold something: sync_graph only refreshes a graph that already exists."""


@dataclass
class InitResult:
    target_dir: Path
    git_initialized: bool
    committed: bool


@dataclass
class SyncResult:
    graph_dir: Path
    changed: list[str]  # relative paths of the managed files actually rewritten, if any
    committed: bool


def _mcp_config() -> dict:
    return {
        "mcpServers": {
            "redraft": {
                "command": _MCP_SERVER_COMMAND,
                "args": _MCP_SERVER_ARGS,
                "env": {"REDRAFT_DIR": "${CLAUDE_PROJECT_DIR:-.}", "PYTHONPATH": ""},
            }
        }
    }


def _claude_settings() -> dict:
    """A SessionStart hook that runs `redraft overview` and injects its plain-stdout markdown
    into every new session's context (Claude Code hooks reference, "SessionStart" section,
    https://code.claude.com/docs/en/hooks, fetched and confirmed live for this slice):
    settings.json's `hooks.SessionStart` is a list of {matcher, hooks} blocks, each `hooks`
    entry a {type: "command", command, timeout} object; a command hook's plain stdout (no
    JSON envelope needed) is added verbatim as context; `matcher` is omitted here so the hook
    fires on every session-start reason (startup/resume/clear/compact alike -- the docs:
    "Omitting the matcher or using '*' runs the hook on every session start reason").

    `CLAUDE_PROJECT_DIR` is documented as available to SessionStart hooks specifically and is
    passed explicitly as `redraft overview`'s graph_dir argument -- never relied on implicitly
    via cwd or the REDRAFT_DIR env var, neither of which a hook subprocess is guaranteed to
    have set the way the MCP server's own env block (_mcp_config() above) does. `PYTHONPATH=`
    prefixes the command for the same reason .mcp.json's own env block clears it (README.md's
    "Registering redraft in an existing project"): a shell where something else has already
    polluted PYTHONPATH (a ROS workspace is this project's own documented repeat offender)
    would otherwise shadow redraft's dependencies on every single session start. `timeout: 60`
    sits comfortably above GraphStore's own write-lock timeout (locking.py's
    LOCK_TIMEOUT_SECONDS = 30.0), so a concurrent writer produces redraft.overview's own clean
    LockTimeoutError message instead of Claude Code externally killing the process mid-wait.
    """
    return {
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": 'PYTHONPATH= redraft overview "${CLAUDE_PROJECT_DIR}"',
                            "timeout": 60,
                        }
                    ]
                }
            ]
        }
    }


def init_graph(target_dir: Path, *, git: bool = True, project_name: str | None = None) -> InitResult:
    """Birth a graph repo at target_dir (created if missing). Raises GraphAlreadyExistsError
    if target_dir/graph/nodes already exists and is non-empty, or if target_dir already has a
    CLAUDE.md, .mcp.json, or .claude/settings.json (init would clobber them); raises
    NotADirectoryError if target_dir exists but is a file. All are checked before anything is
    written.
    """
    target_dir = Path(target_dir).resolve()
    if target_dir.exists() and not target_dir.is_dir():
        raise NotADirectoryError(f"{target_dir} exists and is not a directory")
    nodes_dir = GraphPaths(target_dir).nodes_dir
    if nodes_dir.exists() and any(nodes_dir.iterdir()):
        raise GraphAlreadyExistsError(
            f"{nodes_dir} already exists and is not empty -- refusing to overwrite an "
            "existing graph. Point redraft init at an empty or new directory."
        )
    clobber = [
        name for name in ("CLAUDE.md", ".mcp.json", ".claude/settings.json") if (target_dir / name).exists()
    ]
    if clobber:
        raise GraphAlreadyExistsError(
            f"{' and '.join(clobber)} already exist(s) in {target_dir} -- refusing to "
            "overwrite. Point redraft init at an empty or new directory."
        )
    nodes_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "CLAUDE.md").write_text(ORGANIZING_PROTOCOL_TEXT, encoding="utf-8")
    (target_dir / ".mcp.json").write_text(json.dumps(_mcp_config(), indent=2) + "\n", encoding="utf-8")
    claude_dir = target_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_text(json.dumps(_claude_settings(), indent=2) + "\n", encoding="utf-8")

    committed = False
    if git:
        _ensure_gitignore(target_dir)
        name = project_name or target_dir.name
        _git_run(target_dir, ["init", "-b", "main"])
        _git_run(target_dir, ["add", "--", "graph", "CLAUDE.md", ".mcp.json", ".claude", ".gitignore"])
        _git_run(target_dir, ["commit", "-m", f"Initialize {name} redraft graph"])
        committed = True

    return InitResult(target_dir=target_dir, git_initialized=git, committed=committed)


def sync_graph(graph_dir: Path, *, git: bool = True) -> SyncResult:
    """Refresh an EXISTING graph's engine-managed files to match what the currently
    installed engine ships (module docstring above). Non-destructive and idempotent:

      - CLAUDE.md and .claude/settings.json are each rewritten only if their on-disk content
        differs from ORGANIZING_PROTOCOL_TEXT / _claude_settings() -- the exact same source of
        truth init_graph() writes from, reused verbatim, never a second copy. A graph that's
        already current is left byte-for-byte untouched.
      - .mcp.json is written only if entirely absent. Never overwritten if present: a user may
        have hand-customized its REDRAFT_DIR/command/env (module docstring's five-things list
        already documents .mcp.json as location-independent by construction, so there is
        nothing engine-version-specific in it worth force-refreshing -- only its *presence*
        matters here).
      - .gitignore is refreshed via the same _ensure_gitignore() every init/snapshot runs
        (append-only; never touches a human's own lines) -- but only in an actual git work
        tree, mirroring init_graph's own split above (.gitignore is "inert without a git
        repo" there too): a --no-git graph never had one and sync must not hand it a surprise
        new file just because it happens to be git-related housekeeping.
      - graph/nodes/, docs/, and reports/ are never written by this function.

    Raises NotAGraphError if graph_dir has no graph/nodes/ -- sync refuses to scaffold a new
    graph; that is init_graph's job alone.

    Commits iff git=True, the directory is an existing git work tree (checked, never
    created -- a --no-git graph stays --no-git; this is the one place sync deliberately does
    NOT mirror init_graph's own git=True default behavior of running `git init`), and staging
    the changed paths actually produces a diff against HEAD. That last check matters: a file
    can be in `changed` (its on-disk bytes differed from the target right before this call)
    and still be a no-op for git -- e.g. a hand-edited CLAUDE.md that gets restored to
    exactly the same protocol text already committed at `redraft init` time round-trips back
    to HEAD's own content. `git commit` treats that as "nothing to commit" and exits 1, so
    the gate is the same add-then-`git diff --cached --quiet` check gitops.snapshot() already
    uses, reused here rather than re-derived, instead of committing unconditionally whenever
    `changed` is non-empty. Never creates an empty commit.
    """
    graph_dir = Path(graph_dir).resolve()
    if not GraphPaths(graph_dir).nodes_dir.exists():
        raise NotAGraphError(
            f"{graph_dir} is not a redraft graph (no graph/nodes/); run 'redraft init' to create one"
        )

    is_git_tree = _git_run(graph_dir, ["rev-parse", "--is-inside-work-tree"], check=False).returncode == 0
    changed: list[str] = []

    claude_md = graph_dir / "CLAUDE.md"
    if not claude_md.exists() or claude_md.read_text(encoding="utf-8") != ORGANIZING_PROTOCOL_TEXT:
        claude_md.write_text(ORGANIZING_PROTOCOL_TEXT, encoding="utf-8")
        changed.append("CLAUDE.md")

    settings_path = graph_dir / ".claude" / "settings.json"
    settings_text = json.dumps(_claude_settings(), indent=2) + "\n"
    if not settings_path.exists() or settings_path.read_text(encoding="utf-8") != settings_text:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(settings_text, encoding="utf-8")
        changed.append(".claude/settings.json")

    mcp_path = graph_dir / ".mcp.json"
    if not mcp_path.exists():
        mcp_path.write_text(json.dumps(_mcp_config(), indent=2) + "\n", encoding="utf-8")
        changed.append(".mcp.json")

    if is_git_tree:
        gitignore_path = graph_dir / ".gitignore"
        before = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else None
        _ensure_gitignore(graph_dir)
        after = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else None
        if after != before:
            changed.append(".gitignore")

    committed = False
    if git and is_git_tree and changed:
        _git_run(graph_dir, ["add", "--", *changed])
        staged = _git_run(graph_dir, ["diff", "--cached", "--quiet"], check=False)  # exit 1 = staged changes
        if staged.returncode != 0:
            _git_run(graph_dir, ["commit", "-m", "redraft sync: refresh engine-managed files"])
            committed = True

    return SyncResult(graph_dir=graph_dir, changed=changed, committed=committed)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="redraft init",
        description="Birth a new redraft repo: graph/nodes/, CLAUDE.md, "
        ".mcp.json, a SessionStart hook, and (by default) its own git history.",
    )
    parser.add_argument("target_dir", type=Path, help="directory to initialize (created if missing)")
    parser.add_argument("--no-git", action="store_true", help="skip git init and the initial commit")
    parser.add_argument(
        "--project-name", default=None, help="name used in the initial commit message (default: the directory name)"
    )
    args = parser.parse_args(argv)

    try:
        result = init_graph(args.target_dir, git=not args.no_git, project_name=args.project_name)
    except (GraphAlreadyExistsError, NotADirectoryError, GitOperationError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Initialized redraft graph at {result.target_dir}")
    if result.committed:
        print("git repo initialized on branch 'main' with the initial commit.")
    print()
    print("Next steps:")
    print(f"  cd {result.target_dir}")
    print("  Start a Claude Code session here -- the redraft MCP tools appear via .mcp.json,")
    print("  and a SessionStart hook (.claude/settings.json) prints the project overview into")
    print("  context automatically. See README.md's \"Session-start overview\" to opt out.")


def sync_main(graph_dir: str | Path | None = None, *, no_commit: bool = False) -> None:
    """redraft.cli's `sync` subcommand hands its already-parsed graph_dir/no_commit straight
    here -- no argv/argparse in this module, matching redraft.overview.main's plain-kwargs
    shape (that module's docstring) rather than this file's own main()'s REMAINDER-delegated
    argparse (cli.py parses `sync`'s two options itself, like it does for `overview`)."""
    try:
        resolved = resolve_graph_dir(graph_dir)
        result = sync_graph(resolved, git=not no_commit)
    except (RuntimeError, NotAGraphError, GitOperationError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if result.changed:
        print(f"{result.graph_dir}: refreshed {', '.join(result.changed)}")
    else:
        print(f"{result.graph_dir}: already up to date")
    print("Committed." if result.committed else "Not committed.")


if __name__ == "__main__":  # pragma: no cover
    main()
