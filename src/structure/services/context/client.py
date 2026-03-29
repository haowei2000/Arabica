import httpx
import logging
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


class ContextServiceClient:
    """Client for the separate Context Service."""

    def __init__(self, base_url: Optional[str] = None):
        if base_url:
            self.base_url = base_url
        else:
            try:
                from structure.config.factory import get_settings
                self.base_url = getattr(get_settings(), "CONTEXT_SERVICE_URL", "http://context-service:8080")
            except Exception:
                self.base_url = "http://context-service:8080"

    async def create_context(
        self,
        workspace_id: UUID,
        path: str,
        content: str,
        **kwargs
    ) -> dict[str, Any]:
        """Create or update a context entry."""
        payload = {
            "path": path,
            "content": content,
            **kwargs
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/workspaces/{workspace_id}/context",
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()

    async def get_context(self, workspace_id: UUID, path: str) -> Optional[dict[str, Any]]:
        """Retrieve a context entry."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/workspaces/{workspace_id}/context/{path}",
                    timeout=10.0
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Error calling context-service: {e}")
                return None

    async def delete_context(self, workspace_id: UUID, path: str) -> bool:
        """Delete a context entry."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{self.base_url}/workspaces/{workspace_id}/context/{path}",
                timeout=10.0
            )
            return response.status_code == 200

    async def list_contexts(
        self, workspace_id: UUID, prefix: str = "", recursive: bool = False
    ) -> dict[str, Any]:
        """List context entries."""
        params = {"prefix": prefix, "recursive": recursive}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/workspaces/{workspace_id}/list",
                params=params,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()


# Singleton instance
context_service_client = ContextServiceClient()
