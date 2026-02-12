"""
Unified Tool Management API

Provides REST endpoints for managing tools (both inner and external).

Key concepts:
- **Inner tools**: Code-defined tools registered at startup (read-only via API)
- **External tools**: User-created tools that delegate to inner tool backends
- **Tool templates**: Pre-configured examples for creating external tools

All tools run through the same unified ``execute()`` / ``__call__()`` protocol.
"""

import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.dependencies.auth import get_current_user
from aiwen.extensions.database import get_aiwen_db
from aiwen.registries import ToolRegistry
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.context.tools.tool_template import (
    TOOL_TEMPLATES,
    ToolTemplate,
    ToolTemplateListResponse,
    get_all_templates,
    get_inner_tool_templates,
)
from aiwen.schemas.context.tools.user_tool import (
    UserToolCreate,
    UserToolExecutionRequest,
    UserToolExecutionResponse,
    UserToolListResponse,
    UserToolResponse,
    UserToolUpdate,
)
from aiwen.services.context.tools.tool_crud import UserToolCRUD

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])

# Tool types that cannot be created or modified via API
PROTECTED_TOOL_TYPES = {"inner"}


def _build_user_tool_response(tool) -> UserToolResponse:
    """Build a UserToolResponse from a Tool model instance.

    Args:
        tool: Tool model instance

    Returns:
        UserToolResponse with tool_code and inner_tool_name populated
    """
    return UserToolResponse.model_validate(tool)


# ============================================================================
# Tool Templates - Pre-configured examples for creating tools
# ============================================================================


@router.get(
    "/templates",
    response_model=ToolTemplateListResponse,
    summary="List available tool templates",
)
async def list_templates(
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
        source: Optional filter by template source ("static" or "inner_tool")
    """
    templates = get_all_templates(source=source)
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


# ============================================================================
# Tool CRUD - Create, Read, Update, Delete external tools
# ============================================================================


@router.post(
    "/",
    response_model=UserToolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new external tool",
)
async def create_tool(
    tool_data: UserToolCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Create a new user-defined external tool.

    External tools delegate execution to a registered InnerTool backend.
    Specify `inner_tool_name` to choose which InnerTool to delegate to.

    Args:
        tool_data: Tool creation data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created tool information

    Raises:
        HTTPException 400: If validation fails or tool name already exists
    """
    crud = UserToolCRUD(db)

    try:
        tool = await crud.create_tool(current_user.id, tool_data)
        return _build_user_tool_response(tool)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/",
    response_model=UserToolListResponse,
    summary="List tools",
)
async def list_tools(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    workspace_id: UUID | None = None,
    enabled_only: bool = True,
    include_public: bool = True,
    tool_type: str | None = None,
):
    """
    List all tools accessible to the current user.

    Returns:
    - User's own tools (both private and public)
    - Public tools from other users
    - Inner (built-in) tools

    Args:
        workspace_id: Optional filter by workspace
        enabled_only: If True, only return enabled tools
        include_public: If True, include public tools from other users
        tool_type: Optional filter - "inner" for built-in, "external" for user-defined
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of tools with pagination info
    """
    crud = UserToolCRUD(db)

    tools = await crud.list_user_tools(
        user_id=current_user.id,
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
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Get a specific tool by ID.

    Args:
        tool_id: Tool UUID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Tool information

    Raises:
        HTTPException 404: If tool not found
        HTTPException 403: If user doesn't have access to private tool
    """
    crud = UserToolCRUD(db)

    tool = await crud.get_tool_by_id(tool_id, current_user.id)
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
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Update an existing external tool (must be owner).

    Inner tools are code-defined and cannot be modified via API.

    Args:
        tool_id: Tool UUID
        tool_data: Update data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated tool information

    Raises:
        HTTPException 400: If trying to update an inner tool
        HTTPException 404: If tool not found
        HTTPException 403: If user is not the owner
    """
    crud = UserToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type in PROTECTED_TOOL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be modified",
        )

    tool = await crud.update_tool(tool_id, current_user.id, tool_data)
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
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Delete a tool (must be owner).

    Inner tools are code-defined and cannot be deleted via API.

    Args:
        tool_id: Tool UUID
        current_user: Current authenticated user
        db: Database session

    Raises:
        HTTPException 400: If trying to delete an inner tool
        HTTPException 404: If tool not found
        HTTPException 403: If user is not the owner
    """
    crud = UserToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type in PROTECTED_TOOL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be deleted",
        )

    success = await crud.delete_tool(tool_id, current_user.id)
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
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Enable or disable a tool.

    Inner tools cannot be toggled via API.

    Args:
        tool_id: Tool UUID
        enabled: New enabled state
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated tool information

    Raises:
        HTTPException 403: If trying to toggle an inner tool
        HTTPException 404: If tool not found or no permission
    """
    crud = UserToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type in PROTECTED_TOOL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be toggled",
        )

    tool = await crud.toggle_enabled(tool_id, current_user.id, enabled)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )

    return _build_user_tool_response(tool)


# ============================================================================
# Tool Loading & Execution - Dynamic tool registration and testing
# ============================================================================


@router.post(
    "/load",
    summary="Load user tools into agent",
)
async def load_user_tools(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    workspace_id: UUID | None = None,
):
    """
    Load user tools into the agent's toolset.

    This endpoint dynamically loads tools from the database and registers
    them with the ToolRegistry so they can be used by agents.

    Args:
        workspace_id: Optional workspace filter
        current_user: Current authenticated user
        db: Database session

    Returns:
        Success message with count of loaded tools
    """
    loader = DynamicToolLoader(db)

    loaded_tools = await loader.load_user_tools(current_user.id, workspace_id)

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
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Reload a specific tool.

    Useful after updating a tool to apply changes immediately.

    Args:
        tool_id: Tool UUID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException 404: If tool not found
    """
    loader = DynamicToolLoader(db)

    success = await loader.reload_tool(tool_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    return {"success": True, "message": "Tool reloaded successfully"}





# ============================================================================
# Tool Registry - Introspection endpoints for registered tools
# ============================================================================


@router.get(
    "/registry/all",
    summary="Get all registered tools",
)
async def get_registered_tools():
    """
    Get all tools currently registered in the ToolRegistry.

    This includes both built-in tools and user-defined tools.

    Returns:
        List of tool metadata from the registry
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

    Args:
        format: Schema format - "openai" (default) or "langchain"

    Returns:
        Tool schemas in the requested format
    """
    schemas = ToolRegistry.get_all_schemas(format=format)
    return {"schemas": schemas, "total": len(schemas)}
