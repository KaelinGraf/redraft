# redraft (engine)

This repo is the **engine**: the MCP server, canonical-store/index code, and the CLI
(`redraft serve`, `redraft init`) that redraft ships. It holds no graph data itself —
a project's graph (its nodes, decisions, and the organizing protocol as *that
project's own* `CLAUDE.md`) lives in a separate repo, born with `redraft init` (see
README.md's Quickstart). Engine and graph are cloned, versioned, and upgraded
independently; redraft's own dogfood graph lives in its own sibling repo, not here.

`.mcp.json` is gitignored in this repo — it is device-specific dev config. To work on
the engine with live MCP tools, write your own pointing at your own graph:

```json
{"mcpServers": {"redraft": {"command": "uv",
  "args": ["run", "--directory", "${CLAUDE_PROJECT_DIR:-.}", "redraft", "serve"],
  "env": {"REDRAFT_DIR": "<abs path to your graph repo>", "PYTHONPATH": ""}}}}
```

## Running tests

```
env -u PYTHONPATH uv run pytest -q
```

`-u PYTHONPATH` matters on any shell where something else (e.g. a ROS workspace) has
polluted `PYTHONPATH` — an inherited entry can shadow this project's own dependencies or
autoload an incompatible pytest plugin. Python itself is uv-managed (`.python-version`
pins 3.12); a system/distro Python is not supported — `redraft.server` fails fast at
startup if its `sqlite3` build lacks FTS5 or extension-loading, both of which a
python-build-standalone/uv-managed CPython provides and many system Pythons don't.

## Rebuilding the UI

`frontend/` (Vite + React + TypeScript) is dev-time only — not packaged, not under `src/`.
Its build *output* is committed under `src/redraft/ui/static/` instead, so `git clone && uv
sync` never needs Node/npm (s6-ui.md §7). After any `frontend/src/**` change:

```
cd frontend && npm ci && npm run build:ship
```

`build:ship` (a package.json script) runs `tsc -b && vite build`, then replaces
`src/redraft/ui/static/` with the fresh `frontend/dist/` output. Commit the resulting
`src/redraft/ui/static/` changes in the same change as the `frontend/` edit that produced
them — `frontend/node_modules/` and `frontend/dist/` are gitignored; `src/redraft/ui/static/`
is not.

For iterative frontend dev with hot reload: run the backend
(`env -u PYTHONPATH REDRAFT_DIR=<graph dir> uv run redraft ui`, listens on `127.0.0.1:8420`)
in one terminal and `cd frontend && npm run dev` in another — `vite.config.ts` proxies
`/api/*` to the backend, so the Vite dev server and FastAPI never need CORS config between
them.

## The organizing protocol

The full protocol this project's MCP server ships — as both the `CLAUDE.md`
`redraft init` writes into every graph repo, and the server's own
`organizing_protocol` MCP prompt — lives at `docs/protocol/organizing-protocol.md`. It
is not duplicated here: this file governs how to work on the *engine*; that document
governs how to work a *graph*.
