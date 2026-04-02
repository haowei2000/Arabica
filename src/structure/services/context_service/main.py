from typing import Optional
from uuid import UUID

from fastapi import FastAPI, HTTPException

from .manager import ContextManager
from .models import ContextCreateRequest, ContextListResponse, ContextResponse

app = FastAPI(title="Structure Context Service")
manager = ContextManager(data_root="/app/data/context")


@app.post("/workspaces/{workspace_id}/context", response_model=ContextResponse)
async def create_context(workspace_id: UUID, request: ContextCreateRequest):
    """Create or update a context entry."""
    try:
        return manager.create_context(workspace_id, request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))  # noqa: B904


@app.get("/workspaces/{workspace_id}/context/{path:path}", response_model=ContextResponse)
async def get_context(workspace_id: UUID, path: str):
    """Retrieve a context entry."""
    ctx = manager.get_context(workspace_id, path)
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")
    return ctx


@app.delete("/workspaces/{workspace_id}/context/{path:path}")
async def delete_context(workspace_id: UUID, path: str):
    """Delete a context entry."""
    success = manager.delete_context(workspace_id, path)
    if not success:
        raise HTTPException(status_code=404, detail="Context not found")
    return {"status": "deleted"}


@app.get("/workspaces/{workspace_id}/list", response_model=ContextListResponse)
async def list_contexts(
    workspace_id: UUID, prefix: str = "", recursive: bool = False
):
    """List context entries."""
    items = manager.list_contexts(workspace_id, prefix, recursive)
    return ContextListResponse(
        workspace_id=workspace_id,
        total=len(items),
        items=items
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
