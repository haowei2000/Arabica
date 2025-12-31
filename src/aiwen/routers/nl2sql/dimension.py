#!/usr/bin/env python3
"""
Dimension Router

Provides REST API endpoints for retrieving sub-dimensions for indicators.

维度路由模块
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_mes_db
from aiwen.schemas.common import ErrorResponse, SuccessResponse, success
from aiwen.schemas.nl2sql.select_indicator import SelectSubIndicatorRequest
from aiwen.schemas.nl2sql.sub_dimension import (
    SingleDimension,
    SubDimensionResponseSchema,
)
from aiwen.services.nl2sql.indi_r_sub_dim import indi_r_sub_dim

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nl2sql-dimension"])


@router.post(
    "/get_sub_dimensions",
    operation_id="get_sub_dimensions",
    response_model=SuccessResponse[list[SingleDimension]] | ErrorResponse,
)
async def get_sub_dimensions(
        request: Annotated[
            SelectSubIndicatorRequest,
            Body(
                examples=[
                    {
                        "indicator_name": "工序任务完工率",
                        "indicator_gid": "8a8181989aba61b1019ac34047bd01c4",
                    }
                ]
            ),
        ],
        db: Annotated[AsyncSession, Depends(get_mes_db)],
):
    """
    Get sub-dimensions for a given indicator.

    获取指定指标的子维度列表。

    Args:
        request: Request containing indicator name and GID
        db: Database session

    Returns:
        List of sub-dimensions
    """
    logger.info("Received request for indicator_name: %s", request.indicator_name)

    sub_dimensions = await indi_r_sub_dim(
        db, request.indicator_name, request.indicator_gid
    )
    logger.info("Sub dimension_registry data: %s", sub_dimensions)
    return success(
        data=sub_dimensions,
        message="Sub dimensions retrieved successfully",
    )
