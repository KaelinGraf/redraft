"""design-storage.md §7 (git operations): subprocess wrapper, snapshot() semantics."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from redraft import gitops


def _git(tmp_path, *args):
    return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True)


def test_snapshot_initializes_repo_and_writes_gitignore_before_first_add(tmp_path):
    (tmp_path / "graph" / "nodes").mkdir(parents=True)
    (tmp_path / "graph" / "nodes" / "Some Node.md").write_text("---\ntype: concept\n---\n\nbody\n")

    result = gitops.snapshot(tmp_path, "first commit")

    assert result.initialized_repo is True
    assert (tmp_path / ".git").is_dir()
    gitignore_text = (tmp_path / ".gitignore").read_text()
    for line in gitops._GITIGNORE_LINES:
        assert line in gitignore_text
    assert result.committed is True
    committed_files = _git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    assert ".gitignore" in committed_files  # gitignore was staged/committed, not created too late
    assert "graph/nodes/Some Node.md" in committed_files


def test_snapshot_is_noop_when_nothing_changed(tmp_path):
    gitops.snapshot(tmp_path, "first commit")
    result = gitops.snapshot(tmp_path, "second commit attempt")
    assert result.committed is False
    assert result.sha is None
    assert result.message == "nothing to commit"


def test_snapshot_commits_and_returns_sha(tmp_path):
    (tmp_path / "graph" / "nodes").mkdir(parents=True)
    (tmp_path / "graph" / "nodes" / "note.md").write_text("hello")
    result = gitops.snapshot(tmp_path, "a real commit")
    assert result.committed is True
    assert result.sha is not None
    assert len(result.sha) == 40
    actual_head = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert result.sha == actual_head


def test_snapshot_push_false_by_default_never_invokes_network(tmp_path):
    (tmp_path / "graph" / "nodes").mkdir(parents=True)
    (tmp_path / "graph" / "nodes" / "note.md").write_text("hello")
    with patch("redraft.gitops.subprocess.run", wraps=subprocess.run) as spy:
        result = gitops.snapshot(tmp_path, "no push")
    assert result.pushed is False
    push_calls = [c for c in spy.call_args_list if "push" in c.args[0]]
    assert push_calls == []


def test_snapshot_ignores_dirty_files_outside_graph(tmp_path):
    """I6: git add is pathspec-scoped to graph/, reports/, .gitignore -- a dirty file
    elsewhere in the repo (e.g. a WIP source edit, plausible since REDRAFT_DIR may be
    this very project's own root) must never be swept into a snapshot commit."""
    (tmp_path / "graph" / "nodes").mkdir(parents=True)
    (tmp_path / "graph" / "nodes" / "tracked.md").write_text("tracked")
    gitops.snapshot(tmp_path, "initial commit")

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("print('wip')")
    result = gitops.snapshot(tmp_path, "should not see src/")

    assert result.committed is False
    assert result.message == "nothing to commit"
    committed_files = _git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    assert "src/foo.py" not in committed_files


def test_snapshot_works_when_reports_dir_absent(tmp_path):
    """reports/ (a future S4 output dir) usually will not exist -- git add with a
    non-matching pathspec is a fatal error, so the pathspec list must be built only from
    paths that currently exist (I6)."""
    assert not (tmp_path / "reports").exists()
    (tmp_path / "graph" / "nodes").mkdir(parents=True)
    (tmp_path / "graph" / "nodes" / "x.md").write_text("x")
    result = gitops.snapshot(tmp_path, "no reports dir")
    assert result.committed is True


def test_snapshot_does_not_commit_docs_cache(tmp_path):
    """docs/ is a local, gitignored cache of re-fetchable reference material (organizing
    protocol §2), not version-controlled -- a file placed there must never be swept into
    a snapshot commit, unlike graph/ and reports/ (mirrors
    test_snapshot_ignores_dirty_files_outside_graph's shape)."""
    (tmp_path / "graph" / "nodes").mkdir(parents=True)
    (tmp_path / "graph" / "nodes" / "x.md").write_text("x")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "paper-summary.md").write_text("saved research")

    result = gitops.snapshot(tmp_path, "add a node, leave docs/ alone")

    assert result.committed is True
    committed = _git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    assert "docs/paper-summary.md" not in committed
    assert "graph/nodes/x.md" in committed


def test_snapshot_ensure_gitignore_preserves_human_additions(tmp_path):
    (tmp_path / ".gitignore").write_text("*.tmp\n")
    gitops.snapshot(tmp_path, "first commit")
    text = (tmp_path / ".gitignore").read_text()
    assert "*.tmp" in text  # human's own line survives
    assert "/index/" in text  # design's required lines still got appended


def test_snapshot_second_call_after_new_change_commits_again(tmp_path):
    (tmp_path / "graph" / "nodes").mkdir(parents=True)
    (tmp_path / "graph" / "nodes" / "a.md").write_text("a")
    first = gitops.snapshot(tmp_path, "commit a")
    (tmp_path / "graph" / "nodes" / "b.md").write_text("b")
    second = gitops.snapshot(tmp_path, "commit b")
    assert second.committed is True
    assert second.initialized_repo is False  # repo already existed on the second call
    assert second.sha != first.sha
