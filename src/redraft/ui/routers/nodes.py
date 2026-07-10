"""/api/nodes* -- CRUD, rename, merge, neighbors (s6-ui.md §9).

GET .../{id}, GET .../{id}/neighbors are Lane B (queries.py, own connection, never touch
StoreWorker). POST/PATCH/DELETE and the rename/merge actions are Lane A (through
UIAppState.mutate, s6-ui.md §4.2's generation counter)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, HTTPException, Query, Request

from redraft.models import (
    DeleteResult,
    Direction,
    EdgeType,
    MergeResult,
    NeighborEdge,
    NodeOut,
    NodeWithNeighbors,
    RenameResult,
    to_node_out,
)
from redraft.store import GraphStore
from redraft.tools.read_tools import index_read_conn
from redraft.ui import mutations
from redraft.ui.models import CreateNodeRequest, UpdateNodeRequest
from redraft.ui.queries import node_detail, node_neighbors

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

router = APIRouter()


@router.get("/api/nodes/{id}", response_model=NodeWithNeighbors)
def get_node(id: str, request: Request, neighbor_depth: Annotated[int, Query(ge=0)] = 0) -> NodeWithNeighbors:
    state: UIAppState = request.app.state.ui
    with index_read_conn(state.graph_dir) as conn:
        return node_detail(conn, id, neighbor_depth)


@router.get("/api/nodes/{id}/neighbors", response_model=list[NeighborEdge])
def get_node_neighbors(
    id: str,
    request: Request,
    edge_types: Annotated[list[EdgeType] | None, Query()] = None,
    direction: Annotated[Direction, Query()] = "both",
) -> list[NeighborEdge]:
    state: UIAppState = request.app.state.ui
    with index_read_conn(state.graph_dir) as conn:
        return node_neighbors(conn, id, edge_types, direction)


@router.post("/api/nodes", response_model=NodeOut)
async def create_node(body: CreateNodeRequest, request: Request) -> NodeOut:
    """CreateNodeRequest's own model_validator rejects the common bare-ValueError cases
    up front (empty title, bad status-for-type, part_of-inside-edges) before this ever
    touches the store; UIAppState.mutate's own blanket ValueError->422 safety net (see its
    docstring) covers the one residual case that validator can't close statelessly (a title
    that sanitizes to "" despite being non-empty/non-whitespace)."""
    state: UIAppState = request.app.state.ui
    node = await state.mutate(
        GraphStore.create_node,
        type=body.type,
        title=body.title,
        body=body.body,
        status=body.status,
        properties=body.properties,
        part_of=body.part_of,
        edges=body.edges,
    )
    return to_node_out(node)


@router.patch("/api/nodes/{id}", response_model=NodeOut)
async def update_node(id: str, body: UpdateNodeRequest, request: Request) -> NodeOut:
    """No pydantic-level status/type pre-check is possible here (unlike create_node): the
    node's TYPE lives in the store, not in this request body, so whether `status` is legal
    can only be known once GraphStore.update_node has already looked the node up --
    UIAppState.mutate's blanket ValueError->422 safety net covers it."""
    state: UIAppState = request.app.state.ui
    node = await state.mutate(
        GraphStore.update_node,
        id,
        body=body.body,
        mode=body.mode,
        status=body.status,
        properties=body.properties,
        remove_properties=body.remove_properties,
    )
    return to_node_out(node)


@router.delete("/api/nodes/{id}", response_model=DeleteResult)
async def delete_node(id: str, request: Request) -> DeleteResult:
    state: UIAppState = request.app.state.ui
    return await state.mutate(mutations.delete_node, id)


@router.post("/api/nodes/{id}/rename", response_model=RenameResult)
async def rename_node(id: str, request: Request, new_title: Annotated[str, Body(embed=True)]) -> RenameResult:
    state: UIAppState = request.app.state.ui
    return await state.mutate(mutations.rename_node, id, new_title)


@router.post("/api/nodes/{id}/merge", response_model=MergeResult)
async def merge_nodes(id: str, request: Request, drop_id: Annotated[str, Body(embed=True)]) -> MergeResult:
    state: UIAppState = request.app.state.ui
    if drop_id == id:
        raise HTTPException(400, "keep and drop id must differ")
    outcome = await state.mutate(GraphStore.merge_nodes, id, drop_id)
    return MergeResult(
        kept=to_node_out(outcome.kept),
        dropped_id=drop_id,
        dropped_body_preview=outcome.dropped_body_preview,
        warnings=outcome.warnings,
    )
