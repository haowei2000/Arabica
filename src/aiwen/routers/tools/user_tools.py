"""
User Tools API Router

API endpoints for managing tools (both inner and external).

External tools delegate execution to a registered InnerTool backend.
Inner tools are code-defined and synced to the database on startup.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.extensions.database import get_aiwen_db
from aiwen.registries import ToolRegistry
from aiwen.schemas.tools.tool_template import (
    TOOL_TEMPLATES,
    ToolTemplate,
    ToolTemplateListResponse,
    get_all_templates,
    get_inner_tool_templates,
)
from aiwen.schemas.tools.user_tool import (
    EXECUTION_MODE_TO_INNER_TOOL,
    UserToolCreate,
    UserToolExecutionRequest,
    UserToolExecutionResponse,
    UserToolListResponse,
    UserToolResponse,
    UserToolUpdate,
)
from aiwen.services.tools.dynamic_tool_loader import DynamicToolLoader
from aiwen.services.tools.user_tool_crud import UserToolCRUD

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["user-tools"])


def _build_user_tool_response(tool) -> UserToolResponse:
    """Build a UserToolResponse with computed fields.

    Args:
        tool: Tool model instance

    Returns:
        UserToolResponse with tool_code and inner_tool_name populated
    """
    response = UserToolResponse.model_validate(tool)
    # For external tools, derive inner_tool_name from execution_mode if not explicit
    if response.tool_type == "external" and not response.inner_tool_name:
        response.inner_tool_name = EXECUTION_MODE_TO_INNER_TOOL.get(tool.execution_mode)
    return response


# Mock authentication dependency (replace with your actual auth)
async def get_current_user_id() -> UUID:
    """Get current authenticated user ID"""
    # TODO: Replace with actual authentication
    return UUID("00000000-0000-0000-0000-000000000001")


@router.get(
    "/templates",
    response_model=ToolTemplateListResponse,
    summary="List available tool templates",
)
async def list_templates(
    execution_mode: str | None = None,
    source: str | None = None,
):
    """
    List all available tool creation templates.

    Templates provide pre-filled request bodies for creating external tools.
    Use a template as a starting point, then customize the name, URL,
    parameters, etc. to fit your use case.

    Two sources of templates are available:
    - **static**: Hand-crafted examples (HTTP GET/POST, webhook, code, etc.)
    - **inner_tool**: Auto-generated from every registered InnerTool via ``to_template()``

    Args:
        execution_mode: Optional filter by execution mode ("http", "server_run", etc.)
        source: Optional filter by template source ("static" or "inner_tool")
    """
    templates = get_all_templates(execution_mode=execution_mode, source=source)
    return ToolTemplateListResponse(templates=templates, total=len(templates))


@router.get(
    "/templates/{template_id}",
    response_model=ToolTemplate,
    summary="Get a specific tool template",
)
async def get_template(template_id: str):
    """
    Get a specific tool template by ID.

    Returns a pre-filled UserToolCreate body that can be directly
    POST-ed to ``/tools/`` (after customizing name, URL, etc.).

    Template IDs include:
    - Static templates: ``http_get_api``, ``http_post_json``, ``http_webhook``,
      ``http_rest_crud``, ``http_form_submit``, ``code_basic``
    - Dynamic templates: ``inner_<tool_name>`` for each registered InnerTool
    """
    # Check static templates first, then dynamic
    template = TOOL_TEMPLATES.get(template_id)
    if not template:
        dynamic = get_inner_tool_templates()
        template = dynamic.get(template_id)

    if not template:
        # Build a list of all available IDs for the error message
        all_ids = list(TOOL_TEMPLATES.keys()) + list(get_inner_tool_templates().keys())
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found. "
            f"Available: {', '.join(sorted(all_ids))}",
        )
    return template


@router.post(
    "/",
    response_model=UserToolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new external tool",
)
async def create_tool(
    tool_data: UserToolCreate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """
    Create a new user-defined external tool.

    External tools delegate execution to a built-in InnerTool backend:
    - **server_run**: Executes Python code (requires `code` field)
    - **http**: Makes HTTP API calls (requires `http_config` field)
    """
    # Validate configuration
    if not tool_data.inner_tool_name:
        mode = tool_data.execution_mode
        if mode == "server_run" and not tool_data.code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="execution_mode 'server_run' requires a 'code' field (or set 'inner_tool_name' for direct delegation)",
            )
        if mode == "http" and not tool_data.http_config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="execution_mode 'http' requires an 'http_config' field (or set 'inner_tool_name' for direct delegation)",
            )

    crud = UserToolCRUD(db)

    try:
        tool = await crud.create_tool(user_id, tool_data)
        return _build_user_tool_response(tool)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/",
    response_model=UserToolListResponse,
    summary="List tools",
)
async def list_tools(
    workspace_id: UUID | None = None,
    enabled_only: bool = True,
    include_public: bool = True,
    tool_type: str | None = None,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """
    List all tools for the current user.

    - Returns user's own tools, public tools, and inner (built-in) tools
    - Can be filtered by workspace, tool_type, and enabled status

    Args:
        tool_type: Optional filter - "inner" for built-in, "external" for user-defined, None for both
    """
    crud = UserToolCRUD(db)

    tools = await crud.list_user_tools(
        user_id=user_id,
        workspace_id=workspace_id,
        enabled_only=enabled_only,
        include_public=include_public,
        tool_type=tool_type,
    )

    tool_responses = [_build_user_tool_response(tool) for tool in tools]

    return UserToolListResponse(tools=tool_responses, total=len(tool_responses))


@router.get(
    "/{tool_id}",
    response_model=UserToolResponse,
    summary="Get tool by ID",
)
async def get_tool(
    tool_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """Get a specific tool by ID"""
    crud = UserToolCRUD(db)

    tool = await crud.get_tool_by_id(tool_id, user_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    return _build_user_tool_response(tool)


@router.patch(
    "/{tool_id}",
    response_model=UserToolResponse,
    summary="Update an external tool",
)
async def update_tool(
    tool_id: UUID,
    tool_data: UserToolUpdate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """Update an existing external tool (must be owner). Inner tools cannot be modified."""
    crud = UserToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type == "inner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be modified",
        )

    tool = await crud.update_tool(tool_id, user_id, tool_data)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )

    return _build_user_tool_response(tool)


@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tool",
)
async def delete_tool(
    tool_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """Delete a tool (must be owner). Inner tools cannot be deleted."""
    crud = UserToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type == "inner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be deleted",
        )

    success = await crud.delete_tool(tool_id, user_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )


@router.post(
    "/{tool_id}/toggle",
    response_model=UserToolResponse,
    summary="Enable/disable a tool",
)
async def toggle_tool(
    tool_id: UUID,
    enabled: bool,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """Enable or disable a tool. Inner tools cannot be toggled."""
    crud = UserToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type == "inner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be toggled",
        )

    tool = await crud.toggle_enabled(tool_id, user_id, enabled)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )

    return _build_user_tool_response(tool)


@router.post(
    "/load",
    summary="Load user tools into agent",
)
async def load_user_tools(
    workspace_id: UUID | None = None,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """
    Load user tools into the agent's toolset.

    This endpoint dynamically loads tools from the database and registers
    them with the ToolRegistry so they can be used by agents.
    """
    loader = DynamicToolLoader(db)

    loaded_tools = await loader.load_user_tools(user_id, workspace_id)

    return {
        "success": True,
        "message": f"Loaded {len(loaded_tools)} tools",
        "tools": loaded_tools,
    }


@router.post(
    "/{tool_id}/reload",
    summary="Reload a specific tool",
)
async def reload_tool(
    tool_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """
    Reload a specific tool.

    Useful after updating a tool to apply changes immediately.
    """
    loader = DynamicToolLoader(db)

    success = await loader.reload_tool(tool_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    return {"success": True, "message": "Tool reloaded successfully"}


@router.post(
    "/execute",
    response_model=UserToolExecutionResponse,
    summary="Execute a user tool",
)
async def execute_tool(
    request: UserToolExecutionRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_aiwen_db),
):
    """
    Execute a user tool directly.

    This endpoint is for testing purposes. In production, tools should be
    called by the agent automatically.
    """
    import time

    # Load tool if not already loaded
    loader = DynamicToolLoader(db)
    await loader.load_user_tools(user_id)

    # Get tool instance
    crud = UserToolCRUD(db)
    tool_model = await crud.get_tool_by_id(request.tool_id, user_id)

    if not tool_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    # Get tool from registry
    tool_instance = ToolRegistry.get_tool_instance(tool_model.name)
    if not tool_instance:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool not loaded in registry",
        )

    # Execute tool
    start_time = time.time()
    try:
        result = await tool_instance(**request.parameters)
        execution_time = time.time() - start_time

        return UserToolExecutionResponse(
            success=result.get("success", True),
            message=result.get("message"),
            data=result.get("data"),
            error=result.get("error"),
            execution_time=execution_time,
        )
    except Exception as e:
        execution_time = time.time() - start_time
        logger.error(f"Tool execution failed: {e}", exc_info=True)
        return UserToolExecutionResponse(
            success=False,
            error=str(e),
            execution_time=execution_time,
        )


@router.get(
    "/registry/all",
    summary="Get all registered tools",
)
async def get_registered_tools():
    """
    Get all tools currently registered in the ToolRegistry.

    This includes both built-in tools and user-defined tools.
    """
    tools = ToolRegistry.list_tools()
    tool_info = [ToolRegistry.get_tool_info(name) for name in tools]

    return {"tools": tool_info, "total": len(tools)}


@router.get(
    "/registry/schemas",
    summary="Get tool schemas for agent",
)
async def get_tool_schemas(format: str = "openai"):
    """
    Get JSON schemas of all registered tools.

    These schemas can be passed to LLM for function calling.

    - format: "openai" (default) or "langchain"
    """
    schemas = ToolRegistry.get_all_schemas(format=format)
    return {"schemas": schemas, "total": len(schemas)}
