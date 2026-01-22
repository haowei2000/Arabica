from unittest.mock import MagicMock, patch

import pytest

from aiwen.extensions.database import (
    _ensure_registered,
    _mask_url,
    check_all_databases,
    check_database_health,
    get_base,
    get_engine,
    is_database_registered,
    list_registered_databases,
)


class TestDatabaseExtensions:
    """测试数据库扩展功能"""

    def test_mask_url_with_password(self):
        """测试隐藏URL中的密码"""
        # Arrange
        url_with_password = "postgresql://user:secret@localhost:5432/mydb"

        # Act
        masked_url = _mask_url(url_with_password)

        # Assert
        assert "secret" not in masked_url
        assert "****" in masked_url
        assert masked_url.startswith("postgresql://user:")
        assert "@localhost:5432/" in masked_url

    def test_mask_url_without_password(self):
        """测试不包含密码的URL"""
        # Arrange
        url_without_password = "postgresql://user@localhost:5432/mydb"

        # Act
        masked_url = _mask_url(url_without_password)

        # Assert
        assert masked_url == url_without_password

    @patch("aiwen.extensions.database._engines")
    def test_list_registered_databases(self, mock_engines):
        """测试列出已注册的数据库"""
        # Arrange
        mock_engines.keys.return_value = ["primary", "dify", "mes"]

        # Act
        databases = list_registered_databases()

        # Assert
        assert isinstance(databases, list)
        assert "primary" in databases
        assert "dify" in databases
        assert "mes" in databases

    @patch("aiwen.extensions.database._engines")
    def test_is_database_registered_true(self, mock_engines):
        """测试数据库已注册的情况"""
        # Arrange
        mock_engines.__contains__.return_value = True

        # Act
        result = is_database_registered("primary")

        # Assert
        assert result is True

    @patch("aiwen.extensions.database._engines")
    def test_is_database_registered_false(self, mock_engines):
        """测试数据库未注册的情况"""
        # Arrange
        mock_engines.__contains__.return_value = False

        # Act
        result = is_database_registered("nonexistent")

        # Assert
        assert result is False

    @patch("aiwen.extensions.database._bases")
    @patch("aiwen.extensions.database._engines")
    def test_get_base_success(self, mock_engines, mock_bases):
        """测试成功获取Base类"""
        # Arrange
        mock_engines.__contains__.return_value = True
        mock_base_class = MagicMock()
        mock_bases.__getitem__.return_value = mock_base_class

        # Act
        base = get_base("primary")

        # Assert
        assert base == mock_base_class

    @patch("aiwen.extensions.database._engines")
    def test_get_base_not_found(self, mock_engines):
        """测试获取不存在的Base类"""
        # Arrange
        mock_engines.__contains__.return_value = False

        # Act & Assert
        with pytest.raises(
            ValueError, match="Base for database 'nonexistent' not found"
        ):
            get_base("nonexistent")

    @patch("aiwen.extensions.database._engines")
    def test_get_engine_success(self, mock_engines):
        """测试成功获取引擎"""
        # Arrange
        mock_engine = MagicMock()
        mock_engines.__contains__.return_value = True
        mock_engines.__getitem__.return_value = mock_engine

        # Act
        engine = get_engine("primary")

        # Assert
        assert engine == mock_engine

    @patch("aiwen.extensions.database._engines")
    def test_get_engine_not_found(self, mock_engines):
        """测试获取不存在的引擎"""
        # Arrange
        mock_engines.__contains__.return_value = False

        # Act & Assert
        with pytest.raises(ValueError, match="Engine for 'nonexistent' not found"):
            get_engine("nonexistent")

    @patch("aiwen.extensions.database._engines")
    async def test_check_database_health_success(self, mock_engines):
        """测试数据库健康检查成功"""
        # Arrange
        mock_engine = MagicMock()
        mock_engines.__contains__.return_value = True
        mock_engines.__getitem__.return_value = mock_engine
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__aenter__.return_value = mock_connection
        mock_connection.execute.return_value = None
        mock_engine.pool = MagicMock()
        mock_engine.pool.size.return_value = 5
        mock_engine.pool.checkedin.return_value = 3
        mock_engine.pool.checkedout.return_value = 2
        mock_engine.pool.overflow.return_value = 0

        # Act
        result = await check_database_health("primary")

        # Assert
        assert result["status"] == "healthy"
        assert result["bind_name"] == "primary"
        assert "pool" in result
        assert result["pool"]["size"] == 5

    @patch("aiwen.extensions.database._engines")
    async def test_check_database_health_failure(self, mock_engines):
        """测试数据库健康检查失败"""
        # Arrange
        mock_engine = MagicMock()
        mock_engines.__contains__.return_value = True
        mock_engines.__getitem__.return_value = mock_engine
        mock_engine.connect.return_value.__aenter__.side_effect = Exception(
            "Connection failed"
        )

        # Act
        result = await check_database_health("primary")

        # Assert
        assert result["status"] == "unhealthy"
        assert result["bind_name"] == "primary"
        assert "error" in result

    @patch("aiwen.extensions.database._engines")
    async def test_check_all_databases(self, mock_engines):
        """测试检查所有数据库"""
        # Arrange
        mock_engines.__iter__.return_value = iter(["primary", "mes"])
        mock_engines.__contains__.return_value = True
        mock_engine = MagicMock()
        mock_engines.__getitem__.return_value = mock_engine
        mock_connection = MagicMock()
        mock_engine.connect.return_value.__aenter__.return_value = mock_connection
        mock_connection.execute.return_value = None
        mock_engine.pool = MagicMock()
        mock_engine.pool.size.return_value = 5

        # Act
        result = await check_all_databases()

        # Assert
        assert "primary" in result
        assert "mes" in result
        assert result["primary"]["status"] == "healthy"
        assert result["mes"]["status"] == "healthy"

    @patch("aiwen.extensions.database.get_settings")
    def test_ensure_registered_already_initialized(self, mock_get_settings):
        """测试数据库已经初始化的情况"""
        # Arrange
        import aiwen.extensions.database as db_module

        db_module._initialized = True

        # Act
        _ensure_registered()

        # Assert
        mock_get_settings.assert_not_called()
