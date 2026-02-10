from unittest.mock import AsyncMock, patch
import uuid

from aiwen.schemas.nl2sql.select_indicator import IndicatorSelectResponseSchema
from fastapi.testclient import TestClient
import pytest

from aiwen.app import app


@pytest.fixture
def test_client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def valid_query():
    """提供一个有效的查询字符串"""
    return "最近一周的产量"


@pytest.fixture
def mock_indicator_response():
    """创建模拟的指标选择响应"""
    return IndicatorSelectResponseSchema(indicator_name="production_quantity_last_week")


class TestSelectIndicatorEndpoint:
    """测试 /api/nl2sql/select_indicator 端点"""

    @patch("aiwen.routers.nl2sql.select_indicator_service")
    def test_select_indicator_success(
        self, mock_service, test_client, valid_query, mock_indicator_response
    ):
        """测试成功选择指标的情况"""
        # Arrange
        mock_service.return_value = mock_indicator_response

        # Act
        response = test_client.post(
            "/api/nl2sql/select_indicator", json={"query": valid_query}
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert data["input"] == "Indicator selected successfully"
        assert "data" in data
        assert data["data"]["indicator_name"] == "production_quantity_last_week"
        mock_service.assert_called_once_with(
            query=valid_query,
            db=mock_service.call_args[1]["db_session"],  # 数据库会话参数由Depends注入
        )

    def test_select_indicator_empty_query(self, test_client):
        """测试空查询字符串的情况"""
        # Act
        response = test_client.post("/api/nl2sql/select_indicator", json={"query": ""})

        # Assert
        assert (
            response.status_code == 500
        )  # 服务层会抛出ValueError，被全局异常处理器捕获
        data = response.json()
        assert data["code"] == 500
        assert data["input"] == "Failed to select indicator"

    def test_select_indicator_missing_query(self, test_client):
        """测试缺少查询参数的情况"""
        # Act
        response = test_client.post("/api/nl2sql/select_indicator", json={})

        # Assert
        assert response.status_code == 422  # 请求验证错误
        data = response.json()
        assert data["code"] == 422
        assert data["input"] == "请求参数验证失败"

    @patch("aiwen.routers.nl2sql.select_indicator_service")
    def test_select_indicator_service_exception(
        self, mock_service, test_client, valid_query
    ):
        """测试服务层异常的情况"""
        # Arrange
        mock_service.side_effect = Exception("数据库连接失败")

        # Act
        response = test_client.post(
            "/api/nl2sql/select_indicator", json={"query": valid_query}
        )

        # Assert
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == 500
        assert data["input"] == "Failed to select indicator"
        mock_service.assert_called_once()
