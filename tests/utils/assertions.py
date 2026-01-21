"""
测试断言辅助函数

提供常用的测试断言函数，简化测试代码编写
"""

from datetime import UTC
from typing import Any, Dict, List, Optional, Union
from unittest.mock import MagicMock


def assert_response_success(
    response: dict[str, Any],
    expected_code: int = 200,
    expected_message: str | None = None,
) -> None:
    """断言 API 响应成功

    Args:
        response: API 响应字典
        expected_code: 期望的状态码，默认 200
        expected_message: 期望的消息内容（可选）

    Raises:
        AssertionError: 当断言失败时

    使用示例:
        >>> response = {"code": 200, "input": "Success", "data": {...}}
        >>> assert_response_success(response)
        >>> assert_response_success(response, expected_message="Success")
    """
    assert "code" in response, "Response missing 'code' field"
    assert response["code"] == expected_code, (
        f"Expected code {expected_code}, got {response['code']}"
    )

    if expected_message:
        assert "input" in response, "Response missing 'input' field"
        assert response["input"] == expected_message, (
            f"Expected input '{expected_message}', got '{response['input']}'"
        )

    # 检查是否有 data 字段（成功响应通常应该有）
    if expected_code == 200:
        assert "data" in response, "Successful response should contain 'data' field"


def assert_response_error(
    response: dict[str, Any],
    expected_code: int,
    message_contains: str | None = None,
) -> None:
    """断言 API 响应为错误

    Args:
        response: API 响应字典
        expected_code: 期望的错误码
        message_contains: 错误消息应包含的文本（可选）

    使用示例:
        >>> response = {"code": 400, "input": "Invalid parameter"}
        >>> assert_response_error(response, 400)
        >>> assert_response_error(response, 400, message_contains="Invalid")
    """
    assert "code" in response, "Response missing 'code' field"
    assert response["code"] == expected_code, (
        f"Expected error code {expected_code}, got {response['code']}"
    )

    if message_contains:
        assert "input" in response, "Response missing 'input' field"
        assert message_contains in response["input"], (
            f"Expected input to contain '{message_contains}', "
            f"got '{response['input']}'"
        )


def assert_dict_contains(
    actual: dict[str, Any],
    expected: dict[str, Any],
    ignore_keys: list[str] | None = None,
) -> None:
    """断言字典包含期望的键值对

    Args:
        actual: 实际的字典
        expected: 期望包含的键值对
        ignore_keys: 要忽略的键列表（可选）

    使用示例:
        >>> actual = {"id": 1, "name": "test", "created_at": "2024-01-01"}
        >>> expected = {"id": 1, "name": "test"}
        >>> assert_dict_contains(actual, expected)
        >>> assert_dict_contains(actual, expected, ignore_keys=["created_at"])
    """
    ignore_keys = ignore_keys or []

    for key, expected_value in expected.items():
        if key in ignore_keys:
            continue

        assert key in actual, f"Key '{key}' not found in actual dict"

        actual_value = actual[key]

        # 递归比较嵌套字典
        if isinstance(expected_value, dict) and isinstance(actual_value, dict):
            assert_dict_contains(actual_value, expected_value, ignore_keys)
        # 比较列表
        elif isinstance(expected_value, list) and isinstance(actual_value, list):
            assert len(actual_value) == len(expected_value), (
                f"List length mismatch for key '{key}': "
                f"expected {len(expected_value)}, got {len(actual_value)}"
            )
            for i, (exp_item, act_item) in enumerate(zip(expected_value, actual_value)):
                if isinstance(exp_item, dict) and isinstance(act_item, dict):
                    assert_dict_contains(act_item, exp_item, ignore_keys)
                else:
                    assert act_item == exp_item, (
                        f"List item mismatch at index {i} for key '{key}': "
                        f"expected {exp_item}, got {act_item}"
                    )
        else:
            assert actual_value == expected_value, (
                f"Value mismatch for key '{key}': "
                f"expected {expected_value}, got {actual_value}"
            )


def assert_db_record_exists(
    mock_session: MagicMock,
    table_name: str,
    filters: dict[str, Any] | None = None,
) -> None:
    """断言数据库记录存在（在 mock 场景下）

    Args:
        mock_session: Mock 的数据库会话
        table_name: 表名
        filters: 过滤条件（可选）

    使用示例:
        >>> mock_db = MagicMock()
        >>> mock_db.execute.called = True
        >>> assert_db_record_exists(mock_db, "users")
    """
    assert mock_session.execute.called, (
        f"Expected database query for table '{table_name}' but no execute was called"
    )


