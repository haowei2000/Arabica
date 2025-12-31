"""异常检测数据验证模块"""

import json
from typing import Any


class ValidationError(Exception):
    """数据验证错误"""

    def __init__(self, message: str, error_type: str = "验证错误"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


def parse_input_data(arg1: str, arg2: str | list) -> tuple[list[dict], dict]:
    """
    解析输入的记录数据和条件配置

    Args:
        arg1: JSON格式的记录数据字符串
        arg2: JSON格式的条件配置（字符串或列表）

    Returns:
        tuple[list[dict], dict]: (记录列表, 条件字典)

    Raises:
        ValidationError: 数据解析或验证失败
    """
    try:
        # 解析记录数据
        cleaned_arg1 = arg1.replace("'", '"').replace("\\", "")
        records = json.loads(cleaned_arg1)

        # 解析条件配置
        if not isinstance(arg2, str):
            arg2 = json.dumps(arg2[0]["records"][0])

        condition_data = json.loads(arg2)

        # 支持多种条件格式
        condition = extract_condition(condition_data)

        return records, condition

    except json.JSONDecodeError as e:
        raise ValidationError(str(e), "JSON解析失败") from e
    except (KeyError, IndexError, TypeError) as e:
        raise ValidationError(f"无法获取条件配置: {e!s}", "条件格式有误") from e


def extract_condition(condition_data: Any) -> dict:
    """
    从不同格式的条件数据中提取条件配置

    Args:
        condition_data: 条件数据（支持多种格式）

    Returns:
        dict: 条件配置字典
    """
    # 格式1: 直接是条件对象 (支持中文和英文)
    if isinstance(condition_data, dict) and ("判断模式" in condition_data or "judge_mode" in condition_data):
        return condition_data

    # 格式2: 数组包含records字段
    if isinstance(condition_data, list) and len(condition_data) > 0:
        if isinstance(condition_data[0], dict) and "records" in condition_data[0]:
            return condition_data[0]["records"][0]
        if isinstance(condition_data[0], dict):
            return condition_data[0]
        return condition_data[0]

    # 格式3: 嵌套对象
    if isinstance(condition_data, dict) and "records" in condition_data:
        return condition_data["records"][0]

    return condition_data if isinstance(condition_data, dict) else {}


def validate_records(records: list[dict]) -> None:
    """
    验证记录格式

    Args:
        records: 记录列表

    Raises:
        ValidationError: 记录格式无效
    """
    if not isinstance(records, list) or not records:
        raise ValidationError("records必须是非空列表", "输入格式有误")


def validate_threshold_condition(condition: dict) -> tuple[float, float]:
    """
    验证阈值模式的条件参数
    Supports both English and Chinese field names for backward compatibility.

    Args:
        condition: 条件配置

    Returns:
        tuple[float, float]: (下限, 上限)

    Raises:
        ValidationError: 参数验证失败
    """
    try:
        # Support both English and Chinese field names
        upper_limit = float(
            condition.get("upper_limit", condition.get("预警上限", float("inf")))
        )
        lower_limit = float(
            condition.get("lower_limit", condition.get("预警下限", float("-inf")))
        )
    except (TypeError, ValueError) as e:
        raise ValidationError(f"预警上下限格式错误: {e!s}", "参数错误") from e

    if lower_limit > upper_limit:
        raise ValidationError(f"下限({lower_limit})大于上限({upper_limit})", "参数错误")

    return lower_limit, upper_limit


def validate_dynamic_condition(condition: dict) -> tuple[int, int]:
    """
    验证动态值模式的条件参数
    Supports both English and Chinese field names for backward compatibility.

    Args:
        condition: 条件配置

    Returns:
        tuple[int, int]: (动态值类型, 比较符)

    Raises:
        ValidationError: 参数验证失败
    """
    try:
        # Support both English and Chinese field names
        dynamic_type = condition.get("dynamic_type", condition.get("动态值类型", 0))  # 0=mean, 1=median
        comparison = condition.get("comparison_operator", condition.get("比较符", 0))  # 0=not below, 1=not above

        if dynamic_type not in [0, 1]:
            raise ValidationError(f"动态值类型只能是0或1，实际值:{dynamic_type}", "参数错误")
        if comparison not in [0, 1]:
            raise ValidationError(f"比较符只能是0或1，实际值:{comparison}", "参数错误")

        return dynamic_type, comparison

    except (TypeError, ValueError) as e:
        raise ValidationError(f"动态值参数错误: {e!s}", "参数错误") from e


def validate_sufficient_data(numeric_columns: dict[str, list[float]]) -> None:
    """
    验证动态值检测所需的数据量

    Args:
        numeric_columns: 数值列数据

    Raises:
        ValidationError: 数据不足
    """
    insufficient_cols = [col for col, vals in numeric_columns.items() if len(vals) < 2]
    if insufficient_cols:
        raise ValidationError(
            f"以下列数据不足（至少需要2条）: {', '.join(insufficient_cols)}",
            "数据不足",
        )