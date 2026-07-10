"""GET /api/schema (s6-ui.md §9). Lane B, static -- no DB connection touched at all."""
from __future__ import annotations

from fastapi import APIRouter

from redraft.models import STATUS_BY_TYPE
from redraft.schema import EdgeType, NodeType
from redraft.ui.models import SchemaOut

router = APIRouter()


@router.get("/api/schema", response_model=SchemaOut)
def get_schema() -> SchemaOut:
    """schema.NodeType/EdgeType + models.STATUS_BY_TYPE, serialized -- zero new logic."""
    return SchemaOut(
        node_types=[t.value for t in NodeType],
        edge_types=[t.value for t in EdgeType],
        status_by_type={k: (sorted(v) if v else None) for k, v in STATUS_BY_TYPE.items()},
    )
