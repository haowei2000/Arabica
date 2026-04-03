"""
测试辅助函数模块

提供常用的测试辅助函数，简化测试代码编写
"""

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock
import uuid


def async_return[T](value: T) -> Coroutine[Any, Any, T]:
    """创建一个返回指定值的异步函数

    Args:
        value: 要返回的值

    Returns:
        返回该值的协程

    使用示例:
        >>> mock_service.get_data = async_return({"id": 1})
        >>> result = await mock_service.get_data()
        >>> assert result == {"id": 1}
    """

    async def _async_return():
        return value

    return _async_return()


def async_raise(exception: Exception) -> Coroutine[Any, Any, None]:
    """创建一个抛出指定异常的异步函数

    Args:
        exception: 要抛出的异常

    Returns:
        抛出异常的协程

    使用示例:
        >>> mock_service.do_something = async_raise(ValueError("Error"))
        >>> with pytest.raises(ValueError):
        ...     await mock_service.do_something()
    """

    async def _async_raise():
        raise exception

    return _async_raise()


def create_async_mock(**kwargs) -> AsyncMock:
    """创建一个配置好的 AsyncMock 对象

    Args:
        **kwargs: AsyncMock 的配置参数

    Returns:
        配置好的 AsyncMock 对象

    使用示例:
        >>> mock = create_async_mock(return_value={"data": "test"})
        >>> result = await mock()
        >>> assert result == {"data": "test"}
    """
    return AsyncMock(**kwargs)


def create_mock_session() -> AsyncMock:
    """创建一个模拟的数据库会话

    Returns:
        配置了常用方法的 AsyncMock 会话对象

    使用示例:
        >>> session = create_mock_session()
        >>> session.execute.return_value.fetchall.return_value = [("data",)]
    """
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    session.delete = MagicMock()
    session.flush = AsyncMock()

    # 配置 context manager 支持
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    return session


def create_mock_result(rows: list[tuple], keys: list[str] | None = None) -> MagicMock:
    """创建一个模拟的数据库查询结果

    Args:
        rows: 结果行数据
        keys: 列名列表（可选）

    Returns:
        模拟的查询结果对象

    使用示例:
        >>> result = create_mock_result(
        ...     rows=[("user1", 25), ("user2", 30)],
        ...     keys=["name", "age"]
        ... )
        >>> rows = result.fetchall()
        >>> assert len(rows) == 2
    """
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    result.fetchone = MagicMock(return_value=rows[0] if rows else None)
    result.scalar = MagicMock(return_value=rows[0][0] if rows and rows[0] else None)
    result.keys = MagicMock(return_value=keys or [])

    # 配置 scalars 方法
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=[row[0] for row in rows] if rows else [])
    result.scalars = MagicMock(return_value=scalars_mock)

    return result


@asynccontextmanager
async def create_mock_context(mock_obj: Any):
    """创建一个异步上下文管理器 mock

    Args:
        mock_obj: 要在上下文中返回的对象

    Yields:
        mock_obj

    使用示例:
        >>> async with create_mock_context(mock_session) as session:
        ...     result = await session.execute(query)
    """
    try:
        yield mock_obj
    finally:
        pass


def get_test_db_url(
    db_type: str = "postgresql",
    host: str = "localhost",
    port: int = 5432,
    username: str = "test_user",
    password: str = "test_password",
    database: str = "test_db",
) -> str:
    """生成测试数据库 URL

    Args:
        db_type: 数据库类型 (postgresql, mysql, sqlite)
        host: 主机地址
        port: 端口号
        username: 用户名
        password: 密码
        database: 数据库名

    Returns:
        数据库连接 URL

    使用示例:
        >>> url = get_test_db_url("postgresql")
        >>> assert "postgresql://test_user:test_password@localhost:5432/test_db" == url
    """
    if db_type == "sqlite":
        return f"sqlite:///{database}.db"
    if db_type == "mysql":
        return f"mysql://{username}:{password}@{host}:{port}/{database}"
    if db_type == "postgresql":
        return f"postgresql://{username}:{password}@{host}:{port}/{database}"
    raise ValueError(f"Unsupported database type: {db_type}")


def generate_test_uuid() -> str:
    """生成测试用的 UUID

    Returns:
        UUID 字符串
    """
    return str(uuid.uuid4())


def generate_test_uuids(count: int) -> list[str]:
    """生成多个测试用的 UUID

    Args:
        count: 要生成的 UUID 数量

    Returns:
        UUID 字符串列表
    """
    return [generate_test_uuid() for _ in range(count)]


def wait_for(
    condition: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.1,
) -> bool:
    """等待条件满足

    Args:
        condition: 条件函数
        timeout: 超时时间（秒）
        interval: 检查间隔（秒）

    Returns:
        条件是否在超时前满足

    使用示例:
        >>> mock_service.is_ready = False
        >>> # 在另一个线程中设置 is_ready = True
        >>> assert wait_for(lambda: mock_service.is_ready, timeout=2.0)
    """
    import time

    start = time.time()
    while time.time() - start < timeout:
        if condition():
            return True
        time.sleep(interval)
    return False


async def async_wait_for(
    condition: Callable[[], bool],
    timeout: float = 5.0,
    interval: float = 0.1,
) -> bool:
    """异步等待条件满足

    Args:
        condition: 条件函数
        timeout: 超时时间（秒）
        interval: 检查间隔（秒）

    Returns:
        条件是否在超时前满足
    """
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if condition():
            return True
        await asyncio.sleep(interval)
    return False


