"""MCP prompt registration for the organizing protocol.

Single-source rule: src/redraft/organizing_protocol.md is a byte-identical packaged
copy of docs/protocol/organizing-protocol.md (tests/test_phase3_gates.py asserts the two
stay byte-identical, so this content can never silently drift from the doc it's copied
from). Loaded via importlib.resources so it travels inside the wheel — verified empirically
against the installed uv_build backend: any non-.py file placed under a package directory
(src/redraft/) is swept into the wheel by default, no [tool.uv.build-backend] config
needed (confirmed by building a wheel and inspecting its contents; pyproject.toml is
unchanged by this slice as a result).

This same text is also used as FastMCP's top-level `instructions=` (server.py) — the prompt
and the instructions are deliberately the same content, not two copies maintained by hand.
"""
from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

from fastmcp import FastMCP

if TYPE_CHECKING:
    from redraft.server import ServerState

ORGANIZING_PROTOCOL_TEXT = resources.files("redraft").joinpath("organizing_protocol.md").read_text(encoding="utf-8")


def register_prompts(mcp: FastMCP, state: "ServerState") -> None:
    @mcp.prompt
    def organizing_protocol() -> str:
        """The redraft organizing protocol for turning freeform dumps into graph mutations."""
        return ORGANIZING_PROTOCOL_TEXT
