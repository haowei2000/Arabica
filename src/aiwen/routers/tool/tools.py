"""REST API endpoints for Tool registry management.

Only external tools (user-defined) can be created or modified through these
endpoints. Inner tools are code-defined and managed by the system at startup.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aiwen.dependencies.agents import get_tool_crud
from aiwen.dependencies.auth import get_current_user
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.tools.tool import (
    ToolCreate,
    ToolListResponse,
    ToolResponse,
    ToolUpdate,
)
from aiwen.services.executor.tool_crud import ToolCRUD

router = APIRouter(prefix="/tools", tags=["tools"])

# Inner tool types that cannot be created or modified via API
PROTECTED_TOOL_TYPES = {"inner"}


@router.post(
    "/create", response_model=ToolResponse, status_code=status.HTTP_201_CREATED
)
async def create_tool(
    data: ToolCreate,
    tool_crud: Annotated[ToolCRUD, Depends(get_tool_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """Create a new tool in the registry.

    Args:
        data: Tool creation data
        tool_crud: Tool CRUD dependency
        current_user: Current authenticated user

    Returns:
        Created tool information

    Raises:
        HTTPException 400: If tool_code already exists
    """
    existing = await tool_crud.get_tool_by_code(data.tool_code)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tool with code '{data.tool_code}' already exists",
        )

    return await tool_crud.create_tool(data, user_id=current_user.id)


@router.get("/{tool_id}/get", response_model=ToolResponse)
async def get_tool(
    tool_id: UUID,
    tool_crud: Annotated[ToolCRUD, Depends(get_tool_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """Get tool by ID.

    Args:
        tool_id: The tool UUID
        tool_crud: Tool CRUD dependency
        current_user: Current authenticated user

    Returns:
        Tool information

    Raises:
        HTTPException 404: If tool not found
        HTTPException 403: If no access
    """
    tool = await tool_crud.get_tool(tool_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found",
        )

    # Allow access if user owns the tool or it's public
    if tool.user_id != current_user.id and not tool.is_public:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this tool",
        )

    return tool


@router.get("/list", response_model=ToolListResponse)
async def list_tools(
    tool_crud: Annotated[ToolCRUD, Depends(get_tool_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    enabled_only: bool = Query(False, description="Only return enabled tools"),
    tool_type: str | None = Query(None, description="Filter by tool type"),
):
    """List tools accessible to the current user.

    Args:
        page: Page number (starting from 1)
        page_size: Number of items per page
        enabled_only: If True, only return enabled tools
        tool_type: Filter by execution type (server/sandbox/client/async)

    Returns:
        Paginated list of tools
    """
    skip = (page - 1) * page_size

    if tool_type:
        tools, total = await tool_crud.list_tools_by_type(
            tool_type=tool_type,
            user_id=current_user.id,
            skip=skip,
            limit=page_size,
        )
    else:
        tools, total = await tool_crud.list_tools(
            user_id=current_user.id,
            skip=skip,
            limit=page_size,
            enabled_only=enabled_only,
        )

    return ToolListResponse(total=total, items=tools, page=page, page_size=page_size)


@router.post("/{tool_id}/update", response_model=ToolResponse)
async def update_tool(
    tool_id: UUID,
    data: ToolUpdate,
    tool_crud: Annotated[ToolCRUD, Depends(get_tool_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """Update an existing external tool.

    Only external tools can be updated via API. Inner tools are code-defined
    and cannot be modified.

    Args:
        tool_id: The tool UUID
        data: Update data
        tool_crud: Tool CRUD dependency
        current_user: Current authenticated user

    Returns:
        Updated tool information

    Raises:
        HTTPException 400: If trying to update an inner tool
        HTTPException 404: If tool not found
        HTTPException 403: If no permission
    """
    tool = await tool_crud.get_tool(tool_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found",
        )

    # Prevent modification of inner (code-defined) tools
    if tool.tool_type in PROTECTED_TOOL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inner tools are code-defined and cannot be modified via API",
        )

    if tool.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this tool",
        )

    updated = await tool_crud.update_tool(tool_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found",
        )

    return updated


@router.post("/{tool_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: UUID,
    tool_crud: Annotated[ToolCRUD, Depends(get_tool_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """Delete an external tool from the registry.

    Only external tools can be deleted via API. Inner tools are code-defined
    and cannot be removed.

    Args:
        tool_id: The tool UUID
        tool_crud: Tool CRUD dependency
        current_user: Current authenticated user

    Raises:
        HTTPException 400: If trying to delete an inner tool
        HTTPException 404: If tool not found
        HTTPException 403: If no permission
    """
    tool = await tool_crud.get_tool(tool_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool with id '{tool_id}' not found",
        )

    # Prevent deletion of inner (code-defined) tools
    if tool.tool_type in PROTECTED_TOOL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inner tools are code-defined and cannot be deleted via API",
        )

    if tool.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this tool",
        )

    deleted = await tool_crud.delete_tool(tool_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool with id '{tool_id}' not found",
        )

    return
