"""REST API endpoints for knowledge base management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aiwen.dependencies.agents import get_knowledge_crud
from aiwen.dependencies.auth import get_current_user
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.knowledge.knowledge import (
    KnowledgeCreate,
    KnowledgeListResponse,
    KnowledgeResponse,
    KnowledgeUpdate,
)
from aiwen.services.knowledge.knowledge_crud import KnowledgeCRUD

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post(
    "/create", response_model=KnowledgeResponse, status_code=status.HTTP_201_CREATED
)
async def create_knowledge(
    data: KnowledgeCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[KnowledgeCRUD, Depends(get_knowledge_crud)],
):
    """
    Create a new knowledge base.

    Args:
        data: Knowledge creation data
        current_user: Current authenticated user
        crud: Knowledge CRUD service

    Returns:
        Created knowledge base
    """
    knowledge = await crud.create(data, user_id=current_user.id)
    return knowledge


@router.get("/{knowledge_id}/get", response_model=KnowledgeResponse)
async def get_knowledge(
    knowledge_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: KnowledgeCRUD = Depends(get_knowledge_crud),
):
    """
    Get knowledge base by ID.

    Args:
        knowledge_id: The knowledge base ID
        current_user: Current authenticated user
        crud: Knowledge CRUD service

    Returns:
        Knowledge base details

    Raises:
        HTTPException: If knowledge base not found or not authorized
    """
    knowledge = await crud.get_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base {knowledge_id} not found",
        )
    if str(knowledge.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this knowledge base",
        )
    return knowledge


@router.post("/{knowledge_id}/update", response_model=KnowledgeResponse)
async def update_knowledge(
    knowledge_id: str,
    data: KnowledgeUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: KnowledgeCRUD = Depends(get_knowledge_crud),
):
    """
    Update an existing knowledge base.

    Args:
        knowledge_id: The knowledge base ID
        data: Update data
        current_user: Current authenticated user
        crud: Knowledge CRUD service

    Returns:
        Updated knowledge base

    Raises:
        HTTPException: If knowledge base not found or not authorized
    """
    existing = await crud.get_by_id(knowledge_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base {knowledge_id} not found",
        )
    if str(existing.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this knowledge base",
        )

    knowledge = await crud.update(knowledge_id, data)
    return knowledge


@router.post("/{knowledge_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge(
    knowledge_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: KnowledgeCRUD = Depends(get_knowledge_crud),
):
    """
    Delete a knowledge base.

    Args:
        knowledge_id: The knowledge base ID
        current_user: Current authenticated user
        crud: Knowledge CRUD service

    Raises:
        HTTPException: If knowledge base not found or not authorized
    """
    existing = await crud.get_by_id(knowledge_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base {knowledge_id} not found",
        )
    if str(existing.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this knowledge base",
        )

    await crud.delete(knowledge_id)
    return


@router.get("/query", response_model=KnowledgeListResponse)
async def query_knowledge(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    knowledge_status: str | None = Query(
        None, alias="status", description="Filter by status"
    ),
    permission: str | None = Query(None, description="Filter by permission"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    crud: KnowledgeCRUD = Depends(get_knowledge_crud),
):
    """
    List knowledge bases with filtering and pagination.

    Only returns knowledge bases belonging to the current user.

    Args:
        knowledge_status: Filter by status
        permission: Filter by permission
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        crud: Knowledge CRUD service

    Returns:
        Paginated list of knowledge bases
    """
    skip = (page - 1) * page_size
    items, total = await crud.list(
        user_id=str(current_user.id),
        status=knowledge_status,
        permission=permission,
        skip=skip,
        limit=page_size,
    )
    return KnowledgeListResponse(
        total=total, items=items, page=page, page_size=page_size
    )


@router.get("/search", response_model=KnowledgeListResponse)
async def search_knowledge(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    q: str = Query(..., min_length=1, description="Search term"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    crud: KnowledgeCRUD = Depends(get_knowledge_crud),
):
    """
    Search knowledge bases by name or description.

    Only searches knowledge bases belonging to the current user.

    Args:
        q: Search term
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        crud: Knowledge CRUD service

    Returns:
        Paginated list of matching knowledge bases
    """
    skip = (page - 1) * page_size
    items, total = await crud.search(
        search_term=q,
        user_id=str(current_user.id),
        skip=skip,
        limit=page_size,
    )
    return KnowledgeListResponse(
        total=total, items=items, page=page, page_size=page_size
    )
