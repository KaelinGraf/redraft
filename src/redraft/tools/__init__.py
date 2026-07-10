"""Tool registration for the redraft MCP surface.

register_all wires up every tool: write, read, admin (S2) plus search/
find_similar/integrity (S3b, retrieval_tools.py -- one module for both
registrars; the search_tools.py/integrity_tools.py split this file used to
forward-reference didn't survive contact with "least code") plus
assemble_report/briefing/overview (S4b, report_tools.py -- overview added later,
same module, same registrar).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import FastMCP

from redraft.tools.admin_tools import register_admin_tools
from redraft.tools.read_tools import register_read_tools
from redraft.tools.report_tools import register_report_tools
from redraft.tools.retrieval_tools import register_integrity_tools, register_search_tools
from redraft.tools.write_tools import register_write_tools

if TYPE_CHECKING:
    from redraft.server import ServerState


def register_all(mcp: FastMCP, state: "ServerState") -> None:
    register_write_tools(mcp, state)
    register_read_tools(mcp, state)
    register_admin_tools(mcp, state)
    register_search_tools(mcp, state)
    register_integrity_tools(mcp, state)
    register_report_tools(mcp, state)
