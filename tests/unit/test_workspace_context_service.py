from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from structure.services.workspace_context.workspace_context_service import (
    WorkspaceContextService,
)


@dataclass
class _FakeContext:
    path: str
    payload: dict[str, Any]

    def disclose(self, level: str = "overview") -> dict[str, Any]:
        return dict(self.payload)


def _loaded_service() -> WorkspaceContextService:
    service = WorkspaceContextService(object(), uuid4())  # type: ignore[arg-type]
    service._loaded = True
    return service


@pytest.mark.asyncio
async def test_get_prefers_workspace_context_rows_for_same_path():
    service = _loaded_service()
    service._contexts = [  # type: ignore[list-item]
        _FakeContext(
            path="/memory/integration/fact",
            payload={"content": "stale value", "context_type": "conversation"},
        )
    ]
    service._workspace_contexts = [  # type: ignore[list-item]
        _FakeContext(
            path="memory/integration/fact",
            payload={"content": "project codename is ORION", "content_type": "text/plain"},
        )
    ]

    result = await service.get("/memory/integration/fact", level="detail")

    assert result is not None
    assert result["content"] == "project codename is ORION"
    assert result["content_type"] == "text/plain"


@pytest.mark.asyncio
async def test_list_includes_workspace_context_rows():
    service = _loaded_service()
    service._workspace_contexts = [  # type: ignore[list-item]
        _FakeContext(
            path="/memory/integration/fact",
            payload={"glance": "project codename"},
        )
    ]

    results = await service.list("/memory", level="glance")

    assert results == [
        {
            "path": "/memory/integration/fact",
            "glance": "project codename",
        }
    ]
