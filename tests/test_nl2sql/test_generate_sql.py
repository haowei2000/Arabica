from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from aiwen.app import app
from aiwen.schemas.nl2sql.generate_sql import SqlResponse
from aiwen.schemas.nl2sql.indicator_info import (
    DimensionInfoSchema,
    IndicatorInfoSchema,
    TableSchema,
)


@pytest.fixture
def test_client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sample_query():
    """提供一个示例查询"""
    return "查询11月份累计报废率"


@pytest.fixture
def sample_indicator_info():
    """提供一个示例指标信息"""
    return IndicatorInfoSchema(
        name="累计报废率",
        sql_template="""select ROUND(s1.报废数 / s2.总生产数, 2) as 累计报废率
                        from (select ifnull(sum(pumo.报废数), 0) as 报废数
                              from pv_uex_making_order pumo
                              where pumo.生产状态 = '完工') s1,
                             (select ifnull(sum(pumo.报废数 + pumo.良品数 + pumo.不良品数), 1) as 总生产数
                              from pv_uex_making_order pumo
                              where pumo.生产状态 = '完工') s2""",
        dimensions=[
            DimensionInfoSchema(
                name="时间",
                code="SJ",
                alias="pumo.完工时间"
            )
        ],
        related_tables=[
            TableSchema(
                table_name="pv_uex_making_order",
                schema_name=None
            )
        ]
    )


@pytest.fixture
def mock_sql_response():
    """创建模拟的SQL生成响应"""
    return SqlResponse(
        sql="SELECT ROUND(s1.报废数 / s2.总生产数, 2) as 累计报废率 FROM (SELECT ifnull(sum(pumo.报废数), 0) as 报废数 FROM pv_uex_making_order pumo WHERE pumo.生产状态 = '完工') s1, (SELECT ifnull(sum(pumo.报废数 + pumo.良品数 + pumo.不良品数), 1) as 总生产数 FROM pv_uex_making_order pumo WHERE pumo.生产状态 = '完工') s2"
    )


class TestGenerateSqlEndpoint:
    """测试 /api/nl2sql/generate_sql 端点"""

    @patch('aiwen.routers.nl2sql.generate_sql_service')
    def test_generate_sql_success(self, mock_service, test_client, sample_query, sample_indicator_info,
                                  mock_sql_response):
        """测试成功生成SQL的情况"""
        # Arrange
        mock_service.return_value = mock_sql_response

        # Act
        response = test_client.post(
            "/api/nl2sql/generate_sql",
            json={
                "query": sample_query,
                "indicator_info": sample_indicator_info.model_dump(),
                "additional_restriction": None
            }
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert data["message"] == "SQL generated successfully"
        assert "data" in data
        assert "sql" in data["data"]
        mock_service.assert_called_once()

    def test_generate_sql_missing_required_fields(self, test_client):
        """测试缺少必填字段的情况"""
        # Act
        response = test_client.post(
            "/api/nl2sql/generate_sql",
            json={}
        )

        # Assert
        assert response.status_code == 422  # 请求验证错误
        data = response.json()
        assert data["code"] == 422
        assert data["message"] == "请求参数验证失败"

    def test_generate_sql_missing_query(self, test_client, sample_indicator_info):
        """测试缺少查询参数的情况"""
        # Act
        response = test_client.post(
            "/api/nl2sql/generate_sql",
            json={
                "indicator_info": sample_indicator_info.model_dump()
            }
        )

        # Assert
        assert response.status_code == 422  # 请求验证错误
        data = response.json()
        assert data["code"] == 422
        assert data["message"] == "请求参数验证失败"

    @patch('aiwen.routers.nl2sql.generate_sql_service')
    def test_generate_sql_service_exception(self, mock_service, test_client, sample_query, sample_indicator_info):
        """测试服务层异常的情况"""
        # Arrange
        mock_service.side_effect = Exception("LLM服务不可用")

        # Act
        response = test_client.post(
            "/api/nl2sql/generate_sql",
            json={
                "query": sample_query,
                "indicator_info": sample_indicator_info.model_dump(),
                "additional_restriction": None
            }
        )

        # Assert
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == 500
        assert data["message"] == "Failed to generate SQL"
        mock_service.assert_called_once()
