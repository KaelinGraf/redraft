"""Resolve REDRAFT_DIR and the repo-root-relative path layout (design §8, Appendix C)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "REDRAFT_DIR"


@dataclass(frozen=True)
class GraphPaths:
    """Filesystem layout rooted at a redraft repo (the directory containing graph/nodes/)."""

    root: Path

    @property
    def nodes_dir(self) -> Path:
        return self.root / "graph" / "nodes"

    @property
    def attachments_dir(self) -> Path:
        return self.root / "graph" / "attachments"

    @property
    def index_dir(self) -> Path:
        return self.root / "index"

    @property
    def index_db(self) -> Path:
        return self.index_dir / "graph.sqlite3"

    @property
    def lock_file(self) -> Path:
        return self.root / ".redraft.lock"

    @property
    def gitignore(self) -> Path:
        return self.root / ".gitignore"


def resolve_graph_dir(graph_dir: str | Path | None = None) -> Path:
    """Resolve the redraft repo root: explicit arg wins, else the REDRAFT_DIR env var."""
    if graph_dir is not None:
        return Path(graph_dir).resolve()
    env = os.environ.get(ENV_VAR)
    if not env:
        raise RuntimeError(f"{ENV_VAR} is not set and no graph_dir was given")
    if "${" in env:
        raise RuntimeError(
            f"{ENV_VAR} contains an unexpanded ${{...}} placeholder -- your MCP client "
            f"did not expand it; set {ENV_VAR} to the graph directory's real path."
        )
    return Path(env).resolve()
