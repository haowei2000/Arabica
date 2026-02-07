"""
REST API endpoints for input management.

Note: Agent and App are merged into a single concept.
The app_id field in messages refers to the agent_id.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aiwen.dependencies.agents import get_conversation_crud, get_message_crud
from aiwen.dependencies.auth import get_current_user
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.conversations.message import (
    MessageCreate,
    MessageListResponse,
    MessageResponse,
    MessageUpdate,
)
from aiwen.services.conversations.conversation_crud import ConversationCRUD
from aiwen.services.conversations.message_crud import MessageCRUD

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post(
    "/create", response_model=MessageResponse, status_code=status.HTTP_201_CREATED
)
async def create_message(
    data: MessageCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: MessageCRUD = Depends(get_message_crud),
):
    """
    Create a new input.

    Args:
        data: Message creation data
        current_user: Current authenticated user
        crud: Message CRUD service

    Returns:
        Created input
    """
    # Set from_account_id to current user if not provided
    if not data.from_account_id:
        data.from_account_id = current_user.id
    message = await crud.create(**data.model_dump())
    return message


@router.get("/{message_id}/get", response_model=MessageResponse)
async def get_message(
    message_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: MessageCRUD = Depends(get_message_crud),
):
    """
    Get input by ID.

    Args:
        message_id: The input ID
        current_user: Current authenticated user
        crud: Message CRUD service

    Returns:
        Message details

    Raises:
        HTTPException: If input not found or not authorized
    """
    message = await crud.get_by_id(message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found",
        )
    # Check ownership
    if message.from_account_id and str(message.from_account_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this message",
        )
    return message


@router.post("/{message_id}/update", response_model=MessageResponse)
async def update_message(
    message_id: str,
    data: MessageUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: MessageCRUD = Depends(get_message_crud),
):
    """
    Update an existing input.

    Args:
        message_id: The input ID
        data: Update data
        current_user: Current authenticated user
        crud: Message CRUD service

    Returns:
        Updated input

    Raises:
        HTTPException: If input not found or not authorized
    """
    # First check ownership
    existing = await crud.get_by_id(message_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found",
        )
    if existing.from_account_id and str(existing.from_account_id) != str(
        current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this message",
        )

    message = await crud.update(message_id, **data.model_dump())
    return message


@router.get("/query", response_model=MessageListResponse)
async def query_messages(
    conversation_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    app_id: str | None = Query(
        None, description="Filter by agent ID (app_id = agent_id)"
    ),
    msg_status: str | None = Query(
        None, alias="status", description="Filter by status"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    crud: MessageCRUD = Depends(get_message_crud),
    conversation_crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    Query messages by conversation_id and other optional parameters.

    Note: app_id refers to the agent_id (agent and app are merged concepts).
    Only returns messages from conversations belonging to the current user.

    Args:
        conversation_id: Filter by conversation ID (required)
        app_id: Filter by agent ID
        msg_status: Filter by status
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        crud: Message CRUD service
        conversation_crud: Conversation CRUD service

    Returns:
        Paginated list of messages
    """
    # First verify the conversation belongs to the current user
    conversation = await conversation_crud.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    if conversation.account_id and str(conversation.account_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access messages from this conversation",
        )

    skip = (page - 1) * page_size
    items, total = await crud.list(
        conversation_id=conversation_id,
        app_id=app_id,
        status=msg_status,
        skip=skip,
        limit=page_size,
    )
    return MessageListResponse(
        total=total,
        items=items,  # ty:ignore[invalid-argument-type]
        page=page,
        page_size=page_size,
    )
