#!/usr/bin/env python3
"""
SQL Generation Router

Provides REST API endpoints for generating SQL statements from natural language queries.

SQL生成路由模块
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Body

from aiwen.schemas.common import ErrorResponse, SuccessResponse, success
from aiwen.schemas.nl2sql.generate_sql import SqlResponse
from aiwen.schemas.nl2sql.indicator_info import IndicatorInfoSchema
from aiwen.services.nl2sql.generate_sql import generate_sql as generate_sql_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nl2sql-sql"])


@router.post(
    "/generate_sql",
    operation_id="generate_sql",
    response_model=SuccessResponse[SqlResponse],
)
async def generate_sql(
        query: Annotated[
            str,
            Body(examples=["最近一个月的累计报废率"]),
        ],
        indicator_info: Annotated[
            IndicatorInfoSchema,
            Body(
                examples=[
                    {
                        "name": "累计报废率",
                        "sql_template": """select ROUND(s1.报废数 / s2.总生产数, 2) as 累计报废率
                                           from (select ifnull(sum(pumo.报废数), 0) as 报废数
                                                 from pv_uex_making_order pumo
                                                 where pumo.生产状态 = '完工') s1,
                                                (select ifnull(sum(pumo.报废数 + pumo.良品数 + pumo.不良品数), 1) as 总生产数
                                                 from pv_uex_making_order pumo
                                                 where pumo.生产状态 = '完工') s2""",
                        "dimensions": [
                            {"name": "时间", "code": "SJ", "alias": "pumo.完工时间"}
                        ],
                        "related_tables": [
                            {"table_name": "pv_uex_making_order", "table_description": None}
                        ],
                    }
                ]
            ),
        ],
        additional_restriction: Annotated[
            str | None,
            Body(examples=[]),
        ] = None,
) -> SuccessResponse[SqlResponse] | ErrorResponse:
    """
    Generate an SQL statement based on a natural language query and indicator information.

    根据自然语言查询和指标信息生成SQL语句。

    Args:
        query (str): Natural language query describing the desired data.
                     描述所需数据的自然语言查询。
        indicator_info (IndicatorInfoSchema): Information about the indicator to base the SQL on.
                                              用于生成SQL的指标信息。
        additional_restriction (str, optional): Additional restrictions to apply to the SQL query.
                                                要应用于SQL查询的额外限制条件。
    Returns:
        SuccessResponse[SqlResponse]: Generated SQL statement and related information.
                                      生成的SQL语句和相关信息。
    """
    logger.info("Received SQL generation request with query: %s", query)

    sql_response = await generate_sql_service(
        query=query,
        indicator_info=indicator_info,
        additional_restriction=additional_restriction,
    )
    return success(data=sql_response, message="SQL generated successfully")
