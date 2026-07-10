"""GET /api/search, GET /api/dedup-hints (s6-ui.md §9) -- both Lane B, both need sqlite-vec
loaded (retrieval_tools.open_conn, not the plain read_tools.index_read_conn) since a warm
call touches vec_nodes. `k` has no FastAPI-level lower bound: hybrid_search.search_nodes/
find_similar/fts_candidates already gracefully return [] for k<=0 (a deliberate, existing
library-layer behavior, not something the HTTP layer should re-guard)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Query, Request

from redraft.models import NodeType, SearchHit, to_search_hit
from redraft.retrieval import search_nodes as lib_search_nodes
from redraft.tools.retrieval_tools import open_conn
from redraft.ui.models import DedupHintsOut
from redraft.ui.queries import dedup_hints

if TYPE_CHECKING:
    from redraft.ui.app import UIAppState

router = APIRouter()


@router.get("/api/search", response_model=list[SearchHit])
def search(
    q: str,
    request: Request,
    types: Annotated[list[NodeType] | None, Query()] = None,
    status: str | None = None,
    k: int = 10,
) -> list[SearchHit]:
    state: UIAppState = request.app.state.ui
    with open_conn(state.graph_dir) as conn:
        hits = lib_search_nodes(conn, state.retrieval_config, q, types=types, status=status, k=k)
    return [to_search_hit(h) for h in hits]


@router.get("/api/dedup-hints", response_model=DedupHintsOut)
def get_dedup_hints(title: str, request: Request, k: int = 5) -> DedupHintsOut:
    state: UIAppState = request.app.state.ui
    with open_conn(state.graph_dir) as conn:
        return dedup_hints(conn, state.retrieval_config, title, k, state.embedder_ready)
