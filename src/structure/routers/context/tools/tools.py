"""
Tool Management API

All tools are imported from MCP servers. Inner (built-in) tools are
exposed through the dedicated MCP server (structure-mcp) and can be imported
from there like any other MCP server.
"""

import logging
import time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.dependencies.agents import get_context_crud
from structure.core.dependencies.auth import get_current_user
from structure.core.enums import ContextType
from structure.extensions.database import get_structure_db
from structure.registries.core import ToolRegistry
from structure.schemas.auth.user import UserResponse
from structure.schemas.context.context_schema import ContextListResponse
from structure.schemas.context.tools.user_tool import (
    UserToolListResponse,
    UserToolResponse,
    UserToolTestRequest,
    UserToolTestResponse,
)
from structure.services.context.context_crud import ContextCRUD
from structure.services.context.tools.tool_crud import ToolCRUD

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


def _build_user_tool_response(tool) -> UserToolResponse:
    return UserToolResponse.model_validate(tool)


# ─── MCP schemas ──────────────────────────────────────────────────────────────

class MCPServerConfig(BaseModel):
    transport: Literal["sse", "stdio"] = "sse"
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None

    @model_validator(mode="after")
    def _check(self) -> "MCPServerConfig":
        if self.transport == "sse" and not self.url:
            raise ValueError("url is required for sse transport")
        if self.transport == "stdio" and not self.command:
            raise ValueError("command is required for stdio transport")
        return self


class MCPToolInfo(BaseModel):
    name: str
    description: str
    input_schema: dict | None = None


class MCPProbeResponse(BaseModel):
    success: bool
    tools: list[MCPToolInfo] = []
    error: str | None = None


class MCPImportRequest(MCPServerConfig):
    tool_names: list[str]
    is_public: bool = False


class MCPImportResponse(BaseModel):
    imported: list[str]
    skipped: list[str]
    failed: list[str]
    bundle_id: str | None = None
    bundle_name: str | None = None


def _mcp_client_config(req: MCPServerConfig) -> str | dict:
    from structure.registries.mcp_loader import client_config_from_tool_config
    cfg = {
        "mcp_transport": req.transport,
        "mcp_url": req.url,
        "mcp_command": req.command,
        "mcp_args": req.args,
        "mcp_env": req.env,
    }
    return client_config_from_tool_config(cfg)


# ============================================================================
# Tool CRUD - List, Get, Delete, Toggle (MCP tools only)
# ============================================================================

@router.get(
    "/",
    response_model=UserToolListResponse,
    summary="List MCP tools",
)
async def list_tools(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
    workspace_id: UUID | None = None,
    enabled_only: bool = True,
    include_public: bool = True,
    tags: str | None = None,
):
    """List all MCP tools accessible to the current user."""
    crud = ToolCRUD(db)
    tag_list = [tag.strip() for tag in tags.split(",")] if tags else None
    tools = await crud.list_user_tools(
        user_id=current_user.id,
        workspace_id=workspace_id,
        enabled_only=enabled_only,
        include_public=include_public,
        tool_type="mcp",
        tags=tag_list,
    )
    tool_responses = [_build_user_tool_response(tool) for tool in tools]
    return UserToolListResponse(tools=tool_responses, total=len(tool_responses))


