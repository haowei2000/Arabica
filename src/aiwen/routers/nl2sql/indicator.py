#!/usr/bin/env python3
"""
Indicator Router

Provides REST API endpoints for indicator selection and retrieval.
Includes:
- Select indicator from natural language query
- Get sub-indicators for a given indicator

指标路由模块
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_mes_db
from aiwen.schemas.common import ErrorResponse, SuccessResponse, error, success
from aiwen.schemas.nl2sql.indicator_info import IndicatorInfoSchema
from aiwen.schemas.nl2sql.select_indicator import (
    IndicatorSelectResponseSchema,
    SelectIndicatorRequest,
    SelectSubIndicatorRequest,
)
from aiwen.schemas.nl2sql.sub_indicator import SingleIndicator
from aiwen.services.nl2sql.indi_r_sub_indi import indi_r_sub_indi
from aiwen.services.nl2sql.select_indicator import select_indicator_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nl2sql-indicator"])


@router.post(
    "/select_indicator",
    operation_id="select_indicator",
    response_model=SuccessResponse[IndicatorSelectResponseSchema],
)
async def select_indicator_endpoint(
        request: Annotated[
            SelectIndicatorRequest,
            Body(
                examples=[
                    {"query": "最近一周的产量"},
                    {"query": "今年的累计报废率"}
                ]
            ),
        ],
        db: Annotated[AsyncSession, Depends(get_mes_db)],
):
    """
    Convert a natural language query into an indicator name for data retrieval.

    将自然语言查询转换为用于数据检索的指标名称。

    Args:
        request (SelectIndicatorRequest): Request object containing the natural language query
        db (AsyncSession): Database session for accessing mes database

    Returns:
        SuccessResponse[IndicatorSelectResponseSchema]: Response containing indicator name if successful,
                                                       or error response if operation fails

    Examples:
        Request:
            POST /nl2sql/select_indicator
            {
                "query": "最近一周的产量"
            }

        Response (Success):
            {
                "code": 200,
                "data": {
                    "indicator_name": "production_quantity_last_week"
                },
                "message": "Indicator selected successfully"
            }
    """
    logger.info("Received indicator selection request with query: %s", request.query)

    try:
        indicator_name: IndicatorSelectResponseSchema = await select_indicator_service(
            query=request.query, db=db
        )
        return success(data=indicator_name, message="Indicator selected successfully")
    except Exception as e:
        logger.exception("Failed to select indicator")
        return error(code=500, message="Failed to select indicator", detail=str(e))


@router.post(
    "/get_sub_indicators",
    response_model=SuccessResponse[list[SingleIndicator]] | ErrorResponse,
)
async def get_sub_indicators(
        request: Annotated[
            SelectSubIndicatorRequest,
            Body(
                examples=[
                    {
                        "indicator_name": "总产品收入",
                        "indicator_gid": "9a9a9a819ab896fa019ab9249b920022",
                    }
                ]
            ),
        ],
        db: Annotated[AsyncSession, Depends(get_mes_db)],
):
    """
    Get sub-indicators for a given indicator.

    获取指定指标的子指标列表。

    Args:
        request: Request containing indicator name and GID
        db: Database session

    Returns:
        List of sub-indicators
    """
    logger.info("Received request for indicator_name: %s", request.indicator_name)

    sub_indicators = await indi_r_sub_indi(
        db, request.indicator_name, request.indicator_gid
    )
    indicator_data = [
        SingleIndicator(
            indicator_gid=item["indicator_gid"], indicator_name=item["indicator_name"]
        )
        for item in sub_indicators
    ]
    logger.info("Sub indicator data: %s", indicator_data)

    return success(data=indicator_data, message="Sub indicators retrieved successfully")
