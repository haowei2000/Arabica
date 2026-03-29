from unittest.mock import patch

from structure.schemas.nl2sql.indicator_info import (
    DimensionInfoSchema,
    IndicatorInfoSchema,
    TableSchema,
)
from fastapi.testclient import TestClient
import pytest

from structure.app import app


@pytest.fixture
def test_client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sample_indicator_name():
    """提供一个示例指标名称"""
    return "设备开机率（生产）"


@pytest.fixture
def mock_indicator_info():
    """创建模拟的指标信息响应"""
    return IndicatorInfoSchema(
        name="设备开机率（生产）",
        sql_template="SELECT * FROM equipment_runtime WHERE status = 'running'",
        dimensions=[DimensionInfoSchema(name="时间", code="SJ", alias="runtime_time")],
        related_tables=[TableSchema(table_name="equipment_runtime", schema_name=None)],
    )


class TestGetIndicatorInfoService:
    """测试指标信息服务"""

    @patch("structure.services.nl2sql.get_indicator_info.get_indicator_info_service")
    def test_get_indicator_info_success(
        self, mock_service, sample_indicator_name, mock_indicator_info
    ):
        """测试成功获取指标信息的情况"""
        # Arrange
        from structure.extensions.database import get_readonly_session

        mock_service.return_value = mock_indicator_info

        # Act
        async def run_test():
            async with get_readonly_session("mes") as db:
                result = await mock_service(sample_indicator_name, db)
                return result

        import asyncio

        result = asyncio.run(run_test())

        # Assert
        assert isinstance(result, IndicatorInfoSchema)
        assert result.name == "设备开机率（生产）"
        assert len(result.dimensions) == 1
        assert len(result.related_tables) == 1
        mock_service.assert_called_once()

    @patch("structure.services.nl2sql.get_indicator_info.get_indicator_info_service")
    def test_get_indicator_info_not_found(self, mock_service, sample_indicator_name):
        """测试指标不存在的情况"""
        # Arrange
        from structure.extensions.database import get_readonly_session

        mock_service.side_effect = ValueError("指标 设备开机率（生产） 不存在")

        # Act & Assert
        async def run_test():
            async with get_readonly_session("mes") as db:
                await mock_service(sample_indicator_name, db)

        import asyncio

        with pytest.raises(ValueError, match="指标 设备开机率（生产） 不存在"):
            asyncio.run(run_test())

        mock_service.assert_called_once()