@router.get(
    "/{tool_id}/context",
    response_model=ContextListResponse,
    summary="View synced context entries for a tool",
)
async def get_tool_context(
    tool_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
    context_crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    # Resolve the tool to find its owner — for public tools, use the owner's
    # user_id so that context created by admin is visible to all users.
    crud = ToolCRUD(db)
    tool = await crud.get_tool_by_id(tool_id, current_user.id)
    if not tool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    context_user_id = tool.user_id or current_user.id

    skip = (page - 1) * page_size
    items, total = await context_crud.list(
        user_id=context_user_id,
        context_type=ContextType.TOOL,
        source_id=str(tool_id),
        skip=skip,
        limit=page_size,
    )
    return ContextListResponse(total=total, items=items, page=page, page_size=page_size)


@router.get(
    "/{tool_id}",
    response_model=UserToolResponse,
    summary="Get tool by ID",
)
async def get_tool(
    tool_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    crud = ToolCRUD(db)
    tool = await crud.get_tool_by_id(tool_id, current_user.id)
    if not tool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    return _build_user_tool_response(tool)


@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an MCP tool",
)
async def delete_tool(
    tool_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    crud = ToolCRUD(db)
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type == "inner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in tools cannot be deleted",
        )
    success = await crud.delete_tool(tool_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )
    from structure.celery_worker.tasks.context_sync_tasks import (
        delete_resource_contexts,
    )
    delete_resource_contexts.delay(str(tool_id), "tool", "tool_id")


@router.post(
    "/{tool_id}/toggle",
    response_model=UserToolResponse,
    summary="Enable/disable a tool",
)
async def toggle_tool(
    tool_id: UUID,
    enabled: bool,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    crud = ToolCRUD(db)
    tool = await crud.toggle_enabled(tool_id, current_user.id, enabled)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )
    return _build_user_tool_response(tool)


@router.post(
    "/{tool_id}/test",
    response_model=UserToolTestResponse,
    summary="Test a tool with sample parameters",
)
async def test_tool(
    tool_id: UUID,
    test_request: UserToolTestRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
):
    """Test an MCP tool by executing it with the provided parameters."""
    crud = ToolCRUD(db)
    tool = await crud.get_tool_by_id(tool_id, current_user.id)
    if not tool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")

    start_time = time.monotonic()

    try:
        if tool.tool_type == "inner":
            tool_instance = ToolRegistry.get_tool_instance(tool.name)
            if not tool_instance:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Inner tool '{tool.name}' not found in registry",
                )
            result = await tool_instance(**test_request.parameters)
        elif tool.tool_type == "mcp":
            from structure.registries.mcp_loader import build_mcp_tool_class
            tool_cls = build_mcp_tool_class(tool)
            result = await tool_cls()(**test_request.parameters)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported tool type: {tool.tool_type}",
            )

        elapsed_ms = (time.monotonic() - start_time) * 1000
        return UserToolTestResponse(
            success=result.get("success", False),
            message=result.get("message"),
            data=result.get("data"),
            error=result.get("error"),
            execution_time_ms=round(elapsed_ms, 2),
            tool_name=tool.name,
            tool_id=str(tool.id),
        )

    except HTTPException:
        raise
    except Exception as e:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.error("Tool test failed for %s (id=%s): %s", tool.name, tool_id, e, exc_info=True)
        return UserToolTestResponse(
            success=False,
            error=str(e),
            execution_time_ms=round(elapsed_ms, 2),
            tool_name=tool.name,
            tool_id=str(tool.id),
        )


# ============================================================================
# MCP — Probe & Import
# ============================================================================

@router.post(
    "/probe-mcp",
    response_model=MCPProbeResponse,
    summary="Probe an MCP server and list its tools",
)
async def probe_mcp(
    body: MCPServerConfig,
    current_user: Annotated[UserResponse, Depends(get_current_user)],  # noqa: ARG001
) -> MCPProbeResponse:
    """Connect to an MCP server and return all tools it exposes."""
    from structure.registries.mcp_loader import probe_mcp_server

    try:
        raw_tools = await probe_mcp_server(_mcp_client_config(body))
        return MCPProbeResponse(
            success=True,
            tools=[MCPToolInfo(**t) for t in raw_tools],
        )
    except Exception as exc:
        logger.warning("MCP probe failed: %s", exc)
        return MCPProbeResponse(success=False, error=str(exc))


