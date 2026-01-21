"""
测试 select_indicator_service 服务

使用改进的测试工具和模式，提供全面的测试覆盖
"""

from unittest.mock import AsyncMock, patch

import pytest

# 从测试工具导入辅助函数
from tests.utils.assertions import (
    assert_dict_contains,
    assert_list_contains_type,
)
from tests.utils.builders import ApiResponseBuilder
from tests.utils.helpers import (
    create_mock_result,
    create_mock_session,
    mock_agent_response,
    mock_llm_response,
)

from aiwen.schemas.nl2sql.select_indicator import IndicatorSelectResponseSchema
from aiwen.services.nl2sql.select_indicator import select_indicator_service


@pytest.mark.unit
class TestSelectIndicatorService:
    """测试 select_indicator_service 函数"""

    @pytest.mark.asyncio
    async def test_select_indicator_success(
        self, sample_indicator_names, mock_get_llm, mock_create_agent
    ):
        """测试成功选择指标 - 使用标准流程"""
        # Arrange
        query = "最近一周的产量"
        mock_db = create_mock_session()

        # 配置数据库返回可用指标列表
        indicator_rows = [(name,) for name in sample_indicator_names]
        mock_result = create_mock_result(indicator_rows, keys=["name"])
        mock_db.execute.return_value = mock_result

        # 配置 LLM 响应
        expected_indicator = "产量统计"
        mock_response = IndicatorSelectResponseSchema(indicator_name=expected_indicator)
        mock_agent = mock_agent_response(mock_response)
        mock_create_agent.return_value = mock_agent

        # Act
        result = await select_indicator_service(query=query, db=mock_db)

        # Assert
        assert isinstance(result, IndicatorSelectResponseSchema)
        assert result.indicator_name == expected_indicator

        # 验证数据库查询被调用
        mock_db.execute.assert_called_once()

        # 验证 LLM 和 Agent 被正确调用
        mock_get_llm.assert_called_once()
        mock_create_agent.assert_called_once()
        mock_agent.ainvoke.assert_called_once()

        # 验证传递给 agent 的参数包含查询和指标列表
        call_args = mock_agent.ainvoke.call_args[0][0]
        assert "input" in call_args or isinstance(call_args, dict)

    @pytest.mark.asyncio
    async def test_select_indicator_with_specific_match(self):
        """测试选择具体匹配的指标"""
        # Arrange
        query = "设备开机率查询"
        mock_db = create_mock_session()

        # 配置数据库返回
        indicator_rows = [
            ("设备开机率（生产）",),
            ("设备开机率（维护）",),
            ("产量统计",),
        ]
        mock_result = create_mock_result(indicator_rows)
        mock_db.execute.return_value = mock_result

        # 配置 Agent 选择第一个匹配的指标
        expected_indicator = "设备开机率（生产）"
        mock_response = IndicatorSelectResponseSchema(indicator_name=expected_indicator)

        with (
            patch("aiwen.services.nl2sql.select_indicator.get_llm") as mock_get_llm,
            patch(
                "aiwen.services.nl2sql.select_indicator.create_agent"
            ) as mock_create_agent,
        ):
            mock_agent = mock_agent_response(mock_response)
            mock_create_agent.return_value = mock_agent
            mock_get_llm.return_value = AsyncMock()

            # Act
            result = await select_indicator_service(query=query, db=mock_db)

            # Assert
            assert result.indicator_name == expected_indicator
            assert "设备开机率" in result.indicator_name

    @pytest.mark.asyncio
    async def test_select_indicator_empty_query(self):
        """测试空查询字符串 - 应该抛出 ValueError"""
        # Arrange
        query = ""
        mock_db = create_mock_session()

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await select_indicator_service(query=query, db=mock_db)

        assert "查询字符串不能为空" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_select_indicator_whitespace_query(self):
        """测试只包含空白字符的查询 - 应该抛出 ValueError"""
        # Arrange
        query = "   \t\n  "
        mock_db = create_mock_session()

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await select_indicator_service(query=query, db=mock_db)

        assert "查询字符串不能为空" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_select_indicator_none_query(self):
        """测试 None 查询 - 应该抛出 ValueError"""
        # Arrange
        query = None
        mock_db = create_mock_session()

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await select_indicator_service(query=query, db=mock_db)

        assert "查询字符串不能为空" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_select_indicator_no_available_indicators(self):
        """测试没有可用指标的情况 - 应该抛出 ValueError"""
        # Arrange
        query = "最近一周的产量"
        mock_db = create_mock_session()

        # 配置数据库返回空列表
        mock_result = create_mock_result([], keys=["name"])
        mock_db.execute.return_value = mock_result

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            await select_indicator_service(query=query, db=mock_db)

        assert "没有可用的指标" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_select_indicator_llm_struct_output_failed(
        self, sample_indicator_names
    ):
        """测试 LLM 结构化输出失败的情况"""
        # Arrange
        query = "最近一周的产量"
        mock_db = create_mock_session()

        # 配置数据库返回
        indicator_rows = [(name,) for name in sample_indicator_names]
        mock_result = create_mock_result(indicator_rows)
        mock_db.execute.return_value = mock_result

        with (
            patch("aiwen.services.nl2sql.select_indicator.get_llm") as mock_get_llm,
            patch(
                "aiwen.services.nl2sql.select_indicator.create_agent"
            ) as mock_create_agent,
        ):
            # 配置 Agent 返回无效的结构化响应
            mock_agent = AsyncMock()
            mock_agent.ainvoke.return_value = {
                "structured_response": "invalid_string_response"
            }
            mock_create_agent.return_value = mock_agent
            mock_get_llm.return_value = AsyncMock()

            # Act & Assert
            with pytest.raises(ValueError) as exc_info:
                await select_indicator_service(query=query, db=mock_db)

            assert "Struct output failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_select_indicator_llm_exception(self, sample_indicator_names):
        """测试 LLM 调用异常的情况"""
        # Arrange
        query = "最近一周的产量"
        mock_db = create_mock_session()

        # 配置数据库返回
        indicator_rows = [(name,) for name in sample_indicator_names]
        mock_result = create_mock_result(indicator_rows)
        mock_db.execute.return_value = mock_result

        with patch("aiwen.services.nl2sql.select_indicator.get_llm") as mock_get_llm:
            # 配置 LLM 抛出异常
            mock_get_llm.side_effect = Exception("LLM service unavailable")

            # Act & Assert
            with pytest.raises(Exception) as exc_info:
                await select_indicator_service(query=query, db=mock_db)

            assert "LLM service unavailable" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_select_indicator_database_exception(self):
        """测试数据库查询异常的情况"""
        # Arrange
        query = "最近一周的产量"
        mock_db = create_mock_session()

        # 配置数据库抛出异常
        mock_db.execute.side_effect = Exception("Database connection failed")

        # Act & Assert
        with pytest.raises(Exception) as exc_info:
            await select_indicator_service(query=query, db=mock_db)

        assert "Database connection failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_select_indicator_with_long_query(self, sample_indicator_names):
        """测试长查询字符串"""
        # Arrange
        query = "我想查询一下最近一周内所有生产线的总产量，特别是A产线和B产线的详细数据，包括良品率和报废率"
        mock_db = create_mock_session()

        # 配置数据库返回
        indicator_rows = [(name,) for name in sample_indicator_names]
        mock_result = create_mock_result(indicator_rows)
        mock_db.execute.return_value = mock_result

        expected_indicator = "产量统计"
        mock_response = IndicatorSelectResponseSchema(indicator_name=expected_indicator)

        with (
            patch("aiwen.services.nl2sql.select_indicator.get_llm") as mock_get_llm,
            patch(
                "aiwen.services.nl2sql.select_indicator.create_agent"
            ) as mock_create_agent,
        ):
            mock_agent = mock_agent_response(mock_response)
            mock_create_agent.return_value = mock_agent
            mock_get_llm.return_value = AsyncMock()

            # Act
            result = await select_indicator_service(query=query, db=mock_db)

            # Assert
            assert result.indicator_name == expected_indicator

    @pytest.mark.asyncio
    async def test_select_indicator_with_special_characters(
        self, sample_indicator_names
    ):
        """测试包含特殊字符的查询"""
        # Arrange
        query = "查询【设备】的（开机率）是多少？"
        mock_db = create_mock_session()

        # 配置数据库返回
        indicator_rows = [(name,) for name in sample_indicator_names]
        mock_result = create_mock_result(indicator_rows)
        mock_db.execute.return_value = mock_result

        expected_indicator = "设备开机率（生产）"
        mock_response = IndicatorSelectResponseSchema(indicator_name=expected_indicator)

        with (
            patch("aiwen.services.nl2sql.select_indicator.get_llm") as mock_get_llm,
            patch(
                "aiwen.services.nl2sql.select_indicator.create_agent"
            ) as mock_create_agent,
        ):
            mock_agent = mock_agent_response(mock_response)
            mock_create_agent.return_value = mock_agent
            mock_get_llm.return_value = AsyncMock()

            # Act
            result = await select_indicator_service(query=query, db=mock_db)

            # Assert
            assert result.indicator_name == expected_indicator

    @pytest.mark.asyncio
    async def test_select_indicator_returns_correct_schema(
        self, sample_indicator_names
    ):
        """测试返回正确的 Schema 类型"""
        # Arrange
        query = "产量查询"
        mock_db = create_mock_session()

        indicator_rows = [(name,) for name in sample_indicator_names]
        mock_result = create_mock_result(indicator_rows)
        mock_db.execute.return_value = mock_result

        expected_indicator = "产量统计"
        mock_response = IndicatorSelectResponseSchema(indicator_name=expected_indicator)

        with (
            patch("aiwen.services.nl2sql.select_indicator.get_llm") as mock_get_llm,
            patch(
                "aiwen.services.nl2sql.select_indicator.create_agent"
            ) as mock_create_agent,
        ):
            mock_agent = mock_agent_response(mock_response)
            mock_create_agent.return_value = mock_agent
            mock_get_llm.return_value = AsyncMock()

            # Act
            result = await select_indicator_service(query=query, db=mock_db)

            # Assert
            assert isinstance(result, IndicatorSelectResponseSchema)
            assert hasattr(result, "indicator_name")
            assert isinstance(result.indicator_name, str)

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_select_indicator_with_many_indicators(self):
        """测试大量指标的情况（标记为慢速测试）"""
        # Arrange
        query = "设备效率"
        mock_db = create_mock_session()

        # 创建大量指标
        many_indicators = [f"指标_{i}" for i in range(1000)]
        indicator_rows = [(name,) for name in many_indicators]
        mock_result = create_mock_result(indicator_rows)
        mock_db.execute.return_value = mock_result

        expected_indicator = "指标_500"
        mock_response = IndicatorSelectResponseSchema(indicator_name=expected_indicator)

        with (
            patch("aiwen.services.nl2sql.select_indicator.get_llm") as mock_get_llm,
            patch(
                "aiwen.services.nl2sql.select_indicator.create_agent"
            ) as mock_create_agent,
        ):
            mock_agent = mock_agent_response(mock_response)
            mock_create_agent.return_value = mock_agent
            mock_get_llm.return_value = AsyncMock()

            # Act
            result = await select_indicator_service(query=query, db=mock_db)

            # Assert
            assert result.indicator_name == expected_indicator
