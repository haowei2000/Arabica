"""
Dynamic Tool Loader

Loads user-defined tools from database and creates tool classes dynamically.
"""

import logging
from typing import Any
from uuid import UUID

from pydantic import Field, create_model
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.agents.tool import Tool
from aiwen.services.tools.base_tool import (
    CeleryConfig,
    ClientConfig,
    ContainerConfig,
    ExternalTool,
    HTTPConfig,
    ToolExecutionMode,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)
from aiwen.services.tools.tool_registry import ToolRegistry
from aiwen.services.tools.user_tool_crud import UserToolCRUD

logger = logging.getLogger(__name__)


class DynamicToolLoader:
    """
    Dynamically load, register, and manage external tools based on user-defined configurations.

    The class allows dynamic creation of Pydantic input schemas and tool implementations to
    register tools into a system. It leverages user and database configurations to determine
    tool behavior, input validation, and execution logic. This facilitates extending the
    system's capabilities with minimal hardcoding. Additionally, tools can be unloaded as
    needed to support dynamic management of the tool lifecycle.

    Attributes:
        db (AsyncSession): The database session used for retrieving and updating tool data.
        crud (UserToolCRUD): Instance of UserToolCRUD for operations on user tools.
    """

    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.crud = UserToolCRUD(db_session)
        self._loaded_tools: dict[UUID, type[ExternalTool]] = {}

    def _create_input_schema(
        self, tool_name: str, schema_dict: dict[str, Any]
    ) -> type[ToolInputSchema]:
        """
        Create a Pydantic model from JSON Schema dictionary

        Args:
            tool_name: Tool name
            schema_dict: JSON Schema dictionary

        Returns:
            type[ToolInputSchema]: Dynamically created Pydantic model
        """
        # Extract properties from schema
        properties = schema_dict.get("properties", {})
        required = schema_dict.get("required", [])

        # Build field definitions
        fields = {}
        for field_name, field_info in properties.items():
            field_type = field_info.get("type", "string")
            field_desc = field_info.get("description", "")
            field_default = field_info.get("default")

            # Map JSON Schema types to Python types
            type_mapping = {
                "string": str,
                "integer": int,
                "number": float,
                "boolean": bool,
                "array": list,
                "object": dict,
            }

            python_type = type_mapping.get(field_type, Any)

            # Create field with or without default
            if field_name in required:
                fields[field_name] = (python_type, Field(..., description=field_desc))
            else:
                default_value = field_default if field_default is not None else None
                fields[field_name] = (python_type, Field(default=default_value, description=field_desc))

        # Create dynamic model
        model_name = f"{tool_name}InputSchema"
        return create_model(model_name, __base__=ToolInputSchema, **fields)  # type: ignore

    def _create_tool_class(self, user_tool: Tool) -> type[ExternalTool]:
        """
        Create an ExternalTool subclass dynamically from UserTool model

        The created ExternalTool delegates execution to a registered InnerTool:
        - SERVER_RUN with code → delegates to "code_execution" InnerTool
        - HTTP → delegates to "http_request" InnerTool
        - Other modes → returns unsupported error (to be extended)

        Args:
            user_tool: UserTool instance from database

        Returns:
            type[ExternalTool]: Dynamically created ExternalTool subclass
        """
        # Create input schema
        input_schema = self._create_input_schema(user_tool.name, user_tool.input_schema)

        # Parse execution mode specific configs
        http_config = None
        container_config = None
        client_config = None
        celery_config = None

        execution_mode = ToolExecutionMode(user_tool.execution_mode)

        if execution_mode == ToolExecutionMode.HTTP and user_tool.http_config:
            http_config = HTTPConfig(**user_tool.http_config)

        elif execution_mode == ToolExecutionMode.CONTAINER_RUN and user_tool.container_config:
            container_config = ContainerConfig(**user_tool.container_config)

        elif execution_mode == ToolExecutionMode.CLIENT_RUN and user_tool.client_config:
            client_config = ClientConfig(**user_tool.client_config)

        elif execution_mode == ToolExecutionMode.CELERY_RUN and user_tool.celery_config:
            celery_config = CeleryConfig(**user_tool.celery_config)

        # Create tool metadata
        metadata = ToolMetadata(
            name=user_tool.name,
            display_name=user_tool.display_name,
            description=user_tool.description,
            version=user_tool.version,
            tags=user_tool.tags or [],
            category=user_tool.category,
            enabled=user_tool.enabled,
            execution_mode=execution_mode,
            timeout=user_tool.timeout,
            http_config=http_config,
            container_config=container_config,
            client_config=client_config,
            celery_config=celery_config,
        )

        # Determine inner tool name, parameter mapping, and extra params.
        # Priority: explicit inner_tool_name from DB > derived from execution_mode
        inner_tool_name = ""
        parameter_mapping: dict[str, str] = {}
        extra_params: dict[str, Any] = {}

        if user_tool.inner_tool_name:
            # Explicit delegation — use the stored InnerTool name and mapping
            inner_tool_name = user_tool.inner_tool_name
            parameter_mapping = user_tool.parameter_mapping or {}

        elif execution_mode == ToolExecutionMode.SERVER_RUN:
            # Default: server_run → code_execution
            inner_tool_name = "code_execution"
            extra_params = {"code": user_tool.code or ""}

        elif execution_mode == ToolExecutionMode.HTTP:
            # Default: http → http_request
            inner_tool_name = "http_request"
            if http_config:
                extra_params = {
                    "url": http_config.url,
                    "method": http_config.method,
                    "headers": http_config.headers,
                    "timeout": http_config.timeout,
                    "verify_ssl": http_config.verify_ssl,
                }

        # Store references for the before_execute hook
        tool_id = user_tool.id
        db_session = self.db

        # Override before_execute to track usage
        async def before_execute(self_tool: ExternalTool, input_data: ToolInputSchema) -> None:
            """Track tool usage before execution"""
            crud = UserToolCRUD(db_session)
            await crud.increment_usage(tool_id, auto_commit=True)

        # Create tool class dynamically
        tool_class_name = f"UserTool_{user_tool.name}_{user_tool.id.hex[:8]}"

        tool_class = type(
            tool_class_name,
            (ExternalTool,),
            {
                "METADATA": metadata,
                "InputSchema": input_schema,
                "OutputSchema": ToolOutputSchema,
                "inner_tool_name": inner_tool_name,
                "parameter_mapping": parameter_mapping,
                "extra_params": extra_params,
                "before_execute": before_execute,
                "__module__": __name__,
            },
        )

        return tool_class  # type: ignore

    async def load_user_tools(
        self, user_id: UUID, workspace_id: UUID | None = None
    ) -> list[str]:
        """
        Load all tools for a user and register them

        Args:
            user_id: User ID
            workspace_id: Optional workspace filter

        Returns:
            list[str]: List of loaded tool names
        """
        # Get user's tools from database
        tools = await self.crud.list_user_tools(
            user_id=user_id,
            workspace_id=workspace_id,
            enabled_only=True,
            include_public=True,
        )

        loaded_names = []

        for user_tool in tools:
            try:
                # Create tool class
                tool_class = self._create_tool_class(user_tool)

                # Register tool
                ToolRegistry.register(tool_class)

                # Store reference
                self._loaded_tools[user_tool.id] = tool_class

                loaded_names.append(user_tool.name)
                logger.info(f"Loaded user tool: {user_tool.name} (id={user_tool.id})")

            except Exception as e:
                logger.error(
                    f"Failed to load user tool {user_tool.name} (id={user_tool.id}): {e}",
                    exc_info=True,
                )

        return loaded_names

    async def unload_tool(self, tool_id: UUID) -> bool:
        """
        Unload a tool from registry

        Args:
            tool_id: Tool ID

        Returns:
            bool: Whether tool was unloaded
        """
        if tool_id in self._loaded_tools:
            tool_class = self._loaded_tools[tool_id]
            tool_name = tool_class.METADATA.name

            # Unregister from registry
            ToolRegistry.unregister(tool_name)

            # Remove from loaded tools
            del self._loaded_tools[tool_id]

            logger.info(f"Unloaded user tool: {tool_name} (id={tool_id})")
            return True

        return False

    async def reload_tool(self, tool_id: UUID) -> bool:
        """
        Reload a tool (unload and load again)

        Args:
            tool_id: Tool ID

        Returns:
            bool: Whether tool was reloaded
        """
        # Get tool from database
        tool = await self.crud.get_tool_by_id(tool_id)
        if not tool:
            return False

        # Unload if already loaded
        await self.unload_tool(tool_id)

        # Load again
        tools = await self.load_user_tools(tool.user_id, tool.workspace_id)
        return tool.name in tools