def assert_list_contains_type(items: list[Any], expected_type: type) -> None:
    """断言列表中的所有元素都是指定类型

    Args:
        items: 要检查的列表
        expected_type: 期望的类型

    使用示例:
        >>> items = [1, 2, 3]
        >>> assert_list_contains_type(items, int)
    """
    assert isinstance(items, list), f"Expected list, got {type(items)}"

    for i, item in enumerate(items):
        assert isinstance(item, expected_type), (
            f"Item at index {i} has wrong type: "
            f"expected {expected_type.__name__}, got {type(item).__name__}"
        )


def assert_sql_valid(sql: str) -> None:
    """断言 SQL 语句基本有效

    Args:
        sql: SQL 语句字符串

    使用示例:
        >>> sql = "SELECT * FROM users WHERE id = 1"
        >>> assert_sql_valid(sql)
    """
    assert sql, "SQL statement is empty"
    assert isinstance(sql, str), f"SQL must be string, got {type(sql)}"

    # 基本的 SQL 关键字检查
    sql_upper = sql.upper().strip()
    valid_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "WITH"]

    has_valid_keyword = any(sql_upper.startswith(kw) for kw in valid_keywords)
    assert has_valid_keyword, f"SQL does not start with valid keyword: {valid_keywords}"

    # 检查基本的 SQL 注入风险（简单检查）
    dangerous_patterns = ["DROP TABLE", "TRUNCATE", "DELETE FROM users;"]
    for pattern in dangerous_patterns:
        assert pattern not in sql_upper, (
            f"SQL contains potentially dangerous pattern: {pattern}"
        )


def assert_mock_called_with_kwargs(
    mock_obj: MagicMock,
    method_name: str,
    **expected_kwargs: Any,
) -> None:
    """断言 Mock 对象的方法被以特定参数调用

    Args:
        mock_obj: Mock 对象
        method_name: 方法名
        **expected_kwargs: 期望的关键字参数

    使用示例:
        >>> mock_service = MagicMock()
        >>> mock_service.do_something(user_id=123, name="test")
        >>> assert_mock_called_with_kwargs(
        ...     mock_service, "do_something", user_id=123, name="test"
        ... )
    """
    method = getattr(mock_obj, method_name)
    assert method.called, f"Method '{method_name}' was not called"

    # 获取调用参数
    call_kwargs = method.call_args.kwargs if method.call_args else {}

    for key, expected_value in expected_kwargs.items():
        assert key in call_kwargs, (
            f"Expected keyword argument '{key}' not found in call"
        )
        actual_value = call_kwargs[key]
        assert actual_value == expected_value, (
            f"Argument '{key}' mismatch: expected {expected_value}, got {actual_value}"
        )


def assert_timestamp_recent(timestamp: str, max_age_seconds: int = 60) -> None:
    """断言时间戳是最近的

    Args:
        timestamp: ISO 格式的时间戳字符串
        max_age_seconds: 最大允许的年龄（秒）

    使用示例:
        >>> from datetime import datetime
        >>> now = datetime.utcnow().isoformat()
        >>> assert_timestamp_recent(now)
    """
    from datetime import datetime, timezone

    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        raise AssertionError(f"Invalid timestamp format: {timestamp}") from e

    now = datetime.now(UTC)
    age = (now - ts).total_seconds()

    assert 0 <= age <= max_age_seconds, (
        f"Timestamp is not recent: age is {age:.1f} seconds "
        f"(max allowed: {max_age_seconds})"
    )


def assert_uuid_valid(uuid_str: str) -> None:
    """断言字符串是有效的 UUID

    Args:
        uuid_str: UUID 字符串

    使用示例:
        >>> assert_uuid_valid("550e8400-e29b-41d4-a716-446655440000")
    """
    import uuid

    try:
        uuid.UUID(uuid_str)
    except (ValueError, AttributeError) as e:
        raise AssertionError(f"Invalid UUID format: {uuid_str}") from e


def assert_json_serializable(obj: Any) -> None:
    """断言对象可以被 JSON 序列化

    Args:
        obj: 要检查的对象

    使用示例:
        >>> data = {"id": 1, "name": "test"}
        >>> assert_json_serializable(data)
    """
    import json

    try:
        json.dumps(obj)
    except (TypeError, ValueError) as e:
        raise AssertionError(f"Object is not JSON serializable: {type(obj)}") from e
