"""resolve_graph_dir: the REDRAFT_DIR env var / explicit-arg resolution (redraft.config)."""
from __future__ import annotations

import pytest

from redraft.config import ENV_VAR, resolve_graph_dir


def test_resolve_graph_dir_raises_on_unexpanded_placeholder(monkeypatch):
    """An MCP client that doesn't expand ${VAR} placeholders leaves the literal string in
    the env var -- resolve_graph_dir must turn that into a one-line diagnosis instead of
    silently resolving a bogus path like Path("${CLAUDE_PROJECT_DIR:-.}")."""
    monkeypatch.setenv(ENV_VAR, "${CLAUDE_PROJECT_DIR:-.}")
    with pytest.raises(RuntimeError, match="unexpanded"):
        resolve_graph_dir()


def test_resolve_graph_dir_raises_when_unset_and_no_explicit_arg(monkeypatch):
    """resolve_graph_dir's own global contract stays strict -- explicit-arg-wins-else-env-var,
    erroring when neither is given -- regardless of cli.py's `sync`/`overview` subcommands
    defaulting to the CWD themselves before ever calling this function (that CWD default is
    scoped to those two CLI subcommands only, never loosening this shared primitive `serve`
    also depends on)."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match="REDRAFT_DIR is not set and no graph_dir was given"):
        resolve_graph_dir()
