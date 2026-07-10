"""redraft init: the graph-birth console script (redraft.init).

end-to-end against tmp_path only, matching every other test module's convention
(conftest.py: "All tests operate on tmp_path -- never the real repo's graph/.").
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from redraft import init as init_mod
from redraft.errors import GitOperationError
from redraft.gitops import _GITIGNORE_LINES
from redraft.init import GraphAlreadyExistsError, NotAGraphError, init_graph, main, sync_graph, sync_main
from redraft.prompts import ORGANIZING_PROTOCOL_TEXT
from redraft.store import GraphStore

_REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGED_PROTOCOL_PATH = _REPO_ROOT / "src" / "redraft" / "organizing_protocol.md"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_init_end_to_end_files_gitignore_and_git_log(tmp_path):
    target = tmp_path / "my-graph"
    result = init_graph(target, project_name="Demo Project")

    assert result.target_dir == target
    assert result.git_initialized is True
    assert result.committed is True

    assert (target / "graph" / "nodes").is_dir()
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == ORGANIZING_PROTOCOL_TEXT
    assert (target / ".claude" / "settings.json").exists()

    gitignore_text = (target / ".gitignore").read_text()
    for line in _GITIGNORE_LINES:
        assert line in gitignore_text

    assert (target / ".git").is_dir()
    log = _git(target, "log", "--oneline").stdout
    assert len(log.strip().splitlines()) == 1
    assert "Demo Project" in log
    assert _git(target, "branch", "--show-current").stdout.strip() == "main"

    committed_files = set(_git(target, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines())
    # graph/nodes/ is empty -- nothing to track
    assert committed_files == {"CLAUDE.md", ".mcp.json", ".gitignore", ".claude/settings.json"}


def test_init_refuses_when_nodes_dir_nonempty(tmp_path):
    target = tmp_path / "existing"
    (target / "graph" / "nodes").mkdir(parents=True)
    (target / "graph" / "nodes" / "Some Node.md").write_text("---\ntype: concept\n---\n\nbody\n")

    with pytest.raises(GraphAlreadyExistsError, match="not empty"):
        init_graph(target)

    assert not (target / "CLAUDE.md").exists()  # refusal is checked before anything else is written
    assert not (target / ".git").exists()


@pytest.mark.parametrize("existing", ["CLAUDE.md", ".mcp.json", ".claude/settings.json"])
def test_init_refuses_when_target_has_own_reserved_file(tmp_path, existing):
    """A project dir with its own CLAUDE.md/.mcp.json/.claude/settings.json but no graph yet
    must not be clobbered."""
    target = tmp_path / "existing-project"
    target.mkdir()
    existing_path = target / existing
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text("hand-authored, not ours\n")

    with pytest.raises(GraphAlreadyExistsError, match="refusing to overwrite"):
        init_graph(target)

    assert existing_path.read_text() == "hand-authored, not ours\n"  # untouched
    assert not (target / "graph").exists()  # refusal is checked before anything is written
    assert not (target / ".git").exists()


def test_init_appends_to_preexisting_gitignore_without_clobbering(tmp_path):
    target = tmp_path / "has-gitignore"
    target.mkdir()
    (target / ".gitignore").write_text("my-own-entry/\n")

    init_graph(target)

    gitignore_text = (target / ".gitignore").read_text()
    assert "my-own-entry/" in gitignore_text  # user line preserved
    for line in _GITIGNORE_LINES:
        assert line in gitignore_text


def test_init_no_git_skips_git_and_gitignore(tmp_path):
    target = tmp_path / "no-git-graph"
    result = init_graph(target, git=False)

    assert result.git_initialized is False
    assert result.committed is False
    assert (target / "graph" / "nodes").is_dir()
    assert (target / "CLAUDE.md").exists()
    assert (target / ".mcp.json").exists()
    assert (target / ".claude" / "settings.json").exists()  # not gated on git -- a hook config, not git-related
    assert not (target / ".gitignore").exists()  # inert without a git repo; not written
    assert not (target / ".git").exists()


def test_mcp_json_command_is_path_binary_with_no_engine_repo_path(tmp_path):
    target = tmp_path / "portable-graph"
    init_graph(target, git=False)

    raw = (target / ".mcp.json").read_text()
    engine_root = str(Path(init_mod.__file__).resolve().parent.parent.parent)  # .../PRO-ject
    assert engine_root not in raw

    config = json.loads(raw)
    server = config["mcpServers"]["redraft"]
    assert server["command"] == "redraft"
    assert server["args"] == ["serve"]
    assert not server["command"].startswith("/")  # a PATH binary, never an absolute interpreter/script path
    assert server["env"]["REDRAFT_DIR"] == "${CLAUDE_PROJECT_DIR:-.}"
    assert server["env"]["PYTHONPATH"] == ""


def test_claude_md_matches_packaged_protocol_byte_identical(tmp_path):
    target = tmp_path / "byte-check"
    init_graph(target, git=False)
    assert (target / "CLAUDE.md").read_bytes() == PACKAGED_PROTOCOL_PATH.read_bytes()


def test_claude_settings_json_has_a_valid_session_start_hook(tmp_path):
    """Structure confirmed live against Claude Code's own hooks reference
    (https://code.claude.com/docs/en/hooks): hooks.SessionStart is a list of
    {matcher?, hooks} blocks, each `hooks` entry a {type: "command", command, timeout}
    object -- see init._claude_settings()'s own docstring for the full citation."""
    target = tmp_path / "hook-graph"
    init_graph(target, git=False)

    config = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))

    session_start = config["hooks"]["SessionStart"]
    assert isinstance(session_start, list) and len(session_start) == 1
    hooks = session_start[0]["hooks"]
    assert isinstance(hooks, list) and len(hooks) == 1
    hook = hooks[0]
    assert hook["type"] == "command"
    assert "redraft overview" in hook["command"]
    assert "CLAUDE_PROJECT_DIR" in hook["command"]
    assert "PYTHONPATH=" in hook["command"]  # guards against inherited PYTHONPATH pollution
    assert isinstance(hook["timeout"], (int, float)) and hook["timeout"] > 0


