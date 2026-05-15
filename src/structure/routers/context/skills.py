"""REST API endpoints for skill management."""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.agents import get_context_crud, get_skill_crud
from structure.core.dependencies.auth import get_current_user
from structure.core.enums import ContextType
from structure.extensions.database import get_structure_db
from structure.extensions.storage.global_storage import get_global_s3_storage
from structure.extensions.storage.s3_storage_backend import S3StorageBackend
from structure.schemas.auth.user import UserResponse
from structure.schemas.context.context_schema import ContextListResponse
from structure.schemas.context.skill import (
    SkillCreate,
    SkillListResponse,
    SkillResponse,
    SkillUpdate,
)
from structure.services.context.context_crud import ContextCRUD
from structure.services.context.skill_crud import SkillCRUD

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


def _build_skill_response(skill) -> SkillResponse:
    return SkillResponse(
        id=skill.id,
        user_id=skill.user_id,
        name=skill.name,
        description=skill.description,
        tags=skill.tags,
        has_embedding=False,
        files=skill.files,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a skill",
)
async def create_skill(
    data: SkillCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[SkillCRUD, Depends(get_skill_crud)],
):
    """Create a new user skill and sync it into the context index."""
    existing = await crud.get_by_name(data.name, current_user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Skill with name '{data.name}' already exists",
        )

    skill = await crud.create(data, user_id=current_user.id)
    return _build_skill_response(skill)


@router.get(
    "/{skill_id}/context",
    response_model=ContextListResponse,
    summary="View synced context entries for a skill",
)
async def get_skill_context(
    skill_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    context_crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """Return all Context entries synced from the given skill."""
    skip = (page - 1) * page_size
    items, total = await context_crud.list(
        user_id=current_user.id,
        context_type=ContextType.SKILL,
        source_id=skill_id,
        skip=skip,
        limit=page_size,
    )
    return ContextListResponse(total=total, items=items, page=page, page_size=page_size)


@router.get(
    "/{skill_id}",
    response_model=SkillResponse,
    summary="Get skill by ID",
)
async def get_skill(
    skill_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
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
    crud: Annotated[SkillCRUD, Depends(get_skill_crud)],
):
    """
    Update an existing skill.

    If content is updated, the skill will be reprocessed automatically.
    """
    skill = await crud.update(skill_id, data, user_id=current_user.id, auto_commit=True)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {skill_id} not found",
        )

    from structure.celery_worker.tasks.context_sync_tasks import sync_skill_to_contexts

    sync_skill_to_contexts.delay(str(skill.id), str(current_user.id), data.content)
    return _build_skill_response(skill)


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill",
)
async def delete_skill(
    skill_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[SkillCRUD, Depends(get_skill_crud)],
    storage: Annotated[S3StorageBackend, Depends(get_global_s3_storage)],
):
    """
    Delete a skill and its associated S3 files.
    """
    skill = await crud.get_by_id(skill_id, user_id=current_user.id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {skill_id} not found",
        )

    # Delete S3 files before removing the DB record
    if skill.files:
        for path, meta in skill.files.items():
            s3_key = meta.get("s3_key") or f"/{current_user.id}/skills/{path}"
            try:
                storage.delete(s3_key)
                logger.info(f"Deleted S3 file: {s3_key}")
            except Exception as e:
                logger.error(f"Failed to delete S3 file {s3_key}: {e}")

    await crud.delete(skill_id, user_id=current_user.id)

    from structure.celery_worker.tasks.context_sync_tasks import (
        delete_resource_contexts,
    )

    delete_resource_contexts.delay(skill_id, "skill", "skill_id")


@router.get(
    "",
    response_model=SkillListResponse,
    summary="List skills",
)
async def list_skills(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
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
    db: Annotated[AsyncSession, Depends(get_structure_db)],
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


@router.get(
    "/{skill_id}/files/{file_path:path}",
    summary="Get skill file content",
)
async def get_skill_file(
    skill_id: str,
    file_path: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[SkillCRUD, Depends(get_skill_crud)],
    storage: Annotated[S3StorageBackend, Depends(get_global_s3_storage)],
):
    """Return the raw content of a file stored in a skill."""
    skill = await crud.get_by_id(skill_id, user_id=current_user.id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill {skill_id} not found"
        )

    if not skill.files or file_path not in skill.files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_path}' not found in skill",
        )

    meta = skill.files[file_path]
    s3_key = meta.get("s3_key") or f"/{current_user.id}/skills/{file_path}"
    try:
        data = storage.get_bytes(s3_key)
    except Exception as e:
        logger.error(f"Failed to fetch skill file {s3_key}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch file content")  # noqa: B904

    content_type = meta.get("content_type") or "application/octet-stream"
    return Response(content=data, media_type=content_type)


