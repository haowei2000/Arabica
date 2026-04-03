"""REST API endpoints for artifact management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.auth import get_current_user
from structure.core.dependencies.workspace import WorkspaceCRUDDep
from structure.extensions.database import get_structure_db
from structure.schemas.auth.user import UserResponse
from structure.schemas.runs.artifact import ArtifactListResponse, ArtifactResponse
from structure.services.runs.artifact_crud import ArtifactCRUD

router = APIRouter(prefix="/workspaces/{workspace_id}/artifacts", tags=["artifacts"])


async def get_artifact_crud(
    db: Annotated[AsyncSession, Depends(get_structure_db)],
) -> ArtifactCRUD:
    return ArtifactCRUD(db)


ArtifactCRUDDep = Annotated[ArtifactCRUD, Depends(get_artifact_crud)]


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    workspace_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    artifact_crud: ArtifactCRUDDep,
    run_id: str | None = Query(None, description="Filter by run ID"),
    artifact_type: str | None = Query(None, description="Filter by artifact type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List artifacts in a workspace."""
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    total, items = await artifact_crud.list_by_workspace(
        workspace_id=workspace_id,
        run_id=run_id,
        artifact_type=artifact_type,
        limit=limit,
        offset=offset,
    )
    return ArtifactListResponse(
        total=total,
        items=[ArtifactResponse.model_validate(a) for a in items],
    )


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    workspace_id: str,
    artifact_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    artifact_crud: ArtifactCRUDDep,
):
    """Get a single artifact by ID."""
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    artifact = await artifact_crud.get_by_id(artifact_id)
    if not artifact or str(artifact.workspace_id) != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    return ArtifactResponse.model_validate(artifact)


@router.get("/{artifact_id}/download")
async def download_artifact(
    workspace_id: str,
    artifact_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    workspace_crud: WorkspaceCRUDDep,
    artifact_crud: ArtifactCRUDDep,
):
    """Download artifact content as a file."""
    workspace = await workspace_crud.get_by_id_and_user(workspace_id, current_user.id)
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found or access denied",
        )

    artifact = await artifact_crud.get_by_id(artifact_id)
    if not artifact or str(artifact.workspace_id) != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    if artifact.s3_url:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=artifact.s3_url)

    if artifact.content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact has no downloadable content",
        )

    content_bytes = artifact.content.encode("utf-8")
    content_type = artifact.content_type or "text/plain; charset=utf-8"

    safe_name = artifact.name.replace('"', '\\"')
    return Response(
        content=content_bytes,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(len(content_bytes)),
        },
    )
