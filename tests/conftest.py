"""
测试配置文件 - 提供全局 fixtures 和测试配置

这个文件包含了所有测试共享的 fixtures，包括：
- 环境变量配置
- 数据库 mock
- 测试客户端
- 常用测试数据
"""

import os

# ============================================================================
# 必须在任何导入之前设置环境变量
# ============================================================================
os.environ["ENV"] = "testing"
os.environ["POSTGRES__HOST"] = "localhost"
os.environ["POSTGRES__PORT"] = "5432"
os.environ["POSTGRES__USERNAME"] = "test_user"
os.environ["POSTGRES__PASSWORD"] = "test_password"
os.environ["POSTGRES__DIFY_DBNAME"] = "dify_test"
os.environ["POSTGRES__STRUCTURE_DBNAME"] = "structure_test"
os.environ["MYSQL__HOST"] = "localhost"
os.environ["MYSQL__PORT"] = "3306"
os.environ["MYSQL__USERNAME"] = "test_user"
os.environ["MYSQL__PASSWORD"] = "test_password"
os.environ["MYSQL__MES_DBNAME"] = "mes_test"
os.environ["REDIS__HOST"] = "localhost"
os.environ["REDIS__PORT"] = "6379"
os.environ["REDIS__DB"] = "1"
os.environ["AUTH__JWT_SECRET_KEY"] = "test-secret-key-for-unit-tests"
os.environ["AUTH__ADMIN_USERNAME"] = "admin"
os.environ["AUTH__ADMIN_PASSWORD"] = "test-admin-password"
os.environ["RUSTFS__HOST"] = "localhost"
os.environ["RUSTFS__PORT"] = "9000"
os.environ["RUSTFS__ACCESS_KEY"] = "test_key"
os.environ["RUSTFS__SECRET_KEY"] = "test_secret"

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# ============================================================================
# 数据库 Mock Fixtures
# ============================================================================


@pytest.fixture(scope="session", autouse=True)
def mock_database_connections():
    """全局数据库连接 mock - 避免测试时连接真实数据库"""
    with (
        patch(
            "structure.extensions.database.create_async_engine"
        ) as mock_create_engine,
        patch(
            "structure.extensions.database._ensure_registered"
        ) as mock_ensure_registered,
    ):
        mock_engine = MagicMock()
        mock_create_engine.return_value = mock_engine
        mock_ensure_registered.return_value = None

        yield


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """提供一个模拟的异步数据库会话

    使用示例:
        async def test_something(mock_db_session):
            mock_db_session.execute.return_value = mock_result
            result = await service_function(mock_db_session)
    """
    session = AsyncMock(spec=AsyncSession)

    # 配置常用方法的默认行为
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()

    return session


@pytest.fixture
def mock_db_result():
    """提供一个模拟的数据库查询结果

    使用示例:
        def test_query(mock_db_session, mock_db_result):
            mock_db_result.fetchall.return_value = [("test",)]
            mock_db_session.execute.return_value = mock_db_result
    """
    result = AsyncMock()
    result.fetchall = MagicMock(return_value=[])
    result.fetchone = MagicMock(return_value=None)
    result.scalar = MagicMock(return_value=None)
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    result.keys = MagicMock(return_value=[])

    return result


# ============================================================================
# FastAPI 测试客户端
# ============================================================================


@pytest.fixture
def test_client() -> TestClient:
    """提供 FastAPI 测试客户端

    使用示例:
        def test_endpoint(test_client):
            response = test_client.get("/api/health")
            assert response.status_code == 200
    """
    with (
        patch("structure.extensions.database._ensure_registered"),
        patch("structure.extensions.database.check_database_health"),
    ):
        from structure.app import app

        with TestClient(app) as client:
            yield client


# ============================================================================
# LLM Mock Fixtures
# ============================================================================


@pytest.fixture
def mock_llm():
    """提供一个模拟的 LLM 实例"""
    llm = AsyncMock()
    llm.ainvoke = AsyncMock()
    llm.astream = AsyncMock()
    return llm


@pytest.fixture
def mock_agent():
    """提供一个模拟的 Agent 实例"""
    agent = AsyncMock()
    agent.ainvoke = AsyncMock()
    agent.astream = AsyncMock()
    return agent


@pytest.fixture
def mock_get_llm(mock_llm):
    """Mock get_llm 函数"""
    with patch(
        "structure.services.nl2sql.select_indicator.get_llm", return_value=mock_llm
    ) as mock:
        yield mock


@pytest.fixture
def mock_create_agent(mock_agent):
    """Mock create_agent 函数"""
    with patch(
        "structure.services.nl2sql.select_indicator.create_agent",
        return_value=mock_agent,
    ) as mock:
        yield mock


# ============================================================================
# 测试数据 Fixtures - NL2SQL
# ============================================================================


@pytest.fixture
def sample_indicator_names() -> list[str]:
    """提供一组示例指标名称"""
    return [
        "产量统计",
        "设备开机率（生产）",
        "累计报废率",
        "良品率",
        "设备效率",
        "准时交付率",
        "库存周转率",
        "能源消耗",
    ]


