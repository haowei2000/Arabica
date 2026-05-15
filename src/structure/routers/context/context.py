"""REST API endpoints for context/memory management with retrieval methods."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from structure.core.dependencies.agents import get_context_crud
from structure.core.dependencies.auth import get_current_user
from structure.core.enums import ContextType
from structure.schemas.auth.user import UserResponse
from structure.schemas.context.context_schema import (
    ContextCreate,
    ContextListResponse,
    ContextResponse,
    ContextSearchResponse,
    ContextUpdate,
    ContextWithScore,
    GrepSearchRequest,
    VectorSearchRequest,
)
from structure.services.context.context_crud import ContextCRUD
from structure.utils.model_converters import models_to_schemas

router = APIRouter(prefix="/context", tags=["context"])


# ==================== Basic CRUD Endpoints ====================


@router.post(
    "/create",
    response_model=ContextResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_context(
    data: ContextCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
):
    """
    Create a new context entry.

    Args:
        data: ContextSchema creation data
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        Created context
    """
    context = await crud.create(data, user_id=current_user.id)
    if data.context_type == ContextType.SHORT_MEMORY:
        from structure.celery_worker.tasks.context_sync_tasks import (
            sync_memory_to_contexts,
        )

        sync_memory_to_contexts.delay(str(context.id), str(current_user.id))
    return context


@router.get("/batch/ids", response_model=list[ContextResponse])
async def get_contexts_by_ids(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    ids: list[str] = Query(..., description="List of context IDs"),  # noqa: B008
):
    """
    Get multiple contexts by IDs.

    Args:
        ids: List of context IDs
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        List of contexts
    """
    contexts = await crud.get_by_ids(ids, user_id=current_user.id)
    return contexts  # noqa: RET504


@router.post("/{context_id}/update", response_model=ContextResponse)
async def update_context(
    context_id: str,
    data: ContextUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
):
    """
    Update an existing context.

    Args:
        context_id: The context ID
        data: Update data
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        Updated context

    Raises:
        HTTPException: If context not found or not authorized
    """
    context = await crud.update(context_id, user_id=current_user.id, data=data)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ContextSchema {context_id} not found",
        )
    return context


@router.post("/{context_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_context(
    context_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
):
    """
    Delete a context entry.

    Args:
        context_id: The context ID
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Raises:
        HTTPException: If context not found or not authorized
    """
    deleted = await crud.delete(context_id, user_id=current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ContextSchema {context_id} not found",
        )
    return


# ==================== List Endpoints ====================


@router.get("/query", response_model=ContextListResponse)
async def list_contexts(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    context_type: str | None = Query(None, description="Filter by context type"),
    source_id: str | None = Query(None, description="Filter by source ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
):
    """
    List contexts with filtering and pagination.

    Only returns contexts belonging to the current user.

    Args:
        context_type: Filter by context type
        source_id: Filter by source ID
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        Paginated list of contexts
    """
    skip = (page - 1) * page_size
    items, total = await crud.list(
        user_id=current_user.id,
        context_type=context_type,
        source_id=source_id,
        skip=skip,
        limit=page_size,
    )
    return ContextListResponse(total=total, items=items, page=page, page_size=page_size)


# ==================== Grep (Text Search) Endpoints ====================


@router.get("/grep", response_model=ContextListResponse)
async def grep_contexts(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    q: str = Query(..., min_length=1, description="Search query string"),
    context_type: str | None = Query(None, description="Filter by context type"),
    source_id: str | None = Query(None, description="Filter by source ID"),
    search_in: list[str] = Query(  # noqa: B008
        default=["content", "summary"],
        description="Fields to search in (content, summary, keywords)",
    ),
    case_sensitive: bool = Query(default=False, description="Case sensitive search"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
):
    """
    Text search (grep) in contexts.

    Searches in content, summary, and/or keywords fields based on parameters.
    Only searches contexts belonging to the current user.

    Args:
        q: Search query string
        context_type: Filter by context type
        source_id: Filter by source ID
        search_in: Fields to search in
        case_sensitive: Whether to use case sensitive search
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        Paginated list of matching contexts
    """
    skip = (page - 1) * page_size
    items, total = await crud.grep(
        query=q,
        user_id=current_user.id,
        context_type=context_type,
        source_id=source_id,
        search_in=search_in,
        case_sensitive=case_sensitive,
        skip=skip,
        limit=page_size,
    )
    return ContextListResponse(total=total, items=items, page=page, page_size=page_size)


@router.get("/memories", response_model=ContextListResponse)
async def list_memories(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
):
    """List all short_memory Context entries for the current user."""
    skip = (page - 1) * page_size
    items, total = await crud.list(
        user_id=current_user.id,
        context_type=ContextType.SHORT_MEMORY.value,
        skip=skip,
        limit=page_size,
    )
    return ContextListResponse(total=total, items=items, page=page, page_size=page_size)


@router.get("/{context_id}", response_model=ContextResponse)
async def get_context(
    context_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
):
    """
    Get context by ID.

    Args:
        context_id: The context ID
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        ContextSchema details

    Raises:
        HTTPException: If context not found or not authorized
    """
    context = await crud.get_by_id(context_id, user_id=current_user.id)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ContextSchema {context_id} not found",
        )
    return context


@router.post("/grep", response_model=ContextListResponse)
async def grep_contexts_post(
    request: GrepSearchRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
):
    """
    Text search (grep) in contexts via POST.

    Searches in content, summary, and/or keywords fields based on request body.
    Only searches contexts belonging to the current user.

    Args:
        request: Grep search request parameters
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        Paginated list of matching contexts
    """
    items, total = await crud.grep(
        query=request.query,
        user_id=current_user.id,
        context_type=request.context_type,
        source_id=request.source_id,
        search_in=request.search_in,
        case_sensitive=request.case_sensitive,
        skip=request.skip,
        limit=request.limit,
    )
    page = (request.skip // request.limit) + 1 if request.limit > 0 else 1
    return ContextListResponse(
        total=total, items=items, page=page, page_size=request.limit
    )


# ==================== Cosine Similarity (Vector Search) Endpoints ====================


@router.post("/vector-search", response_model=ContextSearchResponse)
async def vector_search(
    request: VectorSearchRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
):
    """
    Vector similarity search using cosine distance.

    Finds contexts with similar embeddings to the query vector.
    Only searches contexts belonging to the current user.

    Args:
        request: Vector search request with embedding and parameters
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        List of contexts with similarity scores
    """
    try:
        results = await crud.cosine_search(
            embedding=request.embedding,
            user_id=current_user.id,
            dimension=request.dimension,
            context_type=request.context_type,
            source_id=request.source_id,
            top_k=request.top_k,
            threshold=request.threshold,
        )
    except ValueError as e:
        raise HTTPException(  # noqa: B904
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Extract contexts and scores from results
    contexts = [context for context, _ in results]
    scores = [score for _, score in results]

    # Convert models to schemas with dynamic score injection
    items = models_to_schemas(
        ContextWithScore,
        contexts,
        extra_factory=lambda ctx, idx: {"score": scores[idx]},  # noqa: ARG005
    )

    return ContextSearchResponse(total=len(items), items=items)


@router.post("/hybrid-search", response_model=ContextSearchResponse)
async def hybrid_search(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    query: str = Query(..., min_length=1, description="Text search query"),
    embedding: list[float] = Query(..., description="Query embedding vector"),  # noqa: B008
    dimension: Literal[384, 768, 1024, 1536] = Query(
        default=1536, description="Embedding dimension"
    ),
    context_type: str | None = Query(None, description="Filter by context type"),
    source_id: str | None = Query(None, description="Filter by source ID"),
    top_k: int = Query(default=10, ge=1, le=100, description="Number of results"),
    vector_weight: float = Query(
        default=0.7, ge=0.0, le=1.0, description="Weight for vector similarity"
    ),
    text_weight: float = Query(
        default=0.3, ge=0.0, le=1.0, description="Weight for text matching"
    ),
):
    """
    Hybrid search combining vector similarity and text matching.

    Combines results from both vector similarity search and text search,
    weighted by the specified parameters.
    Only searches contexts belonging to the current user.

    Args:
        query: Text search query
        embedding: Query embedding vector
        dimension: Embedding dimension
        context_type: Filter by context type
        source_id: Filter by source ID
        top_k: Number of results to return
        vector_weight: Weight for vector similarity (0-1)
        text_weight: Weight for text matching (0-1)
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        List of contexts with combined scores
    """
    try:
        results = await crud.hybrid_search(
            query=query,
            embedding=embedding,
            user_id=current_user.id,
            dimension=dimension,
            context_type=context_type,
            source_id=source_id,
            top_k=top_k,
            vector_weight=vector_weight,
            text_weight=text_weight,
        )
    except ValueError as e:
        raise HTTPException(  # noqa: B904
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Extract contexts and scores from results
    contexts = [context for context, _ in results]
    scores = [score for _, score in results]

    # Convert models to schemas with dynamic score injection
    items = models_to_schemas(
        ContextWithScore,
        contexts,
        extra_factory=lambda ctx, idx: {"score": scores[idx]},  # noqa: ARG005
    )

    return ContextSearchResponse(total=len(items), items=items)


# ==================== Batch Operations ====================


@router.post(
    "/batch/create",
    response_model=list[ContextResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_contexts_batch(
    items: list[ContextCreate],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
):
    """
    Create multiple context entries in batch.

    Args:
        items: List of context creation data
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        List of created contexts
    """
    contexts = await crud.create_batch(items, user_id=current_user.id)
    return contexts  # noqa: RET504


@router.post("/batch/delete-by-source/{source_id}", status_code=status.HTTP_200_OK)
async def delete_contexts_by_source(
    source_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
):
    """
    Delete all contexts by source ID.

    Args:
        source_id: The source ID to delete contexts for
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        Number of deleted contexts
    """
    count = await crud.delete_by_source_id(source_id, user_id=current_user.id)
    return {"deleted_count": count}


# ==================== Chunk Listing Endpoints ====================


@router.get("/knowledge/{knowledge_id}/chunks", response_model=ContextListResponse)
async def list_chunks_by_knowledge(
    knowledge_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
):
    """
    List all chunks in a knowledge base.

    Args:
        knowledge_id: The knowledge base ID
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        Paginated list of chunks
    """
    skip = (page - 1) * page_size
    items, total = await crud.list(
        user_id=current_user.id,
        context_type=ContextType.CHUNK.value,
        knowledge_id=knowledge_id,
        skip=skip,
        limit=page_size,
    )
    return ContextListResponse(total=total, items=items, page=page, page_size=page_size)


@router.get("/document/{document_id}/chunks", response_model=ContextListResponse)
async def list_chunks_by_document(
    document_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=1000, description="Number of items per page"),
):
    """
    List all chunks for a specific document.

    Args:
        document_id: The document ID
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        crud: ContextSchema CRUD service

    Returns:
        Paginated list of chunks for the document
    """
    skip = (page - 1) * page_size
    items, total = await crud.list_by_document_id(
        document_id=document_id,
        user_id=current_user.id,
        skip=skip,
        limit=page_size,
    )
    return ContextListResponse(total=total, items=items, page=page, page_size=page_size)
