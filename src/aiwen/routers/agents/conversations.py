"""
REST API endpoints for conversation management.

Note: Agent and App are merged into a single concept.
The app_id field in conversations refers to the agent_id.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aiwen.dependencies.agents import get_conversation_crud
from aiwen.schemas.agents.conversation import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
)
from aiwen.services.agents.crud.conversation_crud import ConversationCRUD

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post(
    "/", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED
)
async def create_conversation(
    data: ConversationCreate, crud: ConversationCRUD = Depends(get_conversation_crud)
):
    """
    Create a new conversation.

    Args:
        data: Conversation creation data
        crud: Conversation CRUD service

    Returns:
        Created conversation
    """
    conversation = await crud.create(data)
    return conversation


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str, crud: ConversationCRUD = Depends(get_conversation_crud)
):
    """
    Get conversation by ID with messages.

    Args:
        conversation_id: The conversation ID
        crud: Conversation CRUD service

    Returns:
        Conversation details with messages

    Raises:
        HTTPException: If conversation not found
    """
    conversation = await crud.get_by_id_with_messages(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return conversation


@router.put("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    Update an existing conversation.

    Args:
        conversation_id: The conversation ID
        data: Update data
        crud: Conversation CRUD service

    Returns:
        Updated conversation

    Raises:
        HTTPException: If conversation not found
    """
    conversation = await crud.update(conversation_id, data)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str, crud: ConversationCRUD = Depends(get_conversation_crud)
):
    """
    Soft delete a conversation.

    Args:
        conversation_id: The conversation ID
        crud: Conversation CRUD service

    Raises:
        HTTPException: If conversation not found
    """
    deleted = await crud.soft_delete(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return


@router.get("/", response_model=ConversationListResponse)
async def list_conversations(
    app_id: str | None = Query(
        None, description="Filter by agent ID (app_id = agent_id)"
    ),
    status: str | None = Query(None, description="Filter by status"),
    from_end_user_id: str | None = Query(None, description="Filter by end user ID"),
    from_account_id: str | None = Query(None, description="Filter by account ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    List conversations with filtering and pagination.

    Note: app_id refers to the agent_id (agent and app are merged concepts).

    Args:
        app_id: Filter by agent ID
        status: Filter by status
        from_end_user_id: Filter by end user ID
        from_account_id: Filter by account ID
        page: Page number (starting from 1)
        page_size: Number of items per page
        crud: Conversation CRUD service

    Returns:
        Paginated list of conversations
    """
    skip = (page - 1) * page_size
    items, total = await crud.list(
        app_id=app_id,
        status=status,
        from_end_user_id=from_end_user_id,
        from_account_id=from_account_id,
        skip=skip,
        limit=page_size,
    )
    return ConversationListResponse(
        total=total, items=items, page=page, page_size=page_size
    )


@router.get("/search/", response_model=ConversationListResponse)
async def search_conversations(
    q: str = Query(..., min_length=1, description="Search term"),
    app_id: str | None = Query(
        None, description="Filter by agent ID (app_id = agent_id)"
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    crud: ConversationCRUD = Depends(get_conversation_crud),
):
    """
    Search conversations by keyword (searches in name and summary).

    Note: app_id refers to the agent_id (agent and app are merged concepts).

    Args:
        q: Search term
        app_id: Filter by agent ID
        page: Page number (starting from 1)
        page_size: Number of items per page
        crud: Conversation CRUD service

    Returns:
        Paginated search results
    """
    skip = (page - 1) * page_size
    items, total = await crud.search(
        search_term=q, app_id=app_id, skip=skip, limit=page_size
    )
    return ConversationListResponse(
        total=total, items=items, page=page, page_size=page_size
    )