@router.post(
    "/{skill_id}/process",
    response_model=SkillResponse,
    summary="Reprocess a skill",
)
async def process_skill(
    skill_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
    embedding_model: str | None = Query(None, description="Embedding model to use"),  # noqa: ARG001
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

    skill = await crud.get_by_id(skill_id, user_id=current_user.id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {skill_id} not found",
        )

    from structure.celery_worker.tasks.context_sync_tasks import sync_skill_to_contexts

    sync_skill_to_contexts.delay(str(skill.id), str(current_user.id))
    return _build_skill_response(skill)


@router.post(
    "/upload-folder",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a skill folder",
)
async def upload_skill_folder(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    crud: Annotated[SkillCRUD, Depends(get_skill_crud)],
    storage: Annotated[S3StorageBackend, Depends(get_global_s3_storage)],
    files: list[UploadFile] = File(..., description="All files in the folder"),  # noqa: B008
    tags: str | None = Query(None, description="Comma-separated tags"),  # noqa: ARG001
):
    """
    Upload a skill folder.

    The folder must contain a SKILL.md file with optional YAML frontmatter:
    ```yaml
    ---
    name: My Skill
    description: What this skill does
    tags: [python, coding]
    ---
    # Skill content here
    ```

    Other files (.md, .py, .txt, etc.) are uploaded to S3 storage and
    their metadata is stored in the `files` field.
    """

    # Read all file contents up front to avoid consuming streams twice
    file_data: dict[str, bytes] = {}
    for upload_file in files:
        file_data[upload_file.filename] = await upload_file.read()

    skill_md_path = next(
        (p for p in file_data if Path(p).parts[1].lower() == "skill.md"),
        None,
    )
    if not skill_md_path:
        logger.warning(f"No SKILL.md found. Processed paths: {list(file_data.keys())}")
        raise HTTPException(
            status_code=400, detail="Folder must contain a SKILL.md file"
        )

    skill_md_content = file_data[skill_md_path].decode("utf-8")
    skill_name = Path(skill_md_path).parts[0]

    existing = await crud.get_by_name(skill_name, current_user.id)
    if existing:
        logger.warning(f"Skill '{skill_name}' already exists (id={existing.id})")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Skill with name '{skill_name}' already exists",
        )
    logger.info(f"Processing {len(files)} files, skill_name={skill_name!r}")

    create_data = SkillCreate(
        name=skill_name,
        description="",
        content=skill_md_content,
        meta={"source": "folder_upload"},
        tags=[],
    )
    # Create skill first to get the skill_id
    skill = await crud.create(create_data, user_id=current_user.id)
    files_metadata: dict[str, dict] = {}
    for path, raw in file_data.items():
        # Build S3 key: skills/{user_id}/{skill_id}/{original_path}
        s3_key = f"/{current_user.id}/skills/{path}"
        logger.info(f"Uploading file: {path} -> S3 key: {s3_key}")

        # Determine content type
        content_type = None
        if path.endswith(".py"):
            content_type = "text/x-python"
        elif path.endswith(".md"):
            content_type = "text/markdown"
        elif path.endswith(".txt"):
            content_type = "text/plain"
        elif path.endswith(".json"):
            content_type = "application/json"
        elif path.endswith(".yaml") or path.endswith(".yml"):
            content_type = "application/yaml"
        try:
            obj_info = storage.put_bytes(
                key=s3_key,
                data=raw,
                content_type=content_type,
                metadata={
                    "original_path": path,
                    "skill_id": str(skill.id),
                    "user_id": str(current_user.id),
                },
            )

            files_metadata[path] = {
                "s3_key": s3_key,
                "size": obj_info.size,
                "etag": obj_info.etag,
                "content_type": content_type,
            }
            logger.info(
                f"Successfully uploaded {path}: size={obj_info.size}, etag={obj_info.etag}"
            )
        except Exception as e:
            logger.error(f"Failed to upload file {path} to S3: {e}")
            # Continue with other files, but log the error

    # Update skill with files metadata
    logger.info(f"Files metadata collected: {len(files_metadata)} files")
    if files_metadata:
        skill.files = files_metadata
        await crud.db.commit()
        await crud.db.refresh(skill)
        logger.info("Updated skill.files with metadata")

    logger.info(f"Skill upload completed successfully: {skill.id}")

    from structure.celery_worker.tasks.context_sync_tasks import sync_skill_to_contexts

    sync_skill_to_contexts.delay(str(skill.id), str(current_user.id))

    return _build_skill_response(skill)
