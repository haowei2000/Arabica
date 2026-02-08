"""HTTP endpoints for client-side tool execution."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from aiwen.dependencies.auth import get_current_user
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.tools.execution import (
    PendingToolExecution,
    ToolResultSubmission,
)
from aiwen.services.executor.tools.client_executor import get_client_executor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolResultResponse(BaseModel):
    """Response after submitting tool result."""

    success: bool = Field(..., description="Whether submission was accepted")
    message: str = Field(..., description="Status message")
    tool_id: str = Field(..., description="Tool invocation ID")


class PendingToolResponse(BaseModel):
    """Response with pending tool info."""

    tool_id: str
    tool_name: str
    handler: str
    arguments: dict
    timeout_seconds: int
    expires_at: str


@router.post(
    "/{tool_id}/result",
    response_model=ToolResultResponse,
    status_code=status.HTTP_200_OK,
)
async def submit_tool_result(
    tool_id: str,
    result: ToolResultSubmission,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Submit the result of a client-side tool execution.

    This endpoint is called by the browser client after executing a tool
    locally (e.g., after the user selects a file via file picker).

    Args:
        tool_id: The tool invocation ID from the TOOL_CLIENT_REQUEST event
        result: The execution result
        current_user: Current authenticated user

    Returns:
        Submission status
    """
    client_executor = get_client_executor()

    # Verify the tool execution exists and belongs to this user's run
    pending = await client_executor.get_pending_execution(tool_id)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool execution {tool_id} not found or expired",
        )

    # Ensure tool_id in body matches URL
    if result.tool_id != tool_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tool ID in body does not match URL",
        )

    # Submit the result
    accepted = await client_executor.submit_result(tool_id, result)

    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Tool execution expired or already completed",
        )

    logger.info(
        f"User {current_user.id} submitted result for tool {tool_id}: "
        f"success={result.success}"
    )

    return ToolResultResponse(
        success=True,
        message="Tool result submitted successfully",
        tool_id=tool_id,
    )


@router.get(
    "/{tool_id}/pending",
    response_model=PendingToolResponse,
    status_code=status.HTTP_200_OK,
)
async def get_pending_tool(
    tool_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Get information about a pending tool execution.

    This can be used by the client to verify tool request details
    before executing.

    Args:
        tool_id: The tool invocation ID
        current_user: Current authenticated user

    Returns:
        Pending tool execution info
    """
    client_executor = get_client_executor()

    pending = await client_executor.get_pending_execution(tool_id)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool execution {tool_id} not found or expired",
        )

    return PendingToolResponse(
        tool_id=pending.tool_id,
        tool_name=pending.tool_name,
        handler=pending.handler,
        arguments=pending.arguments,
        timeout_seconds=pending.timeout_seconds,
        expires_at=pending.expires_at.isoformat(),
    )
