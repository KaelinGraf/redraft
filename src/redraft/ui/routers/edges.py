"""/api/edges*, PUT /api/nodes/{id}/parent (s6-ui.md §9) -- all Lane A.

The reparent endpoint's URL sits under /api/nodes/{id}/..., but it is an EDGE mutation
(part_of is a delete_edge + create_edge composition, s6-ui.md §9.1) -- housed here rather
than in nodes.py, matching the design's own module-layout table (§2: "edges.py # /api/edges*,
PUT /api/nodes/{id}/parent")."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, Request

from redraft.models import DeleteResult, EdgeOut, NodeOut, to_edge_out, to_node_out
from redraft.store import GraphStore
from redraft.tools.read_tools import index_read_conn
from redraft.ui import mutations
from redraft.ui.models import EdgeBatchRequest, EdgeRequest

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

router = APIRouter()


@router.post("/api/edges", response_model=EdgeOut)
async def create_edge(body: EdgeRequest, request: Request) -> EdgeOut:
    state: UIAppState = request.app.state.ui
    edge = await state.mutate(GraphStore.create_edge, body.src, body.dst, body.type)
    return to_edge_out(edge)


@router.post("/api/edges/batch", response_model=list[EdgeOut])
async def create_edges_batch(body: EdgeBatchRequest, request: Request) -> list[EdgeOut]:
    """N edges in ONE atomic StoreWorker call -> ONE generation bump (via state.mutate),
    not one per edge -- GraphStore.create_edges itself guarantees the all-or-nothing part;
    this just routes the whole batch through Lane A as a single unit of work."""
    state: UIAppState = request.app.state.ui
    edges = await state.mutate(GraphStore.create_edges, [(e.src, e.dst, e.type) for e in body.edges])
    return [to_edge_out(edge) for edge in edges]


@router.delete("/api/edges", response_model=DeleteResult)
async def delete_edge(body: EdgeRequest, request: Request) -> DeleteResult:
    state: UIAppState = request.app.state.ui
    with index_read_conn(state.graph_dir) as conn:
        existed = (
            conn.execute(
                "SELECT 1 FROM edges WHERE src = ? AND dst = ? AND type = ?", (body.src, body.dst, body.type)
            ).fetchone()
            is not None
        )
    await state.mutate(GraphStore.delete_edge, body.src, body.dst, body.type)
    return DeleteResult(ok=True, existed=existed)


@router.put("/api/nodes/{id}/parent", response_model=NodeOut)
async def reparent_node(
    id: str, request: Request, new_parent: Annotated[str | None, Body(embed=True)] = None
) -> NodeOut:
    state: UIAppState = request.app.state.ui
    node = await state.mutate(mutations.reparent_node, id, new_parent)
    return to_node_out(node)
