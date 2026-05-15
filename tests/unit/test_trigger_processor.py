from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from structure.routers.triggers import test_trigger as trigger_test_endpoint
from structure.schemas.workspaces.trigger import TriggerTestRequest
from structure.services.triggers.trigger_processor import TriggerProcessor


class _FakeMeta:
    name = "read_context"


class _FakeTool:
    METADATA = _FakeMeta()


@pytest.mark.asyncio
async def test_trigger_resolves_dynamic_tool_when_registry_misses(monkeypatch):
    workspace_id = uuid4()
    user_id = uuid4()
    seen = {}

    monkeypatch.setattr(
        "structure.registries.core.ToolRegistry.get_tool_instance",
        lambda name: None,
    )

    async def fake_load_user_tools(db, loaded_user_id, loaded_workspace_id):
        seen["db"] = db
        seen["user_id"] = loaded_user_id
        seen["workspace_id"] = loaded_workspace_id
        return [_FakeTool]

    monkeypatch.setattr(
        "structure.registries.dynamic_loader.DynamicToolLoader.load_user_tools",
        fake_load_user_tools,
    )

    db = object()
    processor = TriggerProcessor(db, str(workspace_id))

    tool = await processor._resolve_tool_instance(
        "read_context",
        SimpleNamespace(user_id=user_id),
    )

    assert isinstance(tool, _FakeTool)
    assert seen == {
        "db": db,
        "user_id": user_id,
        "workspace_id": workspace_id,
    }


@pytest.mark.asyncio
async def test_trigger_dry_run_passes_event_to_action(monkeypatch):
    workspace_id = uuid4()
    trigger_id = uuid4()
    user_id = uuid4()
    trigger = SimpleNamespace(
        id=trigger_id,
        workspace_id=workspace_id,
        name="dry-run",
        event_type="user.message",
        condition_type="always",
        condition_value=None,
        condition_field="message",
        tool_name="read_context",
        action_params={"path": "/memory/integration/fact"},
        priority=0,
        enabled=True,
        created_by=user_id,
        created_at=datetime.now(UTC),
        updated_at=None,
    )

    class _Result:
        def scalar_one_or_none(self):
            return trigger

    class _DB:
        async def execute(self, stmt):
            return _Result()

    seen = {}

    async def fake_execute_action(self, loaded_trigger, event):
        seen["trigger"] = loaded_trigger
        seen["event"] = event
        return {"success": True}

    monkeypatch.setattr(TriggerProcessor, "_execute_action", fake_execute_action)

    response = await trigger_test_endpoint(
        str(workspace_id),
        str(trigger_id),
        TriggerTestRequest(payload={"message": "hello"}),
        SimpleNamespace(id=user_id),
        _DB(),
    )

    assert response.matched is True
    assert response.result == {"success": True}
    assert seen["trigger"] is trigger
    assert seen["event"].payload == {"message": "hello"}
    assert seen["event"].user_id == user_id
