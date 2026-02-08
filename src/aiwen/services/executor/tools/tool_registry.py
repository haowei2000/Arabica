"""
Tool Registry - Tool Registry

Unified management of all tool classes with discovery, registration, and query capabilities.
"""

import logging
from typing import Dict, List, Optional  # 导入其他必要的类型

from aiwen.services.executor.tools.base_tool import BaseTool, ToolExecutionMode

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Tool Registry

    Manages all available tool classes with querying by name, execution mode, tags, and more.
    """

    _registry: Dict[str, type[BaseTool]] = {}  # 修改：使用 type 替代 Type
    _instances: Dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool_class: type[BaseTool]) -> type[BaseTool]:  # 修改：使用 type 替代 Type
        """
        Register tool class

        Args:
            tool_class: Tool class (inherits from BaseTool)

        Returns:
            type[BaseTool]: Registered tool class (for decorator pattern)

        Raises:
            ValueError: If tool name is already registered
        """
        # Validate metadata
        tool_class._validate_metadata()

        tool_name = tool_class.METADATA.name

        # Check if already registered
        if tool_name in cls._registry:
            existing_class = cls._registry[tool_name]
            if existing_class != tool_class:
                raise ValueError(
                    f"Tool name '{tool_name}' is already registered by "
                    f"{existing_class.__name__}. Cannot register {tool_class.__name__}."
                )
            logger.warning(f"Tool '{tool_name}' is already registered, skipping")
            return tool_class

        # Register tool class
        cls._registry[tool_name] = tool_class
        logger.info(
            f"✓ Registered tool: {tool_name} "
            f"({tool_class.__name__}, mode={tool_class.METADATA.execution_mode.value})"
        )

        return tool_class

    @classmethod
    def unregister(cls, tool_name: str) -> bool:
        """
        Unregister tool

        Args:
            tool_name: Tool name

        Returns:
            bool: Whether unregistration was successful
        """
        if tool_name in cls._registry:
            del cls._registry[tool_name]
            if tool_name in cls._instances:
                del cls._instances[tool_name]
            logger.info(f"Unregistered tool: {tool_name}")
            return True
        return False

    @classmethod
    def get_tool_class(cls, tool_name: str) -> type[BaseTool] | None:  # 修改：使用 type 替代 Type
        """
        Get tool class

        Args:
            tool_name: Tool name

        Returns:
            type[BaseTool] | None: Tool class or None
        """
        return cls._registry.get(tool_name)

    @classmethod
    def get_tool_instance(cls, tool_name: str) -> BaseTool | None:
        """
        Get tool instance (singleton pattern)

        Args:
            tool_name: Tool name

        Returns:
            BaseTool | None: Tool instance or None
        """
        if tool_name not in cls._instances:
            tool_class = cls.get_tool_class(tool_name)
            if tool_class:
                cls._instances[tool_name] = tool_class()
            else:
                return None

        return cls._instances[tool_name]

    @classmethod
    def list_tools(
        cls,
        execution_mode: ToolExecutionMode | None = None,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> List[str]:  # 修改：使用 List 替代 list
        """
        列出Tool name

        Args:
            execution_mode: Filter by execution mode
            category: Filter by category
            enabled_only: Return only enabled tools

        Returns:
            List[str]: Tool nameList
        """
        tools = []

        for tool_name, tool_class in cls._registry.items():
            metadata = tool_class.METADATA

            # Apply filter conditions
            if execution_mode and metadata.execution_mode != execution_mode:
                continue
            if category and metadata.category != category:
                continue
            if enabled_only and not metadata.enabled:
                continue

            tools.append(tool_name)

        return sorted(tools)

    @classmethod
    def list_tool_classes(
        cls,
        execution_mode: ToolExecutionMode | None = None,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> List[type[BaseTool]]:  # 修改：使用 type 替代 Type
        """
        List tool classes

        Args:
            execution_mode: Filter by execution mode
            category: Filter by category
            enabled_only: Return only enabled tools

        Returns:
            List[type[BaseTool]]: Tool class list
        """
        tool_names = cls.list_tools(execution_mode, category, enabled_only)
        return [cls._registry[name] for name in tool_names]

    @classmethod
    def get_tools_by_tag(cls, tag: str) -> List[str]:  # 修改：使用 List 替代 list
        """
        Query tools by tag

        Args:
            tag: Tag name

        Returns:
            List[str]: Tool nameList
        """
        tools = []
        for tool_name, tool_class in cls._registry.items():
            if tag in tool_class.METADATA.tags:
                tools.append(tool_name)
        return sorted(tools)

    @classmethod
    def get_all_schemas(cls, format: str = "openai") -> List[dict]:  # 修改：使用 List 替代 list
        """
        Get schema of all enabled tools

        Args:
            format: schema Format ("openai" or "langchain")

        Returns:
            List[dict]: schema List
        """
        schemas = []

        for tool_class in cls.list_tool_classes(enabled_only=True):
            if format == "openai":
                schema = tool_class.get_json_schema()
            elif format == "langchain":
                schema = tool_class.get_langchain_schema()
            else:
                raise ValueError(f"Unsupported format: {format}")

            schemas.append(schema)

        return schemas

    @classmethod
    def get_tool_info(cls, tool_name: str) -> dict | None:
        """
        Get detailed tool information

        Args:
            tool_name: Tool name

        Returns:
            dict | None: Tool information dictionary or None
        """
        tool_class = cls.get_tool_class(tool_name)
        if not tool_class:
            return None

        metadata = tool_class.METADATA

        return {
            "name": metadata.name,
            "display_name": metadata.display_name,
            "description": metadata.description,
            "version": metadata.version,
            "author": metadata.author,
            "tags": metadata.tags,
            "category": metadata.category,
            "enabled": metadata.enabled,
            "execution_mode": metadata.execution_mode.value,
            "timeout": metadata.timeout,
            "class_name": tool_class.__name__,
            "input_schema": tool_class.InputSchema.model_json_schema(),
            "output_schema": tool_class.OutputSchema.model_json_schema(),
        }

    @classmethod
    def clear(cls) -> None:
        """Clear registry (mainly for testing)"""
        cls._registry.clear()
        cls._instances.clear()
        logger.warning("Tool registry cleared")

    @classmethod
    def get_statistics(cls) -> dict:
        """
        Get registry statistics

        Returns:
            dict: Statistics
        """
        total = len(cls._registry)
        enabled = len([t for t in cls._registry.values() if t.METADATA.enabled])

        # Group by execution mode
        mode_stats = {}
        for mode in ToolExecutionMode:
            count = len(cls.list_tools(execution_mode=mode, enabled_only=False))
            mode_stats[mode.value] = count

        # Group by category
        category_stats = {}
        for tool_class in cls._registry.values():
            category = tool_class.METADATA.category
            category_stats[category] = category_stats.get(category, 0) + 1

        return {
            "total_tools": total,
            "enabled_tools": enabled,
            "disabled_tools": total - enabled,
            "by_execution_mode": mode_stats,
            "by_category": category_stats,
        }


def register_tool(tool_class: type[BaseTool]) -> type[BaseTool]:  # 修改：使用 type 替代 Type
    """
    装饰器：Register tool class

    Example:
        ```python
        @register_tool
        class MyTool(BaseTool):
            METADATA = ToolMetadata(
                name="my_tool",
                display_name="My Tool",
                description="Tool description"
            )
            ...
        ```

    Args:
        tool_class: 工具类

    Returns:
        type[BaseTool]: Registered tool class
    """
    return ToolRegistry.register(tool_class)