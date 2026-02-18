"""REST API endpoints for skill management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.dependencies.auth import get_current_user
from aiwen.extensions.database import get_aiwen_db
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.context.skill import (
    SkillCreate,
    SkillListResponse,
    SkillResponse,
    SkillUpdate,
)
from aiwen.services.context.skill_crud import SkillCRUD
from aiwen.services.context.skill_processor import SkillProcessor

router = APIRouter(prefix="/skills", tags=["skills"])


def _build_skill_response(skill) -> SkillResponse:
    """Build SkillResponse from Skill model.

    Embeddings are stored in the Context table (via sync_skill_to_contexts),
    not on the Skill row itself, so has_embedding is always False here.
    """
    return SkillResponse(
        id=skill.id,
        user_id=skill.user_id,
        source_id=skill.source_id,
        path=skill.path,
        name=skill.name,
        description=skill.description,
        content=skill.content,
        glance=skill.glance,
        summary=skill.summary,
        tags=skill.tags,
        has_embedding=False,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new skill",
)
async def create_skill(
    data: SkillCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Create a new skill from Markdown content.

    The skill will be automatically processed to:
    - Parse Markdown structure
    - Extract metadata
    - Generate summary

    Args:
        data: Skill creation data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created skill
    """
    crud = SkillCRUD(db)
    processor = SkillProcessor(db)

    # Check if skill with same name exists
    existing = await crud.get_by_name(data.name, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Skill with name '{data.name}' already exists",
        )

    # Create skill
    skill = await crud.create(data, user_id=current_user.id, auto_commit=False)

    # Process skill (parse Markdown, generate summary)
    try:
        skill = await processor.process_skill(skill.id, auto_commit=True)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process skill: {e!s}",
        )

    from aiwen.celery_worker.tasks.context_sync_tasks import sync_skill_to_contexts
    sync_skill_to_contexts.delay(str(skill.id), str(current_user.id))
    return _build_skill_response(skill)


@router.get(
    "/{skill_id}",
    response_model=SkillResponse,
    summary="Get skill by ID",
)
async def get_skill(
    skill_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Get a specific skill by ID.

    Args:
        skill_id: Skill UUID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Skill details

    Raises:
        HTTPException 404: If skill not found
        HTTPException 403: If user doesn't have access
    """
    crud = SkillCRUD(db)
    skill = await crud.get_by_id(skill_id, user_id=current_user.id)

    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {skill_id} not found",
        )

    return _build_skill_response(skill)


@router.put(
    "/{skill_id}",
    response_model=SkillResponse,
    summary="Update a skill",
)
async def update_skill(
    skill_id: str,
    data: SkillUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Update an existing skill.

    If content is updated, the skill will be reprocessed automatically.

    Args:
        skill_id: Skill UUID
        data: Update data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated skill

    Raises:
        HTTPException 404: If skill not found
        HTTPException 403: If user doesn't have access
    """
    crud = SkillCRUD(db)
    processor = SkillProcessor(db)

    # Update skill
    skill = await crud.update(skill_id, data, user_id=current_user.id, auto_commit=False)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {skill_id} not found",
        )

    # Reprocess if content was updated
    if data.content is not None:
        try:
            skill = await processor.process_skill(skill.id, auto_commit=True)
        except Exception as e:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process skill: {e!s}",
            )
    else:
        await db.commit()
        await db.refresh(skill)

    from aiwen.celery_worker.tasks.context_sync_tasks import sync_skill_to_contexts
    sync_skill_to_contexts.delay(str(skill.id), str(current_user.id))
    return _build_skill_response(skill)


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill",
)
async def delete_skill(
    skill_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Delete a skill (soft delete).

    Args:
        skill_id: Skill UUID
        current_user: Current authenticated user
        db: Database session

    Raises:
        HTTPException 404: If skill not found
        HTTPException 403: If user doesn't have access
    """
    crud = SkillCRUD(db)
    success = await crud.delete(skill_id, user_id=current_user.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {skill_id} not found",
        )


@router.get(
    "",
    response_model=SkillListResponse,
    summary="List skills",
)
async def list_skills(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    tags: str | None = Query(None, description="Comma-separated tags filter"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """
    List all skills for the current user.

    Args:
        tags: Optional comma-separated tags filter
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        db: Database session

    Returns:
        Paginated list of skills
    """
    crud = SkillCRUD(db)

    # Parse tags
    tag_list = [tag.strip() for tag in tags.split(",")] if tags else None

    skip = (page - 1) * page_size
    items, total = await crud.list(
        user_id=current_user.id,
        tags=tag_list,
        skip=skip,
        limit=page_size,
    )

    skill_responses = [_build_skill_response(skill) for skill in items]

    return SkillListResponse(
        total=total,
        items=skill_responses,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/search/query",
    response_model=SkillListResponse,
    summary="Search skills",
)
async def search_skills(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """
    Search skills by name or content.

    Args:
        q: Search query
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        db: Database session

    Returns:
        Paginated list of matching skills
    """
    crud = SkillCRUD(db)

    skip = (page - 1) * page_size
    items, total = await crud.search(
        user_id=current_user.id,
        query_text=q,
        skip=skip,
        limit=page_size,
    )

    skill_responses = [_build_skill_response(skill) for skill in items]

    return SkillListResponse(
        total=total,
        items=skill_responses,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/{skill_id}/process",
    response_model=SkillResponse,
    summary="Reprocess a skill",
)
async def process_skill(
    skill_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    embedding_model: str | None = Query(None, description="Embedding model to use"),
):
    """
    Manually trigger skill processing (parse Markdown, generate embeddings).

    Useful for regenerating embeddings with a different model.

    Args:
        skill_id: Skill UUID
        embedding_model: Optional embedding model to use
        current_user: Current authenticated user
        db: Database session

    Returns:
        Processed skill

    Raises:
        HTTPException 404: If skill not found
        HTTPException 403: If user doesn't have access
    """
    crud = SkillCRUD(db)
    processor = SkillProcessor(db)

    # Verify ownership
    skill = await crud.get_by_id(skill_id, user_id=current_user.id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {skill_id} not found",
        )

    try:
        skill = await processor.process_skill(skill.id, embedding_model=embedding_model)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process skill: {e!s}",
        )

    from aiwen.celery_worker.tasks.context_sync_tasks import sync_skill_to_contexts
    sync_skill_to_contexts.delay(str(skill.id), str(current_user.id))
    return _build_skill_response(skill)