@router.post(
    "/import-from-mcp",
    response_model=MCPImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import selected MCP tools into the tool library",
)
async def import_from_mcp(
    body: MCPImportRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_structure_db)],
) -> MCPImportResponse:
    """Probe the MCP server, then bulk-create Tool records for the requested tools."""
    from structure.models.context.tools.tool import Tool as ToolModel
    from structure.registries.mcp_loader import probe_mcp_server

    client_config = _mcp_client_config(body)

    try:
        raw_tools = await probe_mcp_server(client_config)
    except Exception as exc:
        raise HTTPException(  # noqa: B904
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to MCP server: {exc}",
        )

    tool_map = {t["name"]: t for t in raw_tools}
    requested = set(body.tool_names)
    unknown = requested - tool_map.keys()

    imported: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    result_bundle_id: str | None = None
    result_bundle_name: str | None = None

    base_cfg: dict = {"mcp_transport": body.transport}
    if body.transport == "sse":
        base_cfg["mcp_url"] = body.url
    else:
        base_cfg["mcp_command"] = body.command
        if body.args:
            base_cfg["mcp_args"] = body.args
        if body.env:
            base_cfg["mcp_env"] = body.env

    for name in body.tool_names:
        if name in unknown:
            failed.append(name)
            continue

        mcp_tool = tool_map[name]

        from sqlalchemy import (
            or_,
            select as sa_select,
        )
        exists_stmt = sa_select(ToolModel.id).where(
            ToolModel.name == name,
            ToolModel.tool_type == "mcp",
            or_(
                ToolModel.user_id == current_user.id,
                ToolModel.is_public == True,  # noqa: E712
            ),
        )
        existing = (await db.execute(exists_stmt)).first()
        if existing:
            skipped.append(name)
            continue

        try:
            tool_cfg = {**base_cfg, "mcp_tool_name": name}
            record = ToolModel(
                name=name,
                tool_code=f"mcp__{name}",
                display_name=name,
                description=mcp_tool.get("description") or "",
                tool_type="mcp",
                input_schema=mcp_tool.get("input_schema"),
                config=tool_cfg,
                category="mcp",
                tags=["mcp"],
                user_id=current_user.id,
                is_public=body.is_public,
                enabled=True,
            )
            db.add(record)
            imported.append(name)
        except Exception as exc:
            logger.error("Failed to create MCP tool '%s': %s", name, exc)
            failed.append(name)

    if imported:
        await db.commit()

        # Invalidate dynamic tool cache so workers pick up new tools.
        from structure.registries.dynamic_loader import DynamicToolLoader
        DynamicToolLoader.invalidate_cache(user_id=current_user.id)

        # Dispatch context sync tasks for newly imported tools
        try:
            from sqlalchemy import select as _sel

            from structure.celery_worker.tasks.context_sync_tasks import (
                sync_tool_to_contexts,
            )

            sync_stmt = _sel(ToolModel).where(
                ToolModel.name.in_(imported),
                ToolModel.user_id == current_user.id,
            )
            synced_tools = (await db.execute(sync_stmt)).scalars().all()
            for t in synced_tools:
                sync_tool_to_contexts.delay(str(t.id), str(current_user.id))
            logger.info("Dispatched context sync for %d MCP tools", len(synced_tools))
        except Exception as exc:
            logger.warning("Failed to dispatch context sync tasks: %s", exc)

        try:
            from sqlalchemy import select as sa_select
            from sqlalchemy.orm import selectinload

            from structure.models.context.tools.tool_bundle import (
                ToolBundle,
                ToolBundleItem,
            )

            if body.transport == "sse":
                server_id = body.url
                server_name = body.url.rstrip("/").split("/")[-1] or body.url
            else:
                server_id = body.command
                server_name = body.args[0] if body.args else body.command

            bundle_stmt = (
                sa_select(ToolBundle)
                .options(selectinload(ToolBundle.items))
                .where(
                    ToolBundle.bundle_type == "mcp",
                    ToolBundle.source == server_id,
                    ToolBundle.user_id == current_user.id,
                )
            )
            bundle = (await db.execute(bundle_stmt)).scalar_one_or_none()
            if not bundle:
                bundle = ToolBundle(
                    bundle_type="mcp",
                    source=server_id,
                    name=server_name,
                    user_id=current_user.id,
                    is_public=body.is_public,
                    tags=["mcp"],
                )
                db.add(bundle)
                await db.flush()
                existing_item_ids: set = set()
            else:
                existing_item_ids = {item.tool_id for item in bundle.items}

            tools_stmt = sa_select(ToolModel).where(
                ToolModel.name.in_(imported),
                ToolModel.user_id == current_user.id,
            )
            tools = (await db.execute(tools_stmt)).scalars().all()
            for idx, tool in enumerate(tools):
                if tool.id not in existing_item_ids:
                    db.add(ToolBundleItem(bundle_id=bundle.id, tool_id=tool.id, position=idx))

            await db.commit()
            result_bundle_id = str(bundle.id)
            result_bundle_name = bundle.name
        except Exception as exc:
            logger.error("Failed to create MCP toolset: %s", exc)

    return MCPImportResponse(
        imported=imported,
        skipped=skipped,
        failed=failed,
        bundle_id=result_bundle_id,
        bundle_name=result_bundle_name,
    )
