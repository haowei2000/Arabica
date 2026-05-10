from types import SimpleNamespace
from uuid import uuid4

import pytest

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
