"""Unit tests for the pin-R7 exception mapping, independent of the stub/tool
round-trip. Covers all 6 mapped storage exception types -- including
MalformedFrontmatterError and GitOperationError, which aren't naturally
reachable through a real create_node/create_edge/... round-trip in this
suite (they'd need a hand-corrupted node file or a genuinely broken git
setup) -- plus the "unrecognized exception propagates unchanged" contract
that makes FastMCP's mask_error_details still work for genuinely unexpected
errors.

I1/I7: translate_storage_error() now does real `isinstance` checks against
redraft.errors' classes (the string-match-by-class-name workaround is
gone), so these tests construct the REAL storage exception types directly
instead of synthesizing look-alikes by name -- a same-named-but-unrelated
fake class would no longer map under isinstance, which is exactly the
behavior change I1 intended.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from redraft import errors as storage_errors
from redraft.tool_errors import (
    CollisionError,
    CycleRejectedError,
    GitOperationError,
    GraphError,
    LockTimeoutError,
    MalformedFrontmatterError,
    NotFoundError,
    translate_store_errors,
    translate_storage_error,
)


def _fake(name: str, message: str) -> Exception:
    """A same-named-but-unrelated exception -- used only to prove the
    "genuinely unrecognized" path still returns None under isinstance
    matching, since a fake sharing a real class's mere NAME must NOT map."""
    cls = type(name, (Exception,), {})
    return cls(message)


@pytest.mark.parametrize(
    "make_storage_exc, expected_cls, expected_code",
    [
        (lambda: storage_errors.CollisionError("X", Path("/tmp/graph/nodes/X.md")), CollisionError, "collision"),
        (lambda: storage_errors.CycleError("A", "B"), CycleRejectedError, "cycle_rejected"),
        (lambda: storage_errors.NotFoundError("X"), NotFoundError, "not_found"),
        (lambda: storage_errors.LockTimeoutError("timed out after 30s"), LockTimeoutError, "lock_timeout"),
        (
            lambda: storage_errors.MalformedFrontmatterError("bad frontmatter"),
            MalformedFrontmatterError,
            "malformed_frontmatter",
        ),
        (
            lambda: storage_errors.GitOperationError(["status"], 128, "fatal: not a git repository"),
            GitOperationError,
            "git_operation_failed",
        ),
    ],
)
def test_translate_storage_error_maps_every_pin_r7_exception(make_storage_exc, expected_cls, expected_code):
    exc = make_storage_exc()
    mapped = translate_storage_error(exc)
    assert isinstance(mapped, expected_cls)
    assert str(mapped) == f"{expected_code}: {exc}"


def test_translate_storage_error_returns_none_for_unrecognized_exception():
    exc = _fake("SomeUnrelatedBug", "boom")
    assert translate_storage_error(exc) is None


def test_translate_storage_error_ignores_a_same_named_but_unrelated_class():
    """isinstance, not class-name string matching (I1): a fake class that happens to be
    named identically to a real storage exception must NOT map -- this is the concrete
    behavior change I1 made over the worktree-era name-matching workaround."""
    exc = _fake("CollisionError", "not really a storage CollisionError")
    assert translate_storage_error(exc) is None


def test_translate_store_errors_maps_known_exception():
    with pytest.raises(GraphError, match="collision") as exc_info:
        with translate_store_errors():
            raise storage_errors.CollisionError("X", Path("/tmp/graph/nodes/X.md"), message="'X' already exists")
    assert "collision: 'X' already exists" in str(exc_info.value)


def test_translate_store_errors_reraises_unrecognized_exception_unchanged():
    """FastMCP's mask_error_details only masks *unexpected* exceptions -- a
    genuinely unrecognized error must propagate as its original type, not be
    silently upgraded to a GraphError, or masking would never apply to it."""
    class SomeInternalBug(RuntimeError):
        pass

    with pytest.raises(SomeInternalBug):
        with translate_store_errors():
            raise SomeInternalBug("unexpected")


def test_graph_error_formats_with_details():
    err = NotFoundError("node 'X' does not exist", node_id="X")
    assert str(err) == "not_found: node 'X' does not exist | details={'node_id': 'X'}"


def test_graph_error_formats_without_details():
    err = NotFoundError("node 'X' does not exist")
    assert str(err) == "not_found: node 'X' does not exist"
