"""
REST API endpoints for conversation management.

Note: Agent and App are merged into a single concept.
The app_id field in conversations refers to the agent_id.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aiwen.dependencies.agents import get_conversation_crud
from aiwen.dependencies.auth import get_current_user
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.conversations.conversation import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
)
from aiwen.services.conversations.conversation_crud import ConversationCRUD

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post(
    "/create", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED
)
async def create_conversation(
    data: ConversationCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    Create a new conversation.

    Args:
        data: Conversation creation data
        current_user: Current authenticated user
        crud: Conversation CRUD service

    Returns:
        Created conversation
    """
    # Set from_account_id to current user if not provided
    if not data.from_account_id:
        data.from_account_id = current_user.id
    conversation = await crud.create(data)
    return conversation


@router.get("/{conversation_id}/get", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    Get conversation by ID with messages.

    Args:
        conversation_id: The conversation ID
        current_user: Current authenticated user
        crud: Conversation CRUD service

    Returns:
        Conversation details with messages

    Raises:
        HTTPException: If conversation not found or not authorized
    """
    conversation = await crud.get_by_id_with_messages(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    # Check ownership
    if conversation.account_id and str(conversation.account_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this conversation",
        )
    return conversation


@router.post("/{conversation_id}/update", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    Update an existing conversation.

    Args:
        conversation_id: The conversation ID
        data: Update data
        current_user: Current authenticated user
        crud: Conversation CRUD service

    Returns:
        Updated conversation

    Raises:
        HTTPException: If conversation not found or not authorized
    """
    # First check ownership
    existing = await crud.get_by_id(conversation_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    if existing.account_id and str(existing.account_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this conversation",
        )

    conversation = await crud.update(conversation_id, data)
    return conversation


@router.post("/{conversation_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    Soft delete a conversation.

    Args:
        conversation_id: The conversation ID
        current_user: Current authenticated user
        crud: Conversation CRUD service

    Raises:
        HTTPException: If conversation not found or not authorized
    """
    # First check ownership
    existing = await crud.get_by_id(conversation_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    if existing.account_id and str(existing.account_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this conversation",
        )

    await crud.soft_delete(conversation_id)
    return


@router.get("/query", response_model=ConversationListResponse)
async def query_conversations(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    app_id: str | None = Query(
        None, description="Filter by agent ID (app_id = agent_id)"
    ),
    conv_status: str | None = Query(
        None, alias="status", description="Filter by status"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    List conversations with filtering and pagination.

    Note: app_id refers to the agent_id (agent and app are merged concepts).
    Only returns conversations belonging to the current user.

    Args:
        app_id: Filter by agent ID
        conv_status: Filter by status
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        crud: Conversation CRUD service

    Returns:
        Paginated list of conversations
    """
    skip = (page - 1) * page_size
    # Always filter by current user's account_id
    items, total = await crud.list(
        app_id=app_id,
        status=conv_status,
        from_account_id=str(current_user.id),
        skip=skip,
        limit=page_size,
    )
    return ConversationListResponse(
        total=total,
        items=items,
        page=page,
        page_size=page_size,  # ty:ignore[invalid-argument-type]
    )
