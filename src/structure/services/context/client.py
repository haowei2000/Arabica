import asyncio
import logging
import os
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_manager = None


def _get_manager():
    global _manager
    if _manager is None:
        from structure.services.context_service.manager import ContextManager

        data_root = os.environ.get("CONTEXT_SERVICE_DATA_ROOT", "/app/data/context")
        _manager = ContextManager(data_root=data_root)
    return _manager


class ContextServiceClient:
    """In-process client for context service (file-based storage).

    Replaces the previous HTTP client that called a separate context-service
    container. All operations now run directly in the backend process.
    """

    async def create_context(
        self, workspace_id: UUID, path: str, content: str, **kwargs
    ) -> dict[str, Any]:
        from structure.services.context_service.models import ContextCreateRequest

        request = ContextCreateRequest(path=path, content=content, **kwargs)
        result = await asyncio.to_thread(_get_manager().create_context, workspace_id, request)
        return result.model_dump(mode="json")

    async def get_context(self, workspace_id: UUID, path: str) -> dict[str, Any] | None:
        result = await asyncio.to_thread(_get_manager().get_context, workspace_id, path)
        if result is None:
            return None
        return result.model_dump(mode="json")

    async def delete_context(self, workspace_id: UUID, path: str) -> bool:
        return await asyncio.to_thread(_get_manager().delete_context, workspace_id, path)

    async def list_contexts(
        self, workspace_id: UUID, prefix: str = "", recursive: bool = False
    ) -> dict[str, Any]:
        items = await asyncio.to_thread(
            _get_manager().list_contexts, workspace_id, prefix, recursive
        )
        return {
            "workspace_id": str(workspace_id),
            "total": len(items),
            "items": [item.model_dump(mode="json") for item in items],
        }


# Singleton instance
context_service_client = ContextServiceClient()