@pytest.fixture
def sample_indicator_name() -> str:
    """提供单个示例指标名称"""
    return "设备开机率（生产）"


@pytest.fixture
def sample_query() -> str:
    """提供示例查询字符串"""
    return "查询11月份累计报废率"


@pytest.fixture
def sample_indicator_info() -> dict[str, Any]:
    """提供示例指标信息"""
    return {
        "name": "累计报废率",
        "sql_template": """
            SELECT ROUND(s1.报废数 / s2.总生产数, 2) as 累计报废率
            FROM (
                SELECT IFNULL(SUM(pumo.报废数), 0) as 报废数
                FROM pv_uex_making_order pumo
                WHERE pumo.生产状态 = '完工'
            ) s1,
            (
                SELECT IFNULL(SUM(pumo.报废数 + pumo.良品数 + pumo.不良品数), 1) as 总生产数
                FROM pv_uex_making_order pumo
                WHERE pumo.生产状态 = '完工'
            ) s2
        """,
        "dimensions": [{"name": "时间", "code": "SJ", "alias": "pumo.完工时间"}],
        "related_tables": [{"table_name": "pv_uex_making_order", "schema_name": None}],
    }


@pytest.fixture
def sample_sql_templates() -> dict[str, str]:
    """提供一组示例 SQL 模板"""
    return {
        "production_quantity": "SELECT SUM(quantity) FROM production_records WHERE date >= :start_date AND date <= :end_date",
        "scrap_rate": "SELECT SUM(scrap_qty) / SUM(total_qty) * 100 FROM production_records WHERE date >= :start_date AND date <= :end_date",
        "equipment_efficiency": "SELECT AVG(uptime) / 24 * 100 FROM equipment_logs WHERE date >= :start_date AND date <= :end_date",
        "delivery_time": "SELECT AVG(delivery_date - order_date) FROM delivery_records WHERE delivery_date >= :start_date AND delivery_date <= :end_date",
    }


@pytest.fixture
def sample_chart_data() -> list[dict[str, Any]]:
    """提供示例图表数据"""
    return [
        {"name": "一月", "value": 120},
        {"name": "二月", "value": 150},
        {"name": "三月", "value": 180},
        {"name": "四月", "value": 160},
        {"name": "五月", "value": 200},
    ]


# ============================================================================
# UUID Fixtures
# ============================================================================


@pytest.fixture
def valid_uuid() -> str:
    """生成一个有效的 UUID"""
    return str(uuid.uuid4())


@pytest.fixture
def invalid_uuid() -> str:
    """提供一个无效的 UUID 字符串"""
    return "invalid-uuid-string"


# ============================================================================
# 通用工具 Fixtures
# ============================================================================


@pytest.fixture
def mock_datetime():
    """Mock datetime 用于时间相关测试"""
    from datetime import datetime

    fixed_datetime = datetime(2024, 1, 1, 12, 0, 0)

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_datetime
        mock_dt.utcnow.return_value = fixed_datetime
        yield mock_dt


@pytest.fixture
def clean_temp_files():
    """测试后清理临时文件"""
    temp_files = []

    def register_temp_file(filepath: str):
        temp_files.append(filepath)

    yield register_temp_file

    # 清理
    for filepath in temp_files:
        p = Path(filepath)
        if p.exists():
            p.unlink()


# ============================================================================
# 辅助函数
# ============================================================================


def create_mock_db_row(data: dict[str, Any]) -> MagicMock:
    """创建一个模拟的数据库行对象

    Args:
        data: 行数据字典

    Returns:
        MagicMock: 模拟的行对象

    使用示例:
        row = create_mock_db_row({"id": 1, "name": "test"})
        assert row.id == 1
        assert row.name == "test"
    """
    row = MagicMock()
    for key, value in data.items():
        setattr(row, key, value)
    return row


def create_mock_response(status_code: int, json_data: dict[str, Any]) -> MagicMock:
    """创建一个模拟的 HTTP 响应对象

    Args:
        status_code: HTTP 状态码
        json_data: 响应 JSON 数据

    Returns:
        MagicMock: 模拟的响应对象
    """
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    return response


# ============================================================================
# Pytest Hooks
# ============================================================================


def pytest_configure(config):
    """Pytest 配置钩子"""
    # 可以在这里添加自定义配置
    pass


def pytest_collection_modifyitems(config, items):
    """修改测试收集项"""
    # 可以在这里添加自动标记等逻辑
    for item in items:
        # 为异步测试添加 asyncio 标记
        if "async" in item.nodeid:
            item.add_marker(pytest.mark.asyncio)


def pytest_addoption(parser):
    """添加自定义命令行选项"""
    parser.addoption(
        "--run-slow", action="store_true", default=False, help="Run slow tests"
    )
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests",
    )


def pytest_runtest_setup(item):
    """测试运行前的设置"""
    # 跳过慢速测试（除非指定 --run-slow）
    if "slow" in item.keywords and not item.config.getoption("--run-slow"):
        pytest.skip("Need --run-slow option to run")

    # 跳过集成测试（除非指定 --run-integration）
    if "integration" in item.keywords and not item.config.getoption(
        "--run-integration"
    ):
        pytest.skip("Need --run-integration option to run")
