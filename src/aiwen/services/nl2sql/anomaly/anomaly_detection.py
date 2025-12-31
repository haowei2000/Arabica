"""异常检测主模块

本模块提供异常检测功能，支持两种检测模式：
1. 阈值模式：基于预设的上下限进行检测
2. 动态值模式：基于统计值（均值/中位数）进行检测

使用的 Pydantic 模型定义在 aiwen.schemas.nl2sql.anomaly_detection 中。
"""

from typing import Any

from sqlalchemy import select

from aiwen.extensions.database import get_readonly_session
from aiwen.models.mes.indicator import AiIndicatorInfo
from aiwen.schemas.nl2sql.anomaly_detection import (
    AnomalyDetectionResponse,
    AnomalyReason,
    SimpleAnomaly,
)

from .anomaly_detectors import create_detector
from .anomaly_utils import extract_numeric_columns
from .anomaly_validators import ValidationError, parse_input_data, validate_records

# 导出 schema 模型供外部使用
__all__ = [
    "get_anomaly_rule",
    "load_anomaly_condition",
    "anomaly_detection",
    "detect_anomalies",
    "detect_anomalies_by_indicator",
    "AnomalyDetectionResponse",
    "AnomalyReason",
    "SimpleAnomaly",
]


async def get_anomaly_rule(indicator_name: str) -> str:
    """
    Returns the anomaly detection rule for the given indicator.

    获取给定指标的异常检测规则。

    Args:
        indicator_name: The name of the indicator.

        indicator_name: 指标的名称。
    Returns:
        The anomaly detection rule as a string.

        异常检测规则字符串。
    """
    try:
        async with get_readonly_session() as session:
            stmt = (
                select(AiIndicatorInfo.judge_mode)
                .where(AiIndicatorInfo.name == indicator_name)
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.fetchone()
            if row:
                return row[0]
            return "No rule found"
    except Exception as e:
        return f"Error retrieving rule: {e!s}"


async def load_anomaly_condition(indicator_name: str) -> dict[str, Any] | None:
    """
    Load anomaly detection condition from database by indicator name.

    根据指标名称从数据库加载异常检测条件。

    Args:
        indicator_name: Indicator name

    Returns:
        dict: Anomaly detection condition configuration or None if not found
        Format:
        {
            "judge_mode": 0 or 1,
            "upper_limit": float (for threshold mode),
            "lower_limit": float (for threshold mode),
            "dynamic_type": 0 or 1 (for dynamic mode),
            "comparison_operator": 0 or 1 (for dynamic mode)
        }
    """
    try:
        async with get_readonly_session() as session:
            stmt = (
                select(
                    AiIndicatorInfo.judge_mode,
                    AiIndicatorInfo.warning_upper,
                    AiIndicatorInfo.warning_lower,
                    AiIndicatorInfo.trend,
                    AiIndicatorInfo.trend_compare,
                )
                .where(AiIndicatorInfo.name == indicator_name)
                .where(AiIndicatorInfo.is_delete == 0)
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.fetchone()

            if not row:
                return None

            judge_mode, warning_upper, warning_lower, trend, trend_compare = row

            # Build condition based on judge_mode
            condition: dict[str, Any] = {"judge_mode": judge_mode or 0}

            if judge_mode == 0:  # Threshold mode
                # Parse limits (they are stored as strings)
                upper_limit = float("inf")
                lower_limit = float("-inf")

                if warning_upper:
                    try:
                        upper_limit = float(warning_upper)
                    except (ValueError, TypeError):
                        pass

                if warning_lower:
                    try:
                        lower_limit = float(warning_lower)
                    except (ValueError, TypeError):
                        pass

                condition["upper_limit"] = upper_limit
                condition["lower_limit"] = lower_limit

            else:  # Dynamic mode (judge_mode == 1)
                condition["dynamic_type"] = trend or 0  # 0=mean, 1=median
                condition["comparison_operator"] = trend_compare or 0  # 0=not below, 1=not above

            return condition

    except Exception as e:
        # Log error or handle appropriately
        return None


def anomaly_detection(data: list[dict], indicator_name: str) -> list:
    """
    Detects anomalies in the given SQL query.

    Args:
        data: A list of dictionaries representing the data to analyze.
        indicator_name: The name of the indicator to check for anomalies.
    Returns:
        A list of detected anomalies.
    """
    anomalies: list[dict] = []

    if not data:
        anomalies.append(
            {"indicator": indicator_name, "reason": "No data available for analysis."}
        )
        return anomalies

    for index, row in enumerate(data):
        value = row.get(indicator_name)
        if value is None:
            anomalies.append(
                {
                    "indicator": indicator_name,
                    "row_index": index,
                    "reason": "Indicator value missing.",
                }
            )

    return anomalies


def _create_error_response(error_type: str, error_message: str) -> dict[str, Any]:
    """
    创建错误响应

    Args:
        error_type: 错误类型
        error_message: 错误消息

    Returns:
        dict: 错误响应字典
    """
    return {
        "result": error_type,
        "anomalies": [],
        "reasons": [{"error": error_message}],
        "count": -1,
    }


def _create_success_response(
    anomalies: list[str], reasons: list[dict]
) -> dict[str, Any]:
    """
    创建成功响应

    Args:
        anomalies: 异常列表
        reasons: 原因列表

    Returns:
        dict: 成功响应字典
    """
    if not anomalies:
        return {
            "result": "全部正常",
            "anomalies": [],
            "reasons": [],
            "count": 0,
        }

    return {
        "result": "检测到异常",
        "anomalies": anomalies,
        "reasons": reasons,
        "count": len(anomalies),
    }


def detect_anomalies(arg1: str, arg2: str | list) -> dict[str, Any]:
    """
    根据判断模式（0=阈值, 1=动态值）对记录进行预警判断

    Args:
        arg1: JSON格式的记录数据字符串
        arg2: JSON格式的条件配置（字符串或列表）

    Returns:
        dict: 包含检测结果的字典
            - result: 检测结果描述
            - anomalies: 异常列表
            - reasons: 异常原因列表
            - count: 异常数量
    """
    try:
        # 解析和验证输入数据
        records, condition = parse_input_data(arg1, arg2)
        validate_records(records)

        # 提取数值列
        numeric_columns = extract_numeric_columns(records)
        if not numeric_columns:
            return _create_error_response("无有效数据", "未找到任何有效的数值列")

        # 创建并执行检测器
        mode = condition.get("judge_mode", condition.get("判断模式", 0))
        detector = create_detector(mode, records, numeric_columns)
        anomalies, reasons = detector.detect(condition)

        # 检查是否有错误
        if reasons and "error" in reasons[0]:
            return _create_error_response("参数错误", reasons[0]["error"])

        # 返回成功响应
        return _create_success_response(anomalies, reasons)

    except ValidationError as e:
        return _create_error_response(e.error_type, e.message)
    except ValueError as e:
        return _create_error_response("参数错误", str(e))


async def detect_anomalies_by_indicator(
    data_result: list[dict[str, Any]], indicator_name: str
) -> dict[str, Any]:
    """
    Detect anomalies by loading conditions from database using indicator name.

    通过指标名称从数据库加载条件并进行异常检测。

    Args:
        data_result: Query result data to analyze
        indicator_name: Indicator name to load conditions for

    Returns:
        dict: Detection response
            - result: Detection result description
            - anomalies: List of anomalies
            - reasons: List of anomaly reasons
            - count: Number of anomalies
    """
    try:
        # Validate input
        if not data_result:
            return _create_error_response("Input error", "Data result cannot be empty")

        if not isinstance(data_result, list):
            return _create_error_response("Input error", "Data result must be a list")

        if not all(isinstance(record, dict) for record in data_result):
            return _create_error_response("Input error", "All records must be dictionaries")

        # Load anomaly condition from database
        condition = await load_anomaly_condition(indicator_name)

        if condition is None:
            return _create_error_response(
                "Configuration not found",
                f"No anomaly detection configuration found for indicator: {indicator_name}",
            )

        # Extract numeric columns
        numeric_columns = extract_numeric_columns(data_result)
        if not numeric_columns:
            return _create_error_response("No valid data", "No valid numeric columns found")

        # Create and execute detector
        mode = condition.get("judge_mode", 0)
        detector = create_detector(mode, data_result, numeric_columns)
        anomalies, reasons = detector.detect(condition)

        # Check for errors
        if reasons and "error" in reasons[0]:
            return _create_error_response("Parameter error", reasons[0]["error"])

        # Return success response
        return _create_success_response(anomalies, reasons)

    except Exception as e:
        return _create_error_response("Processing error", str(e))
