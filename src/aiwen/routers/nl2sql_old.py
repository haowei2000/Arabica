#!/usr/bin/env python3
"""
模块名称: nl2sql_graph_router

功能描述:
    提供自然语言转 SQL 图谱查询接口，根据指标 ID 获取对应的图结构数据（节点与边）。
    接口会从 MES 数据库中提取指标相关实体关系，并校验返回数据是否符合 GraphResponse 模型。

作者: wanghaowei
创建日期: 11/21/25
最后修改: 11/21/25 10:43 AM
修改人员: wanghaowei
版本: v1.0.0

公司名称: 艾普工华(武汉)有限责任公司
版权信息: © 2025 艾普工华(武汉)有限责任公司. 保留所有权利.

依赖模块:
    - fastapi
    - sqlalchemy
    - aiwen.schemas.nl2sql.graph
    - aiwen.services.nl2sql.get_graph

使用示例:
    POST /nl2sql/get_graph
    {
        "indicator_id": "IND-001"
    }
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
from aiwen.schemas.nl2sql.generate_sql import SqlResponse
from aiwen.schemas.nl2sql.graph import GetGraphRequest, GraphResponse
from aiwen.schemas.nl2sql.indicator_info import IndicatorInfoSchema
from aiwen.schemas.nl2sql.select_indicator import (
    IndicatorSelectResponseSchema,
    SelectIndicatorRequest,
    SelectSubIndicatorRequest,
)
from aiwen.schemas.nl2sql.sub_dimension import (
    SingleDimension,
)
from aiwen.schemas.nl2sql.sub_indicator import (
    SingleIndicator,
)
from aiwen.schemas.nl2sql.anomaly_detection import (
    AnomalyDetectionByIndicatorRequest,
    AnomalyDetectionRequest,
    AnomalyDetectionResponse,
)
from aiwen.services.nl2sql.anomaly.anomaly_detection import (
    detect_anomalies,
    detect_anomalies_by_indicator,
)
from aiwen.services.nl2sql.generate_sql import generate_sql as generate_sql_service
from aiwen.services.nl2sql.get_graph import get_indicator_graph
from aiwen.services.nl2sql.indi_r_sub_dim import indi_r_sub_dim
from aiwen.services.nl2sql.indi_r_sub_indi import indi_r_sub_indi
from aiwen.services.nl2sql.select_indicator import select_indicator_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/nl2sql", tags=["nl2sql"])


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


async def _get_table_description(
        db: AsyncSession, table_name: str
) -> DescribableResponse:
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
    """
    try:
        table_description = await _get_table_description(db, request.table_name)
        return success(
            data=table_description, message="Table description retrieved successfully"
        )
    except Exception as e:
        logging.exception("Failed to get table description")
        return error(code=500, message="Failed to get table description", detail=str(e))


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
    logger.info("Received request for indicator_name: %s", request.indicator_name)

    sub_dimensions = await indi_r_sub_dim(
        db, request.indicator_name, request.indicator_gid
    )
    logger.info("Sub dimension_registry data: %s", sub_dimensions)
    return success(
        data=sub_dimensions,
        message="Sub dimensions retrieved successfully",
    )


# ==================== Anomaly Detection Endpoints ====================


