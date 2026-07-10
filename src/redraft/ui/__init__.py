"""redraft's operator web app: a local, single-operator FastAPI backend for full graph
authoring (s6-ui.md). Its own OS process, built directly on redraft.store.GraphStore --
not an MCP client. Shares graph/nodes/*.md, index/graph.sqlite3, and .redraft.lock with
the MCP server (redraft.server) through the filesystem, per the locked contract.
"""
from __future__ import annotations
