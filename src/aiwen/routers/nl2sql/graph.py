#!/usr/bin/env python3
"""
Graph Router

Provides REST API endpoints for indicator graph retrieval.
Fetches graph structure (nodes and edges) associated with indicators.

图谱路由模块
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_mes_db
from aiwen.schemas.common import SuccessResponse, success
from aiwen.schemas.nl2sql.graph import GetGraphRequest, GraphResponse
from aiwen.services.nl2sql.get_graph import get_indicator_graph

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nl2sql-graph"])


@router.post(
    "/get_graph",
    operation_id="get_graph",
    response_model=SuccessResponse[GraphResponse],
)
async def get_graph(
        request: Annotated[
            GetGraphRequest,
            Body(
                examples=[
                    {"indicator_id": "9a9a9a819ab99c78019ab9dabe420056"},
                    {"indicator_id": None},
                ]
            ),
        ],
        db: Annotated[AsyncSession, Depends(get_mes_db)],
):
    """
    Retrieve graph data for a specific indicator.

    This endpoint fetches the graph structure (nodes and edges) associated with a given indicator ID
    from the MES database. The graph represents entity relationships relevant to the specified indicator.

    Args:
        request (GetGraphRequest): Request object containing the indicator_id
        db (AsyncSession): Database session for accessing mes database

    Returns:
        SuccessResponse[GraphResponse]: Response containing graph data if successful,
                                       or error response if operation fails

    Examples:
        Request:
            POST /nl2sql/get_graph
            {
                "indicator_id": "9a9a9a819ab38ac1019ab51eead8006f"
            }

        Response (Success):
            {
                "code": 200,
                "data": {
                    "nodes": [...],
                    "edges": [...]
                },
                "message": "Graph retrieved successfully"
            }
    """
    logger.info("Received request for indicator_id: %s", request.indicator_id)
    graph_data = await get_indicator_graph(db, request.indicator_id)
    return success(data=graph_data, message="Graph retrieved successfully")
