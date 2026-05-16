from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from structure.schemas.events.event_payloads import UserMessageEventSchema
from structure.services.auth.quota_service import QuotaExceededError
from structure.services.runs.run_application_service import (
    DEFAULT_EXECUTOR_CODE,
    RunApplicationService,
    WorkspaceAccessDeniedError,
)
from tests.unit.routers.conftest import make_user


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _service(*, workspace=None, db=None):
    workspace_crud = AsyncMock()
    workspace_crud.get_by_id_and_user = AsyncMock(return_value=workspace)
    run = MagicMock()
    run.id = uuid4()
    run_crud = AsyncMock()
    run_crud.create = AsyncMock(return_value=run)
    publisher = AsyncMock()
    publisher.publish_durable = AsyncMock()
    quota = AsyncMock()
    quota.assert_can_start_run = AsyncMock()
    return (
        RunApplicationService(
            db=db or AsyncMock(),
            workspace_crud=workspace_crud,
            run_crud=run_crud,
            event_publisher=publisher,
            quota_service=quota,
        ),
        workspace_crud,
        run_crud,
        publisher,
        quota,
        run,
    )


@pytest.mark.asyncio
async def test_start_user_run_uses_workspace_executor_fallback():
    workspace_id = uuid4()
    workspace = MagicMock()
    workspace.executor_code = "WorkspaceAgent"
    service, _, run_crud, publisher, quota, run = _service(workspace=workspace)
    event = UserMessageEventSchema(
        workspace_id=workspace_id,
        payload={"message": "hello"},
    )

    result = await service.start_user_run(event, make_user())

    assert result is run
    quota.assert_can_start_run.assert_awaited_once()
    run_crud.create.assert_awaited_once()
    publisher.publish_durable.assert_awaited_once()
    assert publisher.publish_durable.await_args.kwargs["executor_code"] == "WorkspaceAgent"


@pytest.mark.asyncio
async def test_start_user_run_resolves_app_executor_before_workspace():
    app_id = uuid4()
    app = MagicMock()
    app.executor_id = uuid4()
    tmpl = MagicMock()
    tmpl.executor_code = "AppAgent"
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(app), _result(tmpl)])
    workspace = MagicMock()
    workspace.executor_code = "WorkspaceAgent"
    service, *_rest = _service(workspace=workspace, db=db)
    event = UserMessageEventSchema(
        workspace_id=uuid4(),
        app_id=app_id,
        payload={"message": "hello"},
    )

    await service.start_user_run(event, make_user())

    publisher = _rest[2]
    assert publisher.publish_durable.await_args.kwargs["executor_code"] == "AppAgent"


@pytest.mark.asyncio
async def test_start_user_run_raises_for_workspace_denial():
    service, *_ = _service(workspace=None)
    event = UserMessageEventSchema(workspace_id=uuid4(), payload={"message": "hello"})

    with pytest.raises(WorkspaceAccessDeniedError):
        await service.start_user_run(event, make_user())


@pytest.mark.asyncio
async def test_start_user_run_propagates_quota_failure():
    workspace = MagicMock()
    workspace.executor_code = None
    service, *_rest = _service(workspace=workspace)
    quota = _rest[3]
    quota.assert_can_start_run.side_effect = QuotaExceededError("exhausted")
    event = UserMessageEventSchema(workspace_id=uuid4(), payload={"message": "hello"})

    with pytest.raises(QuotaExceededError):
        await service.start_user_run(event, make_user())


@pytest.mark.asyncio
async def test_resolve_executor_defaults_when_no_app_or_workspace_executor():
    service, *_ = _service(workspace=MagicMock(executor_code=None))

    executor_code = await service.resolve_executor_code(
        app_id=None,
        workspace_executor_code=None,
    )

    assert executor_code == DEFAULT_EXECUTOR_CODE

