"""
REST API endpoints for input management.

Note: Agent and App are merged into a single concept.
The app_id field in messages refers to the agent_id.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aiwen.dependencies.agents import get_message_crud
from aiwen.schemas.agents.message import (
    MessageCreate,
    MessageListResponse,
    MessageResponse,
    MessageUpdate,
)
from aiwen.services.agents.crud.message_crud import MessageCRUD

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("/", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def create_message(
    data: MessageCreate,
    crud: MessageCRUD = Depends(get_message_crud)
):
    """
    Create a new input.

    Args:
        data: Message creation data
        crud: Message CRUD service

    Returns:
        Created input
    """
    message = await crud.create(data)
    return message


@router.get("/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: str,
    crud: MessageCRUD = Depends(get_message_crud)
):
    """
    Get input by ID.

    Args:
        message_id: The input ID
        crud: Message CRUD service

    Returns:
        Message details

    Raises:
        HTTPException: If input not found
    """
    message = await crud.get_by_id(message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found"
        )
    return message


@router.put("/{message_id}", response_model=MessageResponse)
async def update_message(
    message_id: str,
    data: MessageUpdate,
    crud: MessageCRUD = Depends(get_message_crud)
):
    """
    Update an existing input.

    Args:
        message_id: The input ID
        data: Update data
        crud: Message CRUD service

    Returns:
        Updated input

    Raises:
        HTTPException: If input not found
    """
    message = await crud.update(message_id, data)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found"
        )
    return message


@router.get("/", response_model=MessageListResponse)
async def list_messages(
    conversation_id: str | None = Query(None, description="Filter by conversation ID"),
    app_id: str | None = Query(None, description="Filter by agent ID (app_id = agent_id)"),
    status: str | None = Query(None, description="Filter by status"),
    from_end_user_id: str | None = Query(None, description="Filter by end user ID"),
    from_account_id: str | None = Query(None, description="Filter by account ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    crud: MessageCRUD = Depends(get_message_crud)
):
    """
    List messages with filtering and pagination.

    Note: app_id refers to the agent_id (agent and app are merged concepts).

    Args:
        conversation_id: Filter by conversation ID
        app_id: Filter by agent ID
        status: Filter by status
        from_end_user_id: Filter by end user ID
        from_account_id: Filter by account ID
        page: Page number (starting from 1)
        page_size: Number of items per page
        crud: Message CRUD service

    Returns:
        Paginated list of messages
    """
    skip = (page - 1) * page_size
    items, total = await crud.list(
        conversation_id=conversation_id,
        app_id=app_id,
        status=status,
        from_end_user_id=from_end_user_id,
        from_account_id=from_account_id,
        skip=skip,
        limit=page_size
    )
    return MessageListResponse(
        total=total,
        items=items,
        page=page,
        page_size=page_size
    )


@router.get("/search/", response_model=MessageListResponse)
async def search_messages(
    q: str = Query(..., min_length=1, description="Search term"),
    conversation_id: str | None = Query(None, description="Filter by conversation ID"),
    app_id: str | None = Query(None, description="Filter by agent ID (app_id = agent_id)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    crud: MessageCRUD = Depends(get_message_crud)
):
    """
    Search messages by keyword (searches in query and answer fields).

    Note: app_id refers to the agent_id (agent and app are merged concepts).

    Args:
        q: Search term
        conversation_id: Filter by conversation ID
        app_id: Filter by agent ID
        page: Page number (starting from 1)
        page_size: Number of items per page
        crud: Message CRUD service

    Returns:
        Paginated search results
    """
    skip = (page - 1) * page_size
    items, total = await crud.search(
        search_term=q,
        conversation_id=conversation_id,
        app_id=app_id,
        skip=skip,
        limit=page_size
    )
    return MessageListResponse(
        total=total,
        items=items,
        page=page,
        page_size=page_size
    )
