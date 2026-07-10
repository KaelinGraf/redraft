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