@router.post(
    "/detect_anomalies_by_indicator",
    operation_id="detect_anomalies_by_indicator",
    response_model=SuccessResponse[AnomalyDetectionResponse],
    summary="Detect anomalies by indicator name (Auto-load conditions)",
    description="""
    Detect anomalies in data by automatically loading detection conditions from database using indicator name.

    This is the recommended approach as it:
    - Automatically loads conditions from database
    - Simplifies API calls (no need to pass conditions)
    - Centrally manages detection rules
    - Supports dynamic configuration updates

    根据指标名称自动从数据库加载检测条件并进行异常检测（推荐方式）。
    """,
)
async def detect_anomalies_by_indicator_endpoint(
    request: Annotated[
        AnomalyDetectionByIndicatorRequest,
        Body(
            examples=[
                {
                    "data_result": [
                        {"date": "2024-01", "sales": 100, "profit": 1000},
                        {"date": "2024-02", "sales": 200, "profit": 2500},
                        {"date": "2024-03", "sales": 150, "profit": 1800},
                    ],
                    "indicator_name": "monthly_sales",
                }
            ]
        ),
    ],
) -> SuccessResponse[AnomalyDetectionResponse] | ErrorResponse:
    """
    Detect anomalies by loading conditions from database using indicator name.

    根据指标名称从数据库加载条件并进行异常检测。

    Args:
        request: Detection request containing:
            - data_result: Query result data to analyze
            - indicator_name: Indicator name to load conditions for

    Returns:
        SuccessResponse containing:
            - result: Detection result ("normal"/"detected"/"error")
            - anomalies: List of anomaly strings (format: "column:value")
            - reasons: List of detailed anomaly reasons
            - count: Number of anomalies detected (-1 indicates error)

    Examples:
        Request:
            POST /nl2sql/detect_anomalies_by_indicator
            {
                "data_result": [
                    {"date": "2024-01", "sales": 100},
                    {"date": "2024-02", "sales": 200}
                ],
                "indicator_name": "monthly_sales"
            }

        Response (Success):
            {
                "code": 200,
                "message": "Anomaly detection completed",
                "data": {
                    "result": "检测到异常",
                    "anomalies": ["sales:200"],
                    "reasons": [
                        {
                            "anomaly_value": 200,
                            "reason": "Above upper limit",
                            "upper_limit": 150.0,
                            "deviation": "50.00",
                            "deviation_rate": "33.3%",
                            "location": "Anomaly data: {...}: 200 at position 2"
                        }
                    ],
                    "count": 1
                }
            }
    """
    logger.info(
        "Received anomaly detection request for indicator: %s with %d records",
        request.indicator_name,
        len(request.data_result),
    )

    try:
        result = await detect_anomalies_by_indicator(
            request.data_result, request.indicator_name
        )

        # Check if detection was successful
        if result.get("count", -1) == -1:
            error_msg = result.get("reasons", [{}])[0].get("error", "Unknown error")
            logger.error(
                "Anomaly detection failed for indicator %s: %s",
                request.indicator_name,
                error_msg,
            )
            return error(code=400, message=result.get("result", "Detection failed"), detail=error_msg)

        logger.info(
            "Anomaly detection completed for indicator %s: %d anomalies found",
            request.indicator_name,
            result.get("count", 0),
        )

        response = AnomalyDetectionResponse(**result)
        return success(data=response, message="Anomaly detection completed")

    except Exception as e:
        logger.exception("Unexpected error during anomaly detection")
        return error(
            code=500, message="Anomaly detection failed", detail=str(e)
        )


@router.post(
    "/detect_anomalies",
    operation_id="detect_anomalies",
    response_model=SuccessResponse[AnomalyDetectionResponse],
    summary="Detect anomalies with explicit conditions",
    description="""
    Detect anomalies in data using explicitly provided detection conditions.

    This approach allows you to:
    - Specify custom detection conditions
    - Override database configurations
    - Test different thresholds without updating database

    使用显式提供的检测条件进行异常检测。
    """,
)
async def detect_anomalies_endpoint(
    request: Annotated[
        AnomalyDetectionRequest,
        Body(
            examples=[
                {
                    "records": [
                        {"date": "2024-01", "sales": 100},
                        {"date": "2024-02", "sales": 200},
                        {"date": "2024-03", "sales": 150},
                    ],
                    "condition": {
                        "judge_mode": 0,
                        "upper_limit": 150.0,
                        "lower_limit": 50.0,
                    },
                }
            ]
        ),
    ],
) -> SuccessResponse[AnomalyDetectionResponse] | ErrorResponse:
    """
    Detect anomalies using explicitly provided detection conditions.

    使用显式提供的检测条件进行异常检测。

    Args:
        request: Detection request containing:
            - records: List of data records to analyze
            - condition: Detection condition (ThresholdCondition or DynamicCondition)

    Returns:
        SuccessResponse containing detection results

    Examples:
        Request (Threshold Mode):
            POST /nl2sql/detect_anomalies
            {
                "records": [
                    {"sales": 100},
                    {"sales": 200}
                ],
                "condition": {
                    "judge_mode": 0,
                    "upper_limit": 150.0,
                    "lower_limit": 50.0
                }
            }

        Request (Dynamic Mode):
            POST /nl2sql/detect_anomalies
            {
                "records": [
                    {"sales": 100},
                    {"sales": 105},
                    {"sales": 95},
                    {"sales": 50}
                ],
                "condition": {
                    "judge_mode": 1,
                    "dynamic_type": 0,
                    "comparison_operator": 0
                }
            }
    """
    logger.info(
        "Received anomaly detection request with %d records and mode %s",
        len(request.records),
        request.condition.judge_mode,
    )

    try:
        # Convert Pydantic models to dict for service function
        import json

        records_json = json.dumps(request.records)
        condition_dict = request.condition.model_dump()

        result = detect_anomalies(records_json, condition_dict)

        # Check if detection was successful
        if result.get("count", -1) == -1:
            error_msg = result.get("reasons", [{}])[0].get("error", "Unknown error")
            logger.error("Anomaly detection failed: %s", error_msg)
            return error(code=400, message=result.get("result", "Detection failed"), detail=error_msg)

        logger.info(
            "Anomaly detection completed: %d anomalies found", result.get("count", 0)
        )

        response = AnomalyDetectionResponse(**result)
        return success(data=response, message="Anomaly detection completed")

    except Exception as e:
        logger.exception("Unexpected error during anomaly detection")
        return error(
            code=500, message="Anomaly detection failed", detail=str(e)
        )

