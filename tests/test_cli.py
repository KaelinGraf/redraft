"""redraft.cli's `overview` and `sync` subcommands: end-to-end dispatch (argparse ->
redraft.overview.main / redraft.init.sync_main). `overview` and `sync` are the subcommands
besides `ui` that cli.py itself parses (see cli.py's own module docstring) -- serve/init/ui
are exercised via their own dedicated fixtures/test modules elsewhere; sync_graph()'s own
behavior (what gets refreshed, commit semantics) is covered in depth by tests/test_init.py --
these tests exercise only cli.py's own dispatch wiring (positional graph_dir, --no-commit,
REDRAFT_DIR fallback, error exit code), mirroring the overview tests' scope below.
"""
from __future__ import annotations

import subprocess

import pytest

from redraft import cli
from redraft.init import init_graph
from redraft.store import GraphStore


def _seed(graph_dir):
    store = GraphStore(graph_dir)
    store.create_node(type="concept", title="Architecture")
    store.create_node(type="concept", title="Data model", body="How the graph is stored.", part_of="Architecture")
    store.create_node(
        type="question", title="How should embeddings be cached", status="open", part_of="Data model"
    )
    return store


def test_overview_smoke_prints_markdown_for_seeded_graph(tmp_path, capsys):
    _seed(tmp_path)

    cli.main(["overview", str(tmp_path)])

    out = capsys.readouterr().out
    assert out.strip() != ""
    assert "# Project overview" in out
    assert "Architecture" in out
    assert "Data model" in out
    assert "How should embeddings be cached" in out  # surfaced via the open-questions list


def test_overview_on_a_freshly_inited_graph_bootstraps_the_index_and_prints_empty_message(tmp_path, capsys):
    """Regression guard for the SessionStart-hook scenario: `redraft overview` may be the
    very FIRST `redraft` process to ever touch a graph (fired right after `redraft init`,
    before any MCP server has booted and built index/graph.sqlite3). init_graph() already
    scaffolds an empty graph/nodes/ -- that alone (no index/ yet) must still produce a clean
    result, not a missing-schema crash."""
    init_graph(tmp_path, git=False)

    cli.main(["overview", str(tmp_path)])

    out = capsys.readouterr().out
    assert "Empty graph" in out


