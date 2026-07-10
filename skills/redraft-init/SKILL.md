---
name: redraft-init
description: Set up a redraft design-tracking graph for the current project and register it with Claude Code. Use when the user asks to add redraft, set up project tracking, or "give this project a design graph" and no graph is registered yet.
---

Follow these steps in order. Do not skip ahead or combine steps.

1. **Already a graph repo?** Check whether `graph/nodes/` exists at the project root. If it
   does, this project already has a redraft graph -- verify `.mcp.json` points at it and
   stop here; do not re-initialize or touch anything.

2. **Is the engine installed?** Run `command -v redraft`. If it's missing, print the install
   steps verbatim for the user to run themselves and stop -- do not install it yourself:

   ```
   git clone https://github.com/KaelinGraf/redraft.git redraft
   cd redraft
   uv tool install --from . redraft
   ```

3. **Birth the graph.** Run `redraft init <project root>/redraft-graph`. If that directory
   already exists and is non-empty, **ask the user first** before doing anything -- never
   overwrite silently.

4. **Register it at project scope:**

   ```
   claude mcp add --scope project -e REDRAFT_DIR=<abs path to redraft-graph> -e PYTHONPATH= --transport stdio redraft -- redraft serve
   ```

   Do not use `${CLAUDE_PROJECT_DIR}` here: this `.mcp.json` is registered from the *code*
   project, so CLAUDE_PROJECT_DIR would expand to the code project's root, not the graph
   directory -- REDRAFT_DIR must be spelled out as an absolute path.

5. **Gitignore it.** Append `redraft-graph/` to the project's own `.gitignore` -- the graph
   is a separate repo with its own git history (see the engine's README, "Engine and graph
   are separate repos"); it must never be swept into the code project's commits.

6. **Tell the user to restart the session.** The MCP tools do not appear until Claude Code
   restarts. Never claim the tools are live before that happens.

7. **Report back compactly**: where the graph was birthed, that it's registered at project
   scope, and that a restart is needed.

## What NOT to do

- Never use `--scope user` -- this graph belongs to this project, not to every project the
  user opens.
- Never point `REDRAFT_DIR` at the code project's own root -- it must be the dedicated graph
  directory from step 3, never the root that holds the project's source.
- Never seed or write any nodes during setup. Setup only births an empty graph and registers
  it; populating it is a separate, later activity driven by the organizing protocol.
