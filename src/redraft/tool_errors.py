"""ToolError-derived error hierarchy for the redraft MCP tool surface.

Per design-server.md section 2: every GraphError formats as
"{code}: {message} | details={...}" so both a calling agent (reading a
tool-error's text) and tests can reliably match on the code substring
(e.g. `"cycle_rejected" in str(exc)`) regardless of exact FastMCP-version
exception plumbing -- this is deliberately robust to the "SDK churn" risk
the brief itself calls out.

`FastMCP(mask_error_details=True)` only masks *unexpected* exceptions (e.g. a
raw OSError); a GraphError (ToolError subclass) always shows its full
message. Verified against the installed fastmcp==3.4.3 ToolError base
(fastmcp.exceptions.ToolError -> FastMCPError -> Exception).

translate_storage_error() maps redraft.store's plain exceptions (pin
R7: CollisionError, CycleError, NotFoundError, LockTimeoutError,
MalformedFrontmatterError, GitOperationError) onto this hierarchy.

INTEGRATION NOTE (I1, resolved): design-storage.md section 8 and design-server.md
section 1 both named a file `errors.py` for two different purposes -- storage's
plain exception classes (raised by GraphStore) vs. this module's ToolError-derived
hierarchy. Resolved by renaming this module errors.py -> tool_errors.py, leaving
src/redraft/errors.py as storage's alone. translate_storage_error() now does
real `isinstance` checks against redraft.errors' classes -- the
match-by-class-name-string workaround (needed while this worktree had no
importable copy of storage's errors.py) is gone.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, ClassVar, Iterator

from fastmcp.exceptions import ToolError

from redraft import errors as storage_errors


class GraphError(ToolError):
    code: ClassVar[str] = "graph_error"

    def __init__(self, message: str, **details: Any) -> None:
        self.details = details
        payload = f"{self.code}: {message}"
        if details:
            payload += f" | details={details}"
        super().__init__(payload)


class NotFoundError(GraphError):
    code = "not_found"


class CollisionError(GraphError):
    code = "collision"


class CycleRejectedError(GraphError):
    code = "cycle_rejected"


class LockTimeoutError(GraphError):
    code = "lock_timeout"


class MalformedFrontmatterError(GraphError):
    code = "malformed_frontmatter"


class GitOperationError(GraphError):
    code = "git_operation_failed"


class InvalidArgumentError(GraphError):
    """Additive 5th category (design-server.md section 2): pure request-shape
    validation failures the tool layer catches before ever calling the store
    (e.g. an illegal status for a type, keep_id == drop_id). Never used for
    anything requiring a state read -- that's GraphStore's job (pin R1)."""

    code = "invalid_argument"


# redraft.errors exception type -> ToolError-derived class (pin R7). CycleError is
# storage's name (design-storage.md section 8); it maps to our CycleRejectedError, whose
# *code* ("cycle_rejected") is the exact string the brief's own Phase 1 gate test greps for
# in the raised ToolError's text.
_STORAGE_ERROR_MAP: dict[type[Exception], type[GraphError]] = {
    storage_errors.CollisionError: CollisionError,
    storage_errors.CycleError: CycleRejectedError,
    storage_errors.NotFoundError: NotFoundError,
    storage_errors.LockTimeoutError: LockTimeoutError,
    storage_errors.MalformedFrontmatterError: MalformedFrontmatterError,
    storage_errors.GitOperationError: GitOperationError,
}


def translate_storage_error(exc: Exception) -> GraphError | None:
    """Map an exception raised by redraft.store.GraphStore onto our ToolError
    hierarchy, by real `isinstance` check against redraft.errors' classes (pin R7,
    I1). Returns None for anything unrecognized -- deliberately does NOT fall back to a
    generic GraphError, so a genuinely unexpected bug (not one of the 6 pin-R7 storage
    exceptions) keeps propagating as its original type and gets masked by
    FastMCP(mask_error_details=True), exactly like any other unanticipated exception. Only
    use this via translate_store_errors() below, which implements that re-raise.
    """
    for storage_cls, mapped_cls in _STORAGE_ERROR_MAP.items():
        if isinstance(exc, storage_cls):
            return mapped_cls(str(exc))
    return None


@contextmanager
def translate_store_errors() -> Iterator[None]:
    """Wrap a GraphStore call: translate its 6 known exception types (pin R7)
    into our ToolError hierarchy; let anything else propagate unchanged so
    FastMCP's mask_error_details still masks truly unexpected errors.

        with translate_store_errors():
            node = state.store.create_node(...)
    """
    try:
        yield
    except Exception as exc:
        mapped = translate_storage_error(exc)
        if mapped is None:
            raise
        raise mapped from exc
