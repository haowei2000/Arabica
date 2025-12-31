#!/usr/bin/env python3
"""
Table Description Router

Provides REST API endpoints for retrieving table descriptions and DDL statements.

表描述路由模块
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_mes_db
from aiwen.schemas.common import ErrorResponse, SuccessResponse, error, success
from aiwen.schemas.nl2sql.descript_table import (
    DescribableRequest,
    DescribableResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nl2sql-table"])


async def _get_table_description(
        db: AsyncSession, table_name: str
) -> DescribableResponse:
    """
    Get table DDL statement.

    Args:
        db: Database session
        table_name: Name of the table

    Returns:
        DescribableResponse containing table DDL

    Raises:
        ValueError: If table not found
    """
    result = await db.execute(text(f"SHOW CREATE TABLE `{table_name}`"))
    table_description = result.fetchone()
    if table_description:
        # Assuming the description is in the second column returned by SHOW CREATE TABLE
        description = table_description[1]
    else:
        raise ValueError("Table not found")
    return DescribableResponse(table_name=table_name, description=description)


@router.post(
    "/describe_table",
    operation_id="describe_table",
    response_model=SuccessResponse[DescribableResponse],
)
async def describe_table(
        request: Annotated[
            DescribableRequest, Body(examples=[{"table_name": "ai_indicator_info"}])
        ],
        db: Annotated[AsyncSession, Depends(get_mes_db)],
):
    """
    Get the table building statement for the related table

    获取指定表的建表语句
    """
    try:
        table_description = await _get_table_description(db, request.table_name)
        return success(
            data=table_description, message="Table description retrieved successfully"
        )
    except Exception as e:
        logger.exception("Failed to get table description")
        return error(code=500, message="Failed to get table description", detail=str(e))