def test_overview_on_a_bare_directory_refuses_without_scaffolding(tmp_path, capsys):
    """FIX: overview used to construct GraphStore unconditionally, and GraphStore's
    constructor scaffolds graph/ and index/ unconditionally too -- so running `overview` in
    ANY random directory (it defaults graph_dir to the CWD) silently created a graph there.
    Now it refuses, before ever constructing GraphStore, exactly like `sync` already does."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["overview", str(tmp_path)])

    assert exc_info.value.code == 1
    assert "not a redraft graph" in capsys.readouterr().err
    assert not (tmp_path / "graph").exists()
    assert not (tmp_path / "index").exists()


def test_overview_collapses_floating_nodes_into_one_off_spine_line(tmp_path, capsys):
    """A rationale node with no part_of parent -- the exact real-world shape this fix targets
    (organizing-protocol.md: rationale/observation/artifact attach via justifies/references/
    derived_from, never part_of) -- must not render as its own noisy "## X (rationale)"
    section; it collapses into one off-spine summary line, keeping the real spine root
    (Architecture) at the top of the map instead of buried beneath a pile of one-line
    "no branches yet" sections."""
    store = _seed(tmp_path)
    store.create_node(type="rationale", title="Floating Rationale")

    cli.main(["overview", str(tmp_path)])

    out = capsys.readouterr().out
    assert "## Architecture (concept)" in out
    assert "**Off the part_of spine:** 1 rationale" in out
    assert "## Floating Rationale" not in out  # the noisy per-node section this replaces


def test_overview_honors_redraft_dir_env_when_no_positional_arg(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    monkeypatch.setenv("REDRAFT_DIR", str(tmp_path))

    cli.main(["overview"])

    assert "Architecture" in capsys.readouterr().out


def test_overview_defaults_to_cwd_when_no_redraft_dir_and_no_arg(tmp_path, monkeypatch, capsys):
    """FIX: `redraft overview`/`sync` are meant to be run interactively from inside the graph
    dir itself, but REDRAFT_DIR is only ever injected into the MCP server's own subprocess env
    -- never an interactive shell -- so with neither an explicit arg nor REDRAFT_DIR set, this
    now defaults to the CWD instead of erroring (scoped to this CLI subcommand only;
    resolve_graph_dir's own global contract is unchanged, see test_config.py)."""
    monkeypatch.delenv("REDRAFT_DIR", raising=False)
    _seed(tmp_path)
    monkeypatch.chdir(tmp_path)

    cli.main(["overview"])

    assert "Architecture" in capsys.readouterr().out


# -- sync -------------------------------------------------------------------------------


def test_sync_smoke_reports_already_up_to_date_for_a_freshly_born_graph(tmp_path, capsys):
    init_graph(tmp_path, git=False)

    cli.main(["sync", str(tmp_path)])

    assert "already up to date" in capsys.readouterr().out


def test_sync_refreshes_a_stale_claude_md_and_commits(tmp_path, capsys):
    init_graph(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# an old protocol version\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "CLAUDE.md"], cwd=tmp_path, capture_output=True, text=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "simulate an old-engine graph"], cwd=tmp_path, capture_output=True, text=True,
        check=True,
    )

    cli.main(["sync", str(tmp_path)])

    out = capsys.readouterr().out
    assert "CLAUDE.md" in out
    assert "Committed." in out


def test_sync_no_commit_flag_wires_through_to_git_false(tmp_path, capsys):
    """A --no-git graph is always committed=False regardless of --no-commit, which wouldn't
    catch a broken flag wire-through -- use a git-tracked graph with a genuine diff (so
    *without* --no-commit this would commit) to actually prove the flag suppresses it."""
    init_graph(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# an old protocol version\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", "CLAUDE.md"], cwd=tmp_path, capture_output=True, text=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "simulate an old-engine graph"], cwd=tmp_path, capture_output=True, text=True,
        check=True,
    )

    cli.main(["sync", str(tmp_path), "--no-commit"])

    out = capsys.readouterr().out
    assert "Not committed." in out
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") != "# an old protocol version\n"  # still fixed
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip().splitlines()
    assert len(log) == 2  # no new commit despite the real diff


def test_sync_honors_redraft_dir_env_when_no_positional_arg(tmp_path, monkeypatch, capsys):
    init_graph(tmp_path, git=False)
    monkeypatch.setenv("REDRAFT_DIR", str(tmp_path))

    cli.main(["sync"])

    assert "already up to date" in capsys.readouterr().out


def test_sync_defaults_to_cwd_when_no_redraft_dir_and_no_arg(tmp_path, monkeypatch, capsys):
    """Mirror of test_overview_defaults_to_cwd_when_no_redraft_dir_and_no_arg for `sync`."""
    monkeypatch.delenv("REDRAFT_DIR", raising=False)
    init_graph(tmp_path, git=False)
    monkeypatch.chdir(tmp_path)

    cli.main(["sync"])

    assert "already up to date" in capsys.readouterr().out


def test_sync_not_a_graph_errors(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["sync", str(tmp_path)])

    assert exc_info.value.code == 1
    assert "not a redraft graph" in capsys.readouterr().err


# -- init --help --------------------------------------------------------------------------


def test_init_help_with_no_target_dir_exits_zero_and_prints_options(capsys):
    """MAJOR fix: `redraft init --help` (README.md's own literal quickstart command) used to
    hit the TOP-LEVEL parser's own "unrecognized arguments: --help" error (an argparse
    REMAINDER + add_help=False quirk on the old init subparser) instead of ever reaching
    init.main's own argparse, which already handles --help correctly. cli.py now dispatches
    `init` straight to init.main before the top-level parser ever sees its args."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["init", "--help"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--no-git" in out
    assert "--project-name" in out


def test_init_help_after_target_dir_exits_zero_and_prints_options(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["init", "/tmp/some-dir", "--help"])

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--no-git" in out
    assert "--project-name" in out