def test_default_project_name_falls_back_to_directory_name(tmp_path):
    target = tmp_path / "fallback-name-graph"
    init_graph(target)
    log = _git(target, "log", "-1", "--format=%s").stdout
    assert "fallback-name-graph" in log


def test_target_dir_that_is_a_file_raises(tmp_path):
    target = tmp_path / "a-file"
    target.write_text("not a directory")
    with pytest.raises(NotADirectoryError):
        init_graph(target)


def test_git_commit_failure_surfaces_as_git_operation_error(tmp_path, monkeypatch):
    """No git identity configured anywhere (env or global config) -- `git commit` fails;
    the failure must surface as the existing GitOperationError, not an opaque
    CalledProcessError, so main()'s except clause (and any future MCP-side caller) can
    handle it uniformly."""
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_NAME", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_EMAIL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.gitconfig identity either
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    target = tmp_path / "no-identity-graph"
    with pytest.raises(GitOperationError):
        init_graph(target)


# -- CLI wiring (main()) -----------------------------------------------------------------


def test_main_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    assert "redraft init" in capsys.readouterr().out


def test_main_happy_path_prints_next_steps(tmp_path, capsys):
    target = tmp_path / "cli-graph"
    main([str(target)])
    out = capsys.readouterr().out
    assert str(target) in out
    assert "cd " in out
    assert (target / "CLAUDE.md").exists()


def test_main_no_git_flag_wires_through(tmp_path):
    target = tmp_path / "cli-no-git"
    main([str(target), "--no-git"])
    assert not (target / ".git").exists()


def test_main_refusal_prints_error_to_stderr_and_exits_nonzero(tmp_path, capsys):
    target = tmp_path / "cli-existing"
    (target / "graph" / "nodes").mkdir(parents=True)
    (target / "graph" / "nodes" / "X.md").write_text("---\ntype: concept\n---\n\nbody\n")

    with pytest.raises(SystemExit) as exc_info:
        main([str(target)])
    assert exc_info.value.code == 1
    assert "not empty" in capsys.readouterr().err


# -- sync_graph (redraft sync) -------------------------------------------------------------


def _commit_simulated_old_engine_claude_md(target: Path) -> None:
    """Commit CLAUDE.md content genuinely different from the currently-packaged protocol --
    simulates a graph actually born under an older engine (as opposed to a merely
    hand-mangled, uncommitted edit) so that restoring it is a real diff against HEAD."""
    (target / "CLAUDE.md").write_text("# an old protocol version\n", encoding="utf-8")
    _git(target, "add", "--", "CLAUDE.md")
    _git(target, "commit", "-m", "simulate an old-engine graph")


def test_sync_raises_when_not_a_graph(tmp_path):
    with pytest.raises(NotAGraphError, match="not a redraft graph"):
        sync_graph(tmp_path)
    assert not (tmp_path / "graph").exists()  # refuses without scaffolding anything


