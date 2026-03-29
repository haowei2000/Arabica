"""REST API endpoints for App (Agent) management."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from structure.core.dependencies.agents import get_app_crud, get_executor_crud
from structure.core.dependencies.auth import get_current_user
from structure.schemas.app.app import AppCreate, AppListResponse, AppResponse, AppUpdate
from structure.schemas.auth.user import UserResponse
from structure.schemas.executor.executor import ExecutorResponse
from structure.services.app.app_crud import AppCRUD
from structure.services.executor.executor_crud import ExecutorCRUD
from structure.utils.model_converters import models_to_schemas

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("/executors/list", response_model=list[ExecutorResponse])
async def list_executors(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    curd: Annotated[ExecutorCRUD, Depends(get_executor_crud)],
):
    """
    List all available executors.

    Returns:
        List of available executor codes
    """
    return await curd.list_executors(include_disabled=False)


@router.post("/create", response_model=AppResponse, status_code=status.HTTP_201_CREATED)
async def create_app(
    data: AppCreate,
    app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
    executor_crud: Annotated[ExecutorCRUD, Depends(get_executor_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
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
        HTTPException 400: If app_id already exists or executor not found
    """
    # Check if app already exists
    existing_app = await app_crud.get_app_by_code(data.app_code)
    if existing_app:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"App with code '{data.app_code}' already exists",
        )
    # Validate executor if provided
    if data.executor_code:  # Check if an executor code was provided
        executor = await executor_crud.get_executor_by_code(data.executor_code)
        if not executor:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Executor '{data.executor_code}' not found",
            )
        # Create the app with the current user's ID and resolved executor ID
        data_dict = data.model_dump()
        data_dict["user_id"] = (
            current_user.id
        )  # Automatically associate with current user
        data_dict["executor_id"] = executor.id  # Use the resolved executor ID
        updated_data = AppCreate(**data_dict)

        app = await app_crud.create_app(updated_data)
        return app
    # Create the app without an executor, with the current user's ID
    data_dict = data.model_dump()
    data_dict["user_id"] = current_user.id  # Automatically associate with current user
    updated_data = AppCreate(**data_dict)

    app = await app_crud.create_app(updated_data)
    return app


@router.get("/{app_id}/get", response_model=AppResponse)
async def get_app(
    app_id: UUID,
    app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Get app by app_id.

    Args:
        app_id: The app_id to retrieve
        app_crud: App CRUD dependency

    Returns:
        App information

    Raises:
        HTTPException 404: If app not found
    """
    app = await app_crud.get_app(app_id)

    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"App '{app_id}' not found"
        )

    if app.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this app",
        )
    return app


@router.get("/list", response_model=AppListResponse)
async def list_apps(
    app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    enabled_only: bool = Query(False, description="Only return enabled apps"),
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
        skip=skip, limit=page_size, enabled_only=enabled_only, user_id=current_user.id
    )

    return AppListResponse(total=total, items=apps, page=page, page_size=page_size)  # ty:ignore[invalid-argument-type]


@router.post("/{app_id}/update", response_model=AppResponse)
async def update_app(
    app_id: UUID,
    data: AppUpdate,
    app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Update an existing app.

    Args:
        app_id: The app_id to update
        data: Update data
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Returns:
        Updated app information

    Raises:
        HTTPException 404: If app not found
    """
    # Verify that the app belongs to the current user
    app = await app_crud.get_app(app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"App '{app_id}' not found"
        )

    # Check if the app belongs to the current user
    if app.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this app",
        )

    app = await app_crud.update_app(app_id, data)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"App '{app_id}' not found"
        )

    return app


@router.post("/{app_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app(
    app_id: UUID,
    app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Delete an app by app_id.

    Args:
        app_id: The app_id to delete
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Raises:
        HTTPException 404: If app not found
    """
    # Verify that the app belongs to the current user
    app = await app_crud.get_app(app_id)
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App with id '{app_id}' not found",
        )

    # Check if the app belongs to the current user
    if app.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this app",
        )

    deleted = await app_crud.delete_app(app_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App with id '{app_id}' not found",
        )

    return


@router.get("/query", response_model=AppListResponse)
async def query_apps(
    app_crud: Annotated[AppCRUD, Depends(get_app_crud)],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    executor_id: UUID | None = Query(None, description="Filter by executor UUID"),
    enabled: bool | None = Query(None, description="Filter by enabled status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
):
    """
    Query apps with optional filters.

    Args:
        executor_id: Filter by executor UUID (optional)
        enabled: Filter by enabled status (optional)
        page: Page number (starting from 1)
        page_size: Number of items per page
        app_crud: App CRUD dependency
        current_user: Current authenticated user

    Returns:
        Paginated list of apps matching the filters
    """
    skip = (page - 1) * page_size

    if executor_id:
        apps, total = await app_crud.filter_apps_by_executor(
            executor_id=executor_id, skip=skip, limit=page_size
        )
        # Filter apps to only show those belonging to the current user
        user_apps = [app for app in apps if app.user_id == current_user.id]
        user_total = len(user_apps)
    else:
        user_apps, user_total = await app_crud.list_apps(
            skip=skip,
            limit=page_size,
            enabled_only=enabled if enabled is not None else False,
            user_id=current_user.id,
        )

    return AppListResponse(
        total=user_total,
        items=models_to_schemas(AppResponse,user_apps),
        page=page,
        page_size=page_size,
    )
