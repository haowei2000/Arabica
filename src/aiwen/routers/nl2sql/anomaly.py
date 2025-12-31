#!/usr/bin/env python3
"""
Anomaly Detection Router

Provides REST API endpoints for anomaly detection functionality.
Supports two modes:
1. Auto-load conditions from database by indicator name (recommended)
2. Explicit condition specification

异常检测路由模块
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Body

from aiwen.schemas.common import ErrorResponse, SuccessResponse, error, success
from aiwen.schemas.nl2sql.anomaly_detection import (
    AnomalyDetectionByIndicatorRequest,
    AnomalyDetectionRequest,
    AnomalyDetectionResponse,
)
from aiwen.services.nl2sql.anomaly.anomaly_detection import (
    detect_anomalies,
    detect_anomalies_by_indicator,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["nl2sql-anomaly"])


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
