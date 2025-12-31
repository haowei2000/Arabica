"""异常检测工具函数模块"""

import re
from statistics import mean, median
from typing import Any


def is_time_column(column_name: str, values: list[Any]) -> bool:
    """
    智能判断是否为时间列

    Args:
        column_name: 列名
        values: 列值列表

    Returns:
        bool: 是否为时间列
    """
    column_lower = column_name.lower()

    # 关键字匹配（快速排除）
    time_keywords = [
        "时间",
        "date",
        "day",
        "dt",
        "年",
        "月",
        "日",
        "周数",
        "月份",
        "timestamp",
        "time",
        "hour",
        "minute",
        "second",
        "week",
        "month",
        "year",
    ]
    if any(keyword in column_lower for keyword in time_keywords):
        return True

    # 值内容分析（取前5个非空值检查）
    time_patterns = [
        r"^\d{4}-\d{2}-\d{2}",  # YYYY-MM-DD
        r"^\d{2}:\d{2}:\d{2}",  # HH:MM:SS
        r"^\d{4}/\d{2}/\d{2}",  # YYYY/MM/DD
        r"^\d{1,2}/\d{1,2}/\d{4}",  # M/D/YYYY
        r"^\d{10,13}$",  # Unix时间戳
    ]

    sample_values = [str(v) for v in values[:5] if v is not None]
    if sample_values:
        matched_count = sum(
            1
            for value in sample_values
            if any(re.match(pattern, str(value)) for pattern in time_patterns)
        )
        # 如果50%以上的值匹配时间格式，判定为时间列
        if matched_count / len(sample_values) >= 0.5:
            return True

    return False


def extract_numeric_columns(records: list[dict]) -> dict[str, list[float]]:
    """
    提取所有数值列，排除时间列

    Args:
        records: 记录列表

    Returns:
        dict[str, list[float]]: 列名 -> 数值列表的映射
    """
    # 第一步：收集所有可转换为数值的列
    all_numeric_columns = {}
    for record in records:
        if not isinstance(record, dict):
            continue

        for key, value in record.items():
            try:
                num_value = float(value)
                all_numeric_columns.setdefault(key, []).append(num_value)
            except (ValueError, TypeError):
                continue

    # 第二步：过滤掉时间列
    numeric_columns = {
        key: values
        for key, values in all_numeric_columns.items()
        if not is_time_column(key, values)
    }

    return numeric_columns


def calculate_statistics(values: list[float]) -> dict[str, float]:
    """
    计算列的统计信息

    Args:
        values: 数值列表

    Returns:
        dict: 包含平均值、中位数和标准差的字典
    """
    if not values:
        return {"mean": 0.0, "median": 0.0, "std": 0.0}

    col_mean = mean(values)
    col_median = median(values)
    col_std = (
        (sum((x - col_mean) ** 2 for x in values) / len(values)) ** 0.5
        if len(values) > 1
        else 0.0
    )

    return {"mean": col_mean, "median": col_median, "std": col_std}


def format_percentage(value: float) -> str:
    """格式化百分比"""
    return f"{value:.1f}%"


def format_decimal(value: float) -> str:
    """格式化小数"""
    return f"{value:.2f}"


def format_std_times(value: float) -> str:
    """格式化标准差倍数"""
    return f"{value:.2f}σ"