def test_sync_immediately_after_init_is_a_noop(tmp_path):
    """init_graph() and sync_graph() write CLAUDE.md/.claude/settings.json from the exact
    same source functions -- a freshly-born graph is already "synced" by construction."""
    target = tmp_path / "fresh"
    init_graph(target)

    result = sync_graph(target)

    assert result.changed == []
    assert result.committed is False


def test_sync_restores_hand_edited_files_but_a_head_round_trip_commits_nothing(tmp_path):
    """The commit half of this is the interesting case: CLAUDE.md gets hand-mangled (an
    uncommitted, dirty edit) and settings.json deleted, then sync restores both to EXACTLY
    what's already committed at HEAD (the same init_graph() commit wrote them from the same
    source functions sync_graph() uses) -- from git's perspective the working tree round-trips
    back to HEAD, so there is genuinely nothing to commit even though the files were
    rewritten. Regression guard: this must not raise GitOperationError('nothing to commit')."""
    target = tmp_path / "mangled"
    init_graph(target)
    (target / "CLAUDE.md").write_text("STALE\n", encoding="utf-8")
    (target / ".claude" / "settings.json").unlink()

    result = sync_graph(target)

    assert sorted(result.changed) == [".claude/settings.json", "CLAUDE.md"]
    assert result.committed is False
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == ORGANIZING_PROTOCOL_TEXT
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "SessionStart" in settings["hooks"]
    log = _git(target, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 1  # still just the init commit -- no empty/no-op commit was made


def test_sync_commits_when_head_genuinely_predates_the_current_protocol(tmp_path):
    target = tmp_path / "old-engine-graph"
    init_graph(target)
    _commit_simulated_old_engine_claude_md(target)

    result = sync_graph(target)

    assert result.changed == ["CLAUDE.md"]
    assert result.committed is True
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == ORGANIZING_PROTOCOL_TEXT
    log = _git(target, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 3  # init commit, the simulated old-engine commit, the new sync commit
    assert "redraft sync: refresh engine-managed files" in log[0]


def test_sync_second_run_after_a_real_commit_is_a_noop(tmp_path):
    target = tmp_path / "sync-twice"
    init_graph(target)
    _commit_simulated_old_engine_claude_md(target)
    first = sync_graph(target)
    assert first.committed is True

    second = sync_graph(target)

    assert second.changed == []
    assert second.committed is False
    log = _git(target, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 3  # unchanged from after the first sync


def test_sync_creates_claude_dir_and_settings_when_entirely_missing(tmp_path):
    """Regression guard for a graph born before the SessionStart hook existed at all -- not
    just a missing settings.json, but no .claude/ directory whatsoever."""
    target = tmp_path / "pre-hook-graph"
    init_graph(target, git=False)
    shutil.rmtree(target / ".claude")

    result = sync_graph(target, git=False)

    assert ".claude/settings.json" in result.changed
    assert (target / ".claude" / "settings.json").exists()


def test_sync_creates_mcp_json_only_if_absent(tmp_path):
    target = tmp_path / "no-mcp"
    init_graph(target, git=False)
    (target / ".mcp.json").unlink()

    result = sync_graph(target, git=False)

    assert ".mcp.json" in result.changed
    assert (target / ".mcp.json").exists()


def test_sync_never_overwrites_a_customized_mcp_json(tmp_path):
    target = tmp_path / "custom-mcp"
    init_graph(target)
    custom = json.dumps({"mcpServers": {"redraft": {"env": {"REDRAFT_DIR": "/hand/customized"}}}})
    (target / ".mcp.json").write_text(custom, encoding="utf-8")
    (target / "CLAUDE.md").write_text("STALE\n", encoding="utf-8")  # force changed non-empty overall

    result = sync_graph(target)

    assert ".mcp.json" not in result.changed
    assert (target / ".mcp.json").read_text(encoding="utf-8") == custom


def test_sync_never_touches_node_files_docs_or_reports(tmp_path):
    target = tmp_path / "has-data"
    init_graph(target)
    GraphStore(target).create_node(type="concept", title="Canary")
    (target / "docs").mkdir()
    (target / "docs" / "paper.md").write_text("saved research\n", encoding="utf-8")
    (target / "reports").mkdir()
    (target / "reports" / "r1.md").write_text("a report\n", encoding="utf-8")
    (target / "CLAUDE.md").write_text("STALE\n", encoding="utf-8")

    sync_graph(target)

    assert (target / "graph" / "nodes" / "Canary.md").exists()
    assert (target / "docs" / "paper.md").read_text(encoding="utf-8") == "saved research\n"
    assert (target / "reports" / "r1.md").read_text(encoding="utf-8") == "a report\n"


def test_sync_on_no_git_graph_refreshes_files_without_creating_git_or_gitignore(tmp_path):
    target = tmp_path / "no-git"
    init_graph(target, git=False)
    (target / "CLAUDE.md").write_text("STALE\n", encoding="utf-8")

    result = sync_graph(target)  # git=True default -- there's simply no repo to commit to

    assert "CLAUDE.md" in result.changed
    assert result.committed is False
    assert not (target / ".git").exists()
    assert not (target / ".gitignore").exists()  # inert without a git repo -- sync must not add one


def test_sync_no_commit_kwarg_refreshes_files_but_skips_commit(tmp_path):
    target = tmp_path / "no-commit-kwarg"
    init_graph(target)
    _commit_simulated_old_engine_claude_md(target)

    result = sync_graph(target, git=False)

    assert result.changed == ["CLAUDE.md"]
    assert result.committed is False
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == ORGANIZING_PROTOCOL_TEXT  # still fixed on disk
    log = _git(target, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 2  # no new commit


def test_sync_refreshes_gitignore_preserving_user_lines(tmp_path):
    target = tmp_path / "stale-gitignore"
    init_graph(target)
    (target / ".gitignore").write_text("my-own-entry/\n", encoding="utf-8")  # drops the required lines
    _git(target, "add", "--", ".gitignore")
    _git(target, "commit", "-m", "hand-edit .gitignore")

    result = sync_graph(target)

    assert ".gitignore" in result.changed
    assert result.committed is True
    text = (target / ".gitignore").read_text(encoding="utf-8")
    assert "my-own-entry/" in text
    for line in _GITIGNORE_LINES:
        assert line in text


def test_sync_recreates_a_gitignore_deleted_entirely(tmp_path):
    """Distinct from the stale-content case above: .gitignore.exists() is False going in, not
    just missing some lines -- _ensure_gitignore()'s append-mode open() creates the file from
    scratch, and the before/after None-vs-content comparison must still detect that as
    `changed`."""
    target = tmp_path / "deleted-gitignore"
    init_graph(target)
    _git(target, "rm", "-q", ".gitignore")
    _git(target, "commit", "-m", "delete .gitignore entirely")
    assert not (target / ".gitignore").exists()

    result = sync_graph(target)

    assert ".gitignore" in result.changed
    assert result.committed is True
    text = (target / ".gitignore").read_text(encoding="utf-8")
    for line in _GITIGNORE_LINES:
        assert line in text


def test_sync_git_operation_failure_surfaces_as_git_operation_error(tmp_path, monkeypatch):
    """Mirrors test_git_commit_failure_surfaces_as_git_operation_error above: a commit that
    fails for a real reason (no git identity configured anywhere) must still surface as
    GitOperationError, not an opaque CalledProcessError. The old-engine commit that creates
    the real diff must land BEFORE identity is pulled out from under git -- only
    sync_graph()'s own commit attempt is meant to fail here."""
    target = tmp_path / "identity-graph"
    init_graph(target)
    _commit_simulated_old_engine_claude_md(target)  # both succeed: identity is still intact

    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_NAME", raising=False)
    monkeypatch.delenv("GIT_COMMITTER_EMAIL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.gitconfig identity either
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    with pytest.raises(GitOperationError):
        sync_graph(target)


# -- sync_main (redraft sync CLI) ----------------------------------------------------------


def test_sync_main_prints_already_up_to_date(tmp_path, capsys):
    target = tmp_path / "sync-main-fresh"
    init_graph(target, git=False)

    sync_main(str(target))

    assert "already up to date" in capsys.readouterr().out


def test_sync_main_prints_refreshed_files_and_commit_status(tmp_path, capsys):
    target = tmp_path / "sync-main-stale"
    init_graph(target)
    _commit_simulated_old_engine_claude_md(target)

    sync_main(str(target))

    out = capsys.readouterr().out
    assert "CLAUDE.md" in out
    assert "Committed." in out


def test_sync_main_no_commit_flag_skips_commit(tmp_path, capsys):
    target = tmp_path / "sync-main-no-commit"
    init_graph(target)
    _commit_simulated_old_engine_claude_md(target)

    sync_main(str(target), no_commit=True)

    out = capsys.readouterr().out
    assert "Not committed." in out
    log = _git(target, "log", "--oneline").stdout.strip().splitlines()
    assert len(log) == 2


def test_sync_main_not_a_graph_exits_1(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        sync_main(str(tmp_path))
    assert exc_info.value.code == 1
    assert "not a redraft graph" in capsys.readouterr().err
