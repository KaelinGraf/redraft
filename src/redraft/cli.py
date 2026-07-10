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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="redraft", description="redraft: AI-assisted project development and tracking."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="Run the MCP server (reads REDRAFT_DIR from the environment).")
    init_parser = subparsers.add_parser("init", help="Birth a new redraft graph repo.", add_help=False)
    init_parser.add_argument("init_args", nargs=argparse.REMAINDER)
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
        help="graph repo root; defaults to the REDRAFT_DIR environment variable",
    )
    sync_parser = subparsers.add_parser(
        "sync", help="Refresh an existing graph's engine-managed files (CLAUDE.md, hook, .gitignore)."
    )
    sync_parser.add_argument(
        "graph_dir", nargs="?", default=None,
        help="graph repo root; defaults to the REDRAFT_DIR environment variable",
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

        overview_main(args.graph_dir)
    elif args.command == "sync":
        from redraft.init import sync_main

        sync_main(args.graph_dir, no_commit=args.no_commit)
    else:
        from redraft.init import main as init_main

        init_main(args.init_args)


if __name__ == "__main__":  # pragma: no cover
    main()