def patch_env_vars(env_vars: dict[str, str]) -> Callable:
    """装饰器：临时设置环境变量

    Args:
        env_vars: 要设置的环境变量字典

    Returns:
        装饰器函数

    使用示例:
        >>> @patch_env_vars({"DEBUG": "true", "ENV": "test"})
        ... def test_something():
        ...     assert os.environ["DEBUG"] == "true"
    """
    from functools import wraps
    import os

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            original_env = {}
            try:
                # 保存原始值并设置新值
                for key, value in env_vars.items():
                    original_env[key] = os.environ.get(key)
                    os.environ[key] = value

                return func(*args, **kwargs)
            finally:
                # 恢复原始值
                for key in env_vars:
                    if original_env[key] is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = original_env[key]

        return wrapper

    return decorator


def create_temp_file(content: str = "", suffix: str = ".txt") -> str:
    """创建临时文件

    Args:
        content: 文件内容
        suffix: 文件后缀

    Returns:
        临时文件路径

    使用示例:
        >>> filepath = create_temp_file("test content", ".json")
        >>> # 使用文件...
        >>> os.remove(filepath)  # 记得清理
    """
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with Path(path).open("w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        os.close(fd)
        raise
    return path


def load_test_data(filename: str, data_dir: str = "tests/data") -> Any:
    """从文件加载测试数据

    Args:
        filename: 文件名
        data_dir: 数据目录

    Returns:
        加载的数据

    使用示例:
        >>> data = load_test_data("sample_indicator.json")
        >>> assert "name" in data
    """
    import json

    filepath = Path(data_dir) / filename

    if not filepath.exists():
        raise FileNotFoundError(f"Test data file not found: {filepath}")

    with filepath.open(encoding="utf-8") as f:
        if filename.endswith(".json"):
            return json.load(f)
        if filename.endswith((".yaml", ".yml")):
            try:
                import yaml

                return yaml.safe_load(f)
            except ImportError as err:
                raise ImportError("PyYAML is required to load YAML files") from err
        else:
            return f.read()


def compare_dicts_ignore_keys(
    dict1: dict[str, Any],
    dict2: dict[str, Any],
    ignore_keys: list[str],
) -> bool:
    """比较两个字典，忽略指定的键

    Args:
        dict1: 第一个字典
        dict2: 第二个字典
        ignore_keys: 要忽略的键列表

    Returns:
        是否相等

    使用示例:
        >>> d1 = {"id": 1, "name": "test", "created_at": "2024-01-01"}
        >>> d2 = {"id": 1, "name": "test", "created_at": "2024-01-02"}
        >>> assert compare_dicts_ignore_keys(d1, d2, ["created_at"])
    """
    filtered_dict1 = {k: v for k, v in dict1.items() if k not in ignore_keys}
    filtered_dict2 = {k: v for k, v in dict2.items() if k not in ignore_keys}
    return filtered_dict1 == filtered_dict2


def mock_llm_response(response_text: str) -> AsyncMock:
    """创建一个模拟的 LLM 响应

    Args:
        response_text: 响应文本

    Returns:
        配置好的 AsyncMock LLM 对象

    使用示例:
        >>> llm = mock_llm_response("This is a test response")
        >>> result = await llm.ainvoke("test prompt")
        >>> assert "test response" in result
    """
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=response_text)
    llm.astream = AsyncMock()
    return llm


def mock_agent_response(structured_response: Any) -> AsyncMock:
    """创建一个模拟的 Agent 响应

    Args:
        structured_response: 结构化响应对象

    Returns:
        配置好的 AsyncMock Agent 对象

    使用示例:
        >>> from schemas import IndicatorSelectResponseSchema
        >>> response = IndicatorSelectResponseSchema(indicator_name="test")
        >>> agent = mock_agent_response(response)
        >>> result = await agent.ainvoke({})
        >>> assert result["structured_response"].indicator_name == "test"
    """
    agent = AsyncMock()
    agent.ainvoke = AsyncMock(return_value={"structured_response": structured_response})
    agent.astream = AsyncMock()
    return agent


def run_async_test(coro: Coroutine) -> Any:
    """运行异步测试

    Args:
        coro: 协程对象

    Returns:
        协程返回值

    使用示例:
        >>> async def test_function():
        ...     return "result"
        >>> result = run_async_test(test_function())
        >>> assert result == "result"
    """
    return asyncio.run(coro)


def create_test_context(**kwargs) -> dict[str, Any]:
    """创建测试上下文字典

    Args:
        **kwargs: 上下文键值对

    Returns:
        上下文字典

    使用示例:
        >>> ctx = create_test_context(user_id=123, session_id="abc")
        >>> assert ctx["user_id"] == 123
    """
    return kwargs


class MockLogger:
    """模拟日志记录器"""

    def __init__(self):
        self.logs = {
            "debug": [],
            "info": [],
            "warning": [],
            "error": [],
            "critical": [],
        }

    def debug(self, msg: str, *args, **kwargs):
        self.logs["debug"].append((msg, args, kwargs))

    def info(self, msg: str, *args, **kwargs):
        self.logs["info"].append((msg, args, kwargs))

    def warning(self, msg: str, *args, **kwargs):
        self.logs["warning"].append((msg, args, kwargs))

    def error(self, msg: str, *args, **kwargs):
        self.logs["error"].append((msg, args, kwargs))

    def critical(self, msg: str, *args, **kwargs):
        self.logs["critical"].append((msg, args, kwargs))

    def get_logs(self, level: str) -> list[tuple]:
        """获取指定级别的日志"""
        return self.logs.get(level, [])

    def has_log(self, level: str, message_contains: str) -> bool:
        """检查是否有包含指定文本的日志"""
        logs = self.get_logs(level)
        return any(message_contains in msg[0] for msg in logs)

    def clear(self):
        """清空所有日志"""
        for level in self.logs:
            self.logs[level] = []
