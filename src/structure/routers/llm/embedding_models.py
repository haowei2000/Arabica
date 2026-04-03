"""REST API endpoints for embedding model management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from structure.core.dependencies.agents import get_embedding_model_crud
from structure.core.dependencies.auth import get_current_user
from structure.schemas.auth.user import UserResponse
from structure.schemas.llm.embedding_model import (
    EmbeddingModelCreate,
    EmbeddingModelListResponse,
    EmbeddingModelResponse,
    EmbeddingModelUpdate,
)
from structure.services.llm.embedding_model_crud import EmbeddingModelCRUD

router = APIRouter(prefix="/llm/embedding-models", tags=["llm-models"])


@router.post(
    "", response_model=EmbeddingModelResponse, status_code=status.HTTP_201_CREATED
)
async def create_embedding_model(
    data: EmbeddingModelCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[EmbeddingModelCRUD, Depends(get_embedding_model_crud)],
):
    """Create a new embedding model configuration."""
    return await crud.create(data, user_id=current_user.id)


@router.get("", response_model=EmbeddingModelListResponse)
async def list_embedding_models(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[EmbeddingModelCRUD, Depends(get_embedding_model_crud)],
    provider: str | None = Query(None, description="Filter by provider"),
    enabled: bool | None = Query(None, description="Filter by enabled status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List embedding model configurations with optional filters."""
    skip = (page - 1) * page_size
    items, total = await crud.list(
        user_id=str(current_user.id),
        provider=provider,
        enabled=enabled,
        skip=skip,
        limit=page_size,
    )
    return EmbeddingModelListResponse(
        total=total, items=items, page=page, page_size=page_size
    )


@router.get("/search", response_model=EmbeddingModelListResponse)
async def search_embedding_models(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[EmbeddingModelCRUD, Depends(get_embedding_model_crud)],
    q: str = Query(..., min_length=1, description="Search term"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Search embedding models by name, description, or model ID."""
    skip = (page - 1) * page_size
    items, total = await crud.search(
        search_term=q,
        user_id=str(current_user.id),
        skip=skip,
        limit=page_size,
    )
    return EmbeddingModelListResponse(
        total=total, items=items, page=page, page_size=page_size
    )


@router.get("/{model_id}", response_model=EmbeddingModelResponse)
async def get_embedding_model(
    model_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[EmbeddingModelCRUD, Depends(get_embedding_model_crud)],
):
    """Get an embedding model by ID."""
    obj = await crud.get_by_id(model_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Embedding model not found"
        )
    if not obj.is_system and str(obj.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    return obj


@router.put("/{model_id}", response_model=EmbeddingModelResponse)
async def update_embedding_model(
    model_id: str,
    data: EmbeddingModelUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[EmbeddingModelCRUD, Depends(get_embedding_model_crud)],
):
    """Update an existing embedding model configuration."""
    obj = await crud.get_by_id(model_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Embedding model not found"
        )
    if not obj.is_system and str(obj.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    updated = await crud.update(model_id, data)
    return updated  # noqa: RET504


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_embedding_model(
    model_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[EmbeddingModelCRUD, Depends(get_embedding_model_crud)],
):
    """Delete an embedding model configuration."""
    obj = await crud.get_by_id(model_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Embedding model not found"
        )
    if obj.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete system models"
        )
    if str(obj.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    await crud.delete(model_id)
