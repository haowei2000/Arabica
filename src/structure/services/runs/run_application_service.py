"""Application service for starting user-initiated runs."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums.runs import TriggerType
from structure.models.app import App
from structure.models.executor.executor import ExecutorTemplate
from structure.models.runs.run import Run
from structure.schemas.auth.user import UserResponse
from structure.schemas.events.event_payloads import EventType, UserMessageEventSchema
from structure.services.auth.quota_service import QuotaService
from structure.services.events.event_publisher import EventPublisher
from structure.services.runs.run_crud import RunCRUD
from structure.services.workspaces.workspace_crud import WorkspaceCRUD

logger = logging.getLogger(__name__)

DEFAULT_EXECUTOR_CODE = "SimpleAgent"


class WorkspaceAccessDeniedError(Exception):
    """Raised when a user cannot access the requested workspace."""


class RunApplicationService:
    """Coordinate user run startup without leaking executor logic into routers."""

    def __init__(
        self,
        db: AsyncSession,
        workspace_crud: WorkspaceCRUD,
        run_crud: RunCRUD,
        event_publisher: EventPublisher,
        quota_service: QuotaService,
    ) -> None:
        self.db = db
        self.workspace_crud = workspace_crud
        self.run_crud = run_crud
        self.event_publisher = event_publisher
        self.quota_service = quota_service

    async def start_user_run(
        self,
        user_message_event: UserMessageEventSchema,
        current_user: UserResponse,
    ) -> Run:
        """Create a pending run, persist the first user message, and enqueue it."""
        start = perf_counter()
        workspace_id = str(user_message_event.workspace_id)
        workspace = await self.workspace_crud.get_by_id_and_user(
            workspace_id,
            current_user.id,
        )
        if not workspace:
            raise WorkspaceAccessDeniedError(
                f"Workspace {workspace_id} not found or access denied"
            )

        await self.quota_service.assert_can_start_run(current_user)

        app_id = user_message_event.app_id
        executor_code = await self.resolve_executor_code(
            app_id=str(app_id) if app_id else None,
            workspace_executor_code=getattr(workspace, "executor_code", None),
        )

        run = await self.run_crud.create(
            workspace_id=workspace_id,
            app_id=app_id,
            user_id=current_user.id,
            trigger_type=TriggerType.USER,
            auto_commit=False,
        )

        payload: dict[str, Any] = user_message_event.payload.model_dump(
            exclude_none=True
        )
        await self.event_publisher.publish_durable(
            event_type=EventType.USER_MESSAGE,
            workspace_id=workspace_id,
            run_id=str(run.id),
            app_id=str(app_id) if app_id else None,
            user_id=str(current_user.id),
            executor_code=executor_code,
            payload=payload,
            auto_commit=True,
        )

        logger.info(
            "start_user_run completed workspace=%s run=%s executor_code=%s "
            "api_create_run_ms=%s",
            workspace_id,
            run.id,
            executor_code,
            int((perf_counter() - start) * 1000),
        )
        return run

    async def resolve_executor_code(
        self,
        *,
        app_id: str | None,
        workspace_executor_code: str | None,
    ) -> str:
        """Resolve executor with request app -> app executor -> workspace -> default."""
        if app_id:
            app_result = await self.db.execute(select(App).where(App.id == app_id))
            app_row = app_result.scalar_one_or_none()
            if app_row and app_row.executor_id:
                tmpl_result = await self.db.execute(
                    select(ExecutorTemplate).where(
                        ExecutorTemplate.id == app_row.executor_id
                    )
                )
                tmpl = tmpl_result.scalar_one_or_none()
                if tmpl and tmpl.executor_code:
                    return tmpl.executor_code

        if workspace_executor_code:
            return workspace_executor_code

        return DEFAULT_EXECUTOR_CODE
