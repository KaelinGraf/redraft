"""redraft: consolidated CLI. `redraft serve` runs the MCP server; `redraft init` births a
new graph repo; `redraft ui` runs the operator web app; `redraft overview` prints a compact
markdown map of the project's shape; `redraft sync` refreshes an existing graph's
engine-managed files (CLAUDE.md, the SessionStart hook, .gitignore) from the currently
installed engine. A thin argparse dispatcher only -- each subcommand's real argument handling
stays in its own module (redraft.server.main / redraft.init.main / redraft.ui.app.main /
redraft.overview.main / redraft.init.sync_main), reused as-is, so this file never redefines
target_dir/--no-git/--project-name or the server's env-based config. `ui`, `overview`, and
`sync` are the exceptions to "stays in its own module": like s6-ui.md's contract for `ui`
(--host/--port/--graph-dir/--reindex-poll-interval), this file itself parses `overview`'s one
option (an optional graph_dir positional) and `sync`'s two options (the same optional
graph_dir positional, plus --no-commit) and hands off the already-parsed values, rather than
delegating a raw argv remainder the way `init` does.
"""
from __future__ import annotations

import argparse
import os
import sys

from redraft.config import ENV_VAR


def _graph_dir_default_cwd(explicit: str | None) -> str | None:
    """sync/overview ONLY -- NOT serve, and NOT resolve_graph_dir's own global contract, which
    stays exactly as strict as before. Both commands are meant to be run interactively from
    inside the graph directory itself, but REDRAFT_DIR is only ever injected into the MCP
    server's own subprocess environment (via .mcp.json) -- never into an interactive shell --
    so with no positional arg and no REDRAFT_DIR set, `redraft sync`/`overview` would
    otherwise always fail even when run exactly where the README says to run them. Falls back
    to the CWD only when BOTH are absent; an explicit arg or an already-set REDRAFT_DIR is
    left untouched, so resolve_graph_dir's normal explicit-arg-wins-else-env-var resolution
    still applies unchanged in every case where either is actually given.
    """
    if explicit is not None or os.environ.get(ENV_VAR):
        return explicit
    return os.getcwd()


def main(argv: list[str] | None = None) -> None:
    raw_argv = sys.argv[1:] if argv is None else argv
    if raw_argv and raw_argv[0] == "init":
        # `init` is dispatched BEFORE the top-level parser ever sees its args (rather than via
        # the subparsers mechanism every other command below uses), because argparse's
        # nargs=REMAINDER + add_help=False combination on an init subparser -- needed so this
        # file never redefines init.main's own target_dir/--no-git/--project-name -- has a
        # long-standing argparse quirk (bpo-9351-adjacent) where `redraft init --help` never
        # reaches that REMAINDER at all: the top-level parser rejects `--help` itself as
        # "unrecognized arguments" before init's own argparse (which already handles --help
        # correctly, add_help defaulting to True) ever runs. Delegating straight to
        # init.main(raw_argv[1:]) sidesteps the top-level parser for init's args entirely, so
        # both `redraft init --help` and `redraft init <dir> --help` show init's real help and
        # exit 0, exactly as init.main's own argparse already does when invoked directly.
        from redraft.init import main as init_main

        init_main(raw_argv[1:])
        return

    parser = argparse.ArgumentParser(
        prog="redraft", description="redraft: AI-assisted project development and tracking."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="Run the MCP server (reads REDRAFT_DIR from the environment).")
    subparsers.add_parser("init", help="Birth a new redraft graph repo.", add_help=False)
    ui_parser = subparsers.add_parser("ui", help="Run the operator web app.")
    ui_parser.add_argument("--host", default="127.0.0.1", help="default: 127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8420, help="default: 8420")
    ui_parser.add_argument(
        "--graph-dir", default=None, help="graph repo root; defaults to the REDRAFT_DIR environment variable"
    )
    ui_parser.add_argument(
        "--reindex-poll-interval", type=float, default=5.0,
        help="background reindex poll interval in seconds; 0 disables it (default: 5.0)",
    )
    overview_parser = subparsers.add_parser("overview", help="Print a compact markdown map of the project's shape.")
    overview_parser.add_argument(
        "graph_dir", nargs="?", default=None,
        help="graph repo root; defaults to the current directory (or REDRAFT_DIR, if set)",
    )
    sync_parser = subparsers.add_parser(
        "sync", help="Refresh an existing graph's engine-managed files (CLAUDE.md, hook, .gitignore)."
    )
    sync_parser.add_argument(
        "graph_dir", nargs="?", default=None,
        help="graph repo root; defaults to the current directory (or REDRAFT_DIR, if set)",
    )
    sync_parser.add_argument("--no-commit", action="store_true", help="refresh files but skip the git commit")

    args = parser.parse_args(argv)
    if args.command == "serve":
        from redraft.server import main as serve_main

        serve_main()
    elif args.command == "ui":
        from redraft.ui.app import main as ui_main

        ui_main(
            host=args.host, port=args.port, graph_dir=args.graph_dir,
            reindex_poll_interval=args.reindex_poll_interval,
        )
    elif args.command == "overview":
        from redraft.overview import main as overview_main

        overview_main(_graph_dir_default_cwd(args.graph_dir))
    elif args.command == "sync":
        from redraft.init import sync_main

        sync_main(_graph_dir_default_cwd(args.graph_dir), no_commit=args.no_commit)


if __name__ == "__main__":  # pragma: no cover
    main()
