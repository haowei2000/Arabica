"""
简化的维度注册表系统
只记录维度名称和对应的查询函数
不记录实际的表名和字段名
"""
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class DimensionRegistry:
    """维度注册表 - 仅记录维度和查询函数的映射"""

    def __init__(self):
        """初始化注册表"""
        # 结构: {维度名: 查询函数}
        self._dimensions: dict[str, Callable] = {}
        # 结构: {维度名: 获取所有值的函数}
        self._all_values_getters: dict[str, Callable] = {}

    def register(self, dimension_name: str):
        """
        装饰器：注册一个维度的查询函数

        Args:
            dimension_name: 维度名称 (e.g., 'product', 'factory')

        Returns:
            装饰器函数

        Example:
            @registry.register('product')
            def query_product(item_id: str, field_name: str) -> Any:
                # 查询逻辑由用户自己实现
                return ...
        """

        def decorator(func: Callable) -> Callable:
            self._dimensions[dimension_name] = func
            logger.info(f"✓ 已注册维度: {dimension_name}")
            return func

        return decorator

    def register_values_getter(self, dimension_name: str):
        """
        装饰器：注册一个维度的获取所有可选值的函数

        Args:
            dimension_name: 维度名称 (e.g., 'product', 'factory')

        Returns:
            装饰器函数

        Example:
            @registry.register_values_getter('product')
            def get_all_products() -> list[str]:
                # 获取所有产品的逻辑
                return ['product1', 'product2', ...]
        """

        def decorator(func: Callable) -> Callable:
            self._all_values_getters[dimension_name] = func
            logger.info(f"✓ 已注册维度值获取函数: {dimension_name}")
            return func

        return decorator

    def query(self, dimension_name: str, item_id: str, field_name: str) -> Any:
        """
        查询指定维度的字段值

        Args:
            dimension_name: 维度名称
            item_id: 项目ID
            field_name: 字段名称

        Returns:
            查询结果

        Raises:
            ValueError: 如果维度未注册
        """
        if dimension_name not in self._dimensions:
            raise ValueError(f"维度 '{dimension_name}' 未注册")

        query_func = self._dimensions[dimension_name]
        return query_func(item_id, field_name)

    async def get_all_values_with_info(self, dimension_name: str) -> list[str]:
        """
        获取指定维度的所有可选值

        Args:
            dimension_name: 维度名称

        Returns:
            所有可选值的列表

        Raises:
            ValueError: 如果维度未注册或值获取函数未注册
        """
        if dimension_name not in self._all_values_getters:
            raise ValueError(f"维度 '{dimension_name}' 的值获取函数未注册")

        getter_func = self._all_values_getters[dimension_name]
        return getter_func()

    def unregister(self, dimension_name: str) -> None:
        """取消注册一个维度"""
        if dimension_name in self._dimensions:
            del self._dimensions[dimension_name]
            logger.info(f"✓ 已取消注册: {dimension_name}")

        if dimension_name in self._all_values_getters:
            del self._all_values_getters[dimension_name]
            logger.info(f"✓ 已取消注册值获取函数: {dimension_name}")

    def list_dimensions(self) -> list:
        """列出所有已注册的维度"""
        return list(self._dimensions.keys())

    def is_registered(self, dimension_name: str) -> bool:
        """检查维度是否已注册"""
        return dimension_name in self._dimensions


# ==================== 全局注册表实例 ====================

_global_registry = DimensionRegistry()


def get_registry() -> DimensionRegistry:
    """获取全局注册表实例"""
    return _global_registry


def register_dimension(dimension_name: str):
    """便捷装饰器：注册维度"""
    return _global_registry.register(dimension_name)


def register_values_getter(dimension_name: str):
    """便捷装饰器：注册维度的值获取函数"""
    logger.info(f"Registering values getter for dimension: {dimension_name}")
    return _global_registry.register_values_getter(dimension_name)


async def get_all_values_with_info(dimension_name: str) -> list[str]:
    """便捷函数：获取维度的所有可选值及其信息"""
    return await _global_registry.get_all_values_with_info(dimension_name)
