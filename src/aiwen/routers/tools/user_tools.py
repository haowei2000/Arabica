"""
User Tools API Router

API endpoints for managing user-defined custom tools.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.dependencies.database import get_db
from aiwen.schemas.tools.user_tool import (
    UserToolCreate,
    UserToolExecutionRequest,
    UserToolExecutionResponse,
    UserToolListResponse,
    UserToolResponse,
    UserToolUpdate,
)
from aiwen.services.executor.tools.tool_registry import ToolRegistry
from aiwen.services.tools.dynamic_tool_loader import DynamicToolLoader
from aiwen.services.tools.user_tool_crud import UserToolCRUD

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["user-tools"])


# Mock authentication dependency (replace with your actual auth)
async def get_current_user_id() -> UUID:
    """Get current authenticated user ID"""
    # TODO: Replace with actual authentication
    return UUID("00000000-0000-0000-0000-000000000001")


@router.post(
    "/",
    response_model=UserToolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user tool",
)
async def create_tool(
    tool_data: UserToolCreate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new user-defined tool.

    The tool will be stored in the database and can be dynamically loaded
    into the agent's toolset.

    Example request body:
    ```json
    {
      "name": "calculator",
      "display_name": "Calculator",
      "description": "Performs basic arithmetic operations",
      "execution_mode": "server_run",
      "input_schema": {
        "type": "object",
        "properties": {
          "operation": {
            "type": "string",
            "description": "Operation: add, subtract, multiply, divide"
          },
          "a": {"type": "number", "description": "First number"},
          "b": {"type": "number", "description": "Second number"}
        },
        "required": ["operation", "a", "b"]
      },
      "code": "operations = {'add': lambda a,b: a+b, 'subtract': lambda a,b: a-b, 'multiply': lambda a,b: a*b, 'divide': lambda a,b: a/b if b!=0 else None}\\nresult = {'value': operations[input_data['operation']](input_data['a'], input_data['b'])}"
    }
    ```
    """
    crud = UserToolCRUD(db)

    try:
        tool = await crud.create_tool(user_id, tool_data)
        return UserToolResponse.model_validate(tool)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get(
    "/",
    response_model=UserToolListResponse,
    summary="List user tools",
)
async def list_tools(
    workspace_id: UUID | None = None,
    enabled_only: bool = True,
    include_public: bool = True,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """
    List all tools for the current user.

    - Returns user's own tools and optionally public tools from others
    - Can be filtered by workspace
    - Can include only enabled tools
    """
    crud = UserToolCRUD(db)

    tools = await crud.list_user_tools(
        user_id=user_id,
        workspace_id=workspace_id,
        enabled_only=enabled_only,
        include_public=include_public,
    )

    tool_responses = [UserToolResponse.model_validate(tool) for tool in tools]

    return UserToolListResponse(tools=tool_responses, total=len(tool_responses))


@router.get(
    "/{tool_id}",
    response_model=UserToolResponse,
    summary="Get tool by ID",
)
async def get_tool(
    tool_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific tool by ID"""
    crud = UserToolCRUD(db)

    tool = await crud.get_tool_by_id(tool_id, user_id)
    if not tool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")

    return UserToolResponse.model_validate(tool)


@router.patch(
    "/{tool_id}",
    response_model=UserToolResponse,
    summary="Update a tool",
)
async def update_tool(
    tool_id: UUID,
    tool_data: UserToolUpdate,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing tool (must be owner)"""
    crud = UserToolCRUD(db)

    tool = await crud.update_tool(tool_id, user_id, tool_data)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )

    return UserToolResponse.model_validate(tool)


@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tool",
)
async def delete_tool(
    tool_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Delete a tool (must be owner)"""
    crud = UserToolCRUD(db)

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
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable a tool"""
    crud = UserToolCRUD(db)

    tool = await crud.toggle_enabled(tool_id, user_id, enabled)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )

    return UserToolResponse.model_validate(tool)


@router.post(
    "/load",
    summary="Load user tools into agent",
)
async def load_user_tools(
    workspace_id: UUID | None = None,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
):
    """
    Reload a specific tool.

    Useful after updating a tool to apply changes immediately.
    """
    loader = DynamicToolLoader(db)

    success = await loader.reload_tool(tool_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")

    return {"success": True, "message": "Tool reloaded successfully"}


@router.post(
    "/execute",
    response_model=UserToolExecutionResponse,
    summary="Execute a user tool",
)
async def execute_tool(
    request: UserToolExecutionRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")

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
