#!/usr/bin/env python3
"""
Task Utilities - 任务工具模块

功能描述:
    提供 Redis Stream 消息编码/解码工具函数。

作者: Claude
创建日期: 2025/12/30
版本: v1.0.0

"""
import json
from typing import Any, Dict, Union


def decode_bytes(value: bytes | str, default: str = "") -> str:
    """
    解码 bytes 为字符串

    Args:
        value: bytes 或 str 类型的值
        default: 默认值

    Returns:
        解码后的字符串
    """
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    return default


def decode_message_field(
    data: dict[bytes | str, bytes | str],
    field: str
) -> str:
    """
    从消息数据中解码指定字段

    Args:
        data: Redis Stream 消息数据
        field: 字段名

    Returns:
        解码后的字段值
    """
    # Try both bytes and string keys
    value = data.get(field.encode(), data.get(field, b""))
    return decode_bytes(value)


def parse_json_field(
    data: dict[bytes | str, bytes | str],
    field: str,
    default: Any = None
) -> Any:
    """
    从消息数据中解码并解析 JSON 字段

    Args:
        data: Redis Stream 消息数据
        field: 字段名
        default: JSON 解析失败时的默认值

    Returns:
        解析后的 Python 对象
    """
    field_value = decode_message_field(data, field)
    if not field_value:
        return default

    try:
        return json.loads(field_value)
    except (json.JSONDecodeError, TypeError):
        return default


def encode_message_data(data: dict[str, Any]) -> dict[str, str]:
    """
    编码消息数据为 Redis Stream 格式

    Args:
        data: 原始消息数据

    Returns:
        编码后的消息数据（所有值为字符串）
    """
    encoded = {}
    for key, value in data.items():
        if isinstance(value, dict) or isinstance(value, list):
            encoded[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, (int, float, bool)):
            encoded[key] = str(value)
        else:
            encoded[key] = str(value) if value is not None else ""
    return encoded


def decode_message_id(message_id: bytes | str) -> str:
    """
    解码 Redis Stream 消息 ID

    Args:
        message_id: 消息 ID（bytes 或 str）

    Returns:
        解码后的消息 ID 字符串
    """
    return decode_bytes(message_id)
