"""REST API endpoints for App (Agent) management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from aiwen.dependencies.agents import get_app_crud, get_template_crud
from aiwen.dependencies.auth import get_current_user
from aiwen.schemas.agents.agent_template import AgentTemplateResponse
from aiwen.schemas.agents.app import AppCreate, AppListResponse, AppResponse, AppUpdate
from aiwen.schemas.auth.user import UserResponse
from aiwen.services.agents.crud.agent_template_crud import AgentTemplateCRUD
from aiwen.services.agents.crud.app_crud import AppCRUD

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("/templates", response_model=list[AgentTemplateResponse])
async def list_agent_templates(
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        curd: Annotated[AgentTemplateCRUD, Depends(get_template_crud)]
):
    """
    List all available agent templates.

    Returns:
        List of available agent template codes
    """
    return await curd.list_templates()


@router.post("/", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
async def create_app(
        data: AppCreate,
        app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
        template_crud: Annotated[AgentTemplateCRUD, Depends(get_template_crud)],
        current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    """
    Create a new app (agent instance).

    Args:
        data: App creation data
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Returns:
        Created app information

    Raises:
        HTTPException 400: If app_code already exists or template not found
    """
    # Check if app already exists
    existing_app = await app_crud.get_app_by_code(data.app_code)
    if existing_app:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"App with code '{data.app_code}' already exists"
        )
    # Validate agent template if provided
    if data.agent_template_code:  # Check if a template code was provided
        template = await template_crud.get_template_by_code(data.agent_template_code)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Agent template '{data.agent_template_code}' not found"
            )
        # Create the app with the current user's ID and resolved template ID
        data_dict = data.model_dump()
        data_dict['user_id'] = current_user.id  # Automatically associate with current user
        data_dict['agent_template_id'] = template.id  # Use the resolved template ID
        updated_data = AppCreate(**data_dict)

        app = await app_crud.create_app(updated_data)
        return app
    # Create the app without a template, with the current user's ID
    data_dict = data.model_dump()
    data_dict['user_id'] = current_user.id  # Automatically associate with current user
    updated_data = AppCreate(**data_dict)

    app = await app_crud.create_app(updated_data)
    return app


@router.get("/{app_code}", response_model=AppResponse)
async def get_app(
        app_code: str,
        app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
        current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    """
    Get app by app_code.

    Args:
        app_code: The app_code to retrieve
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Returns:
        App information

    Raises:
        HTTPException 404: If app not found
    """
    app = await app_crud.get_app_by_code(app_code)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_code}' not found"
        )

    return app


@router.get("/", response_model=AppListResponse)
async def list_apps(
        app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        page: int = Query(1, ge=1, description="Page number"),
        page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
        enabled_only: bool = Query(False, description="Only return enabled apps")
):
    """
    List all apps with pagination.

    Args:
        page: Page number (starting from 1)
        page_size: Number of items per page
        enabled_only: If True, only return enabled apps
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Returns:
        Paginated list of apps
    """
    skip = (page - 1) * page_size
    # Filter apps by current user's ID
    apps, total = await app_crud.list_apps(
        skip=skip,
        limit=page_size,
        enabled_only=enabled_only,
        user_id=current_user.id
    )

    return AppListResponse(
        total=total,
        items=apps,
        page=page,
        page_size=page_size
    )


@router.put("/{app_code}", response_model=AppResponse)
async def update_app(
        app_code: str,
        data: AppUpdate,
        app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
        current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    """
    Update an existing app.

    Args:
        app_code: The app_code to update
        data: Update data
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Returns:
        Updated app information

    Raises:
        HTTPException 404: If app not found
    """
    # Verify that the app belongs to the current user
    app = await app_crud.get_app_by_code(app_code)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_code}' not found"
        )

    # Check if the app belongs to the current user
    if app.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this app"
        )

    app = await app_crud.update_app(app_code, data)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_code}' not found"
        )

    return app


@router.delete("/{app_code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(
        app_code: str,
        app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
        current_user: Annotated[UserResponse, Depends(get_current_user)]
):
    """
    Delete an app by app_code.

    Args:
        app_code: The app_code to delete
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Raises:
        HTTPException 404: If app not found
    """
    # Verify that the app belongs to the current user
    app = await app_crud.get_app_by_code(app_code)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_code}' not found"
        )

    # Check if the app belongs to the current user
    if app.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this app"
        )

    deleted = await app_crud.delete_app(app_code)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_code}' not found"
        )

    return


@router.get("/templates/{template_id}/apps", response_model=AppListResponse)
async def get_apps_by_template(
        template_id: UUID,
        app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
        current_user: Annotated[UserResponse, Depends(get_current_user)],
        page: int = Query(1, ge=1, description="Page number"),
        page_size: int = Query(20, ge=1, le=100, description="Number of items per page")
):
    """
    Get all apps using a specific agent template.

    Args:
        template_id: Agent template UUID
        page: Page number (starting from 1)
        page_size: Number of items per page
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Returns:
        Paginated list of apps using the template
    """
    skip = (page - 1) * page_size
    apps, total = await app_crud.get_apps_by_template(
        agent_template_id=template_id,
        skip=skip,
        limit=page_size
    )

    # Filter apps to only show those belonging to the current user
    user_apps = [app for app in apps if app.user_id == current_user.id]
    user_total = len(user_apps)

    return AppListResponse(
        total=user_total,
        items=user_apps,
        page=page,
        page_size=page_size
    )
