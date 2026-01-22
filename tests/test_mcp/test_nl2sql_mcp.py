from unittest.mock import AsyncMock, patch

import pytest

from aiwen.mcp_router.nl2sql import (
    check_data,
    execute_sql,
    generate_chart,
    generate_sql,
    get_indicator_info,
    get_table_schema,
    query_indicator_data,
    select_indicator,
)
from aiwen.schemas.nl2sql.generate_chart import ChartRequestSchema, ChartResponseSchema
from aiwen.schemas.nl2sql.indicator_info import (
    DimensionInfoSchema,
    IndicatorInfoSchema,
    TableSchema,
)
from aiwen.schemas.nl2sql.select_indicator import IndicatorSelectResponseSchema


class TestNl2sqlMcpTools:
    """测试Nl2sql MCP工具"""

    @patch("aiwen.mcp_router.nl2sql.select_indicator_service")
    async def test_select_indicator(self, mock_service):
        """测试select_indicator工具"""
        # Arrange
        query = "最近一周的产量"
        mock_response = IndicatorSelectResponseSchema(
            indicator_name="production_last_week"
        )
        mock_service.return_value = mock_response

        # Act
        result = await select_indicator(query=query)

        # Assert
        assert isinstance(result, IndicatorSelectResponseSchema)
        assert result.indicator_name == "production_last_week"
        mock_service.assert_called_once_with(
            query=query, db=mock_service.call_args[1]["db_session"]
        )

    @patch("aiwen.mcp_router.nl2sql.get_indicator_info_service")
    async def test_get_indicator_info(self, mock_service):
        """测试get_indicator_info工具"""
        # Arrange
        indicator_name = "设备开机率"
        mock_response = IndicatorInfoSchema(
            name=indicator_name,
            sql_template="SELECT * FROM equipment",
            dimensions=[],
            related_tables=[],
        )
        mock_service.return_value = mock_response

        # Act
        result = await get_indicator_info(indicator_name=indicator_name)

        # Assert
        assert isinstance(result, IndicatorInfoSchema)
        assert result.name == indicator_name
        mock_service.assert_called_once_with(
            indicator_name=indicator_name, db=mock_service.call_args[1]["db_session"]
        )

    @patch("aiwen.mcp_router.nl2sql.generate_sql_service")
    async def test_generate_sql(self, mock_service):
        """测试generate_sql工具"""
        # Arrange
        query = "查询11月份累计报废率"
        indicator_info = IndicatorInfoSchema(
            name="累计报废率",
            sql_template="SELECT * FROM scrap_rate",
            dimensions=[],
            related_tables=[],
        )
        mock_sql_response = AsyncMock()
        mock_sql_response.sql = "SELECT * FROM scrap_rate WHERE month = 11"
        mock_service.return_value = mock_sql_response

        # Act
        result = await generate_sql(query=query, indicator_info=indicator_info)

        # Assert
        assert isinstance(result, dict)
        assert "sql" in result
        assert result["sql"] == "SELECT * FROM scrap_rate WHERE month = 11"
        mock_service.assert_called_once()

    @patch("aiwen.mcp_router.nl2sql.auto_chart")
    async def test_generate_chart(self, mock_auto_chart):
        """测试generate_chart工具"""
        # Arrange
        chart_request = ChartRequestSchema(
            data=[{"name": "A", "value": 10}], title="测试图表"
        )
        mock_auto_chart.return_value = "<div>chart html</div>"

        # Act
        result = await generate_chart(chart_request=chart_request)

        # Assert
        assert isinstance(result, ChartResponseSchema)
        assert result.chart == "<div>chart html</div>"
        mock_auto_chart.assert_called_once()

    async def test_nl2sql_prompt(self):
        """测试nl2sql_prompt函数"""
        # Act
        from aiwen.mcp_router.nl2sql import nl2sql_prompt

        prompt = nl2sql_prompt()

        # Assert
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "指标查询助手" in prompt

    @patch("aiwen.mcp_router.nl2sql.select_indicator_service")
    @patch("aiwen.mcp_router.nl2sql.get_indicator_info_service")
    @patch("aiwen.mcp_router.nl2sql.generate_sql_service")
    async def test_query_indicator_data_success(
        self, mock_generate_sql, mock_get_info, mock_select_indicator
    ):
        """测试query_indicator_data工具成功执行"""
        # Arrange
        query = "最近一周的产量是多少？"

        # Mock各个服务的返回值
        mock_select_indicator.return_value = IndicatorSelectResponseSchema(
            indicator_name="weekly_production"
        )

        mock_get_info.return_value = IndicatorInfoSchema(
            name="weekly_production",
            sql_template="SELECT * FROM production WHERE week = CURRENT_WEEK",
            dimensions=[],
            related_tables=[],
        )

        mock_sql_response = AsyncMock()
        mock_sql_response.sql = (
            "SELECT quantity FROM production WHERE week = '2023-W45'"
        )
        mock_generate_sql.return_value = mock_sql_response

        # Mock数据库执行结果
        with patch("aiwen.mcp_router.nl2sql.get_readonly_session") as mock_session:
            mock_session_ctx = AsyncMock()
            mock_session.return_value = mock_session_ctx
            mock_session_ctx.__aenter__.return_value = mock_session_ctx

            mock_result = AsyncMock()
            mock_result.fetchall.return_value = [("1000",), ("1200",)]
            mock_result.keys.return_value = ["quantity"]
            mock_session_ctx.execute.return_value = mock_result

            # Act
            result = await query_indicator_data(query=query)

            # Assert
            assert isinstance(result, dict)
            assert "indicator" in result
            assert "sql" in result
            assert "data" in result
            assert result["indicator"] == "weekly_production"
            assert (
                result["sql"]
                == "SELECT quantity FROM production WHERE week = '2023-W45'"
            )
            assert len(result["data"]) == 2

    @patch("aiwen.mcp_router.nl2sql.check_data_service")
    async def test_check_data(self, mock_check_data_service):
        """测试check_data工具"""
        # Arrange
        indicator_name = "设备效率"
        data = [{"equipment_id": "EQ001", "efficiency": 0.85}]
        mock_check_data_service.return_value = (True, ["数据质量良好"])

        # Act
        result = await check_data(indicator_name=indicator_name, data=data)

        # Assert
        assert isinstance(result, dict)
        assert "is_ok" in result
        assert "messages" in result
        assert result["is_ok"] is True
        assert result["messages"] == ["数据质量良好"]
        mock_check_data_service.assert_called_once_with(indicator_name, data)
