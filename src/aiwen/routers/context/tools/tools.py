"""
Unified Tool Management API

Provides REST endpoints for managing tools (both inner and external).

Key concepts:
- **Inner tools**: Code-defined tools registered at startup (read-only via API)
- **External tools**: User-created tools that delegate to inner tool backends
- **Tool templates**: Pre-configured examples for creating external tools

All tools run through the same unified ``execute()`` / ``__call__()`` protocol.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.core.dependencies.auth import get_current_user
from aiwen.extensions.database import get_aiwen_db
from aiwen.registries.core import ToolRegistry
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.context.tools.tool_template import (
    TOOL_TEMPLATES,
    ToolTemplate,
    ToolTemplateListResponse,
    get_all_templates,
    get_inner_tool_templates,
    tool_record_to_template,
)
from aiwen.schemas.context.tools.user_tool import (
    InnerToolInfo,
    InnerToolListResponse,
    ToolExportData,
    UserToolCreate,
    UserToolListResponse,
    UserToolResponse,
    UserToolTestRequest,
    UserToolTestResponse,
    UserToolUpdate,
)
from aiwen.services.context.tools.tool_crud import ToolCRUD

from typing import Literal

from pydantic import BaseModel, model_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])

# Tool types that cannot be created or modified via API
PROTECTED_TOOL_TYPES = {"inner"}


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


def _build_user_tool_response(tool) -> UserToolResponse:
    """Build a UserToolResponse from a Tool model instance.

    Args:
        tool: Tool model instance

    Returns:
        UserToolResponse with tool_code and inner_tool_name populated
    """
    return UserToolResponse.model_validate(tool)


# ============================================================================
# Tool Templates - Pre-configured examples for creating tools
# ============================================================================


@router.get(
    "/templates",
    response_model=ToolTemplateListResponse,
    summary="List available tool templates",
)
async def list_templates(
    source: str | None = None,
):
    """
    List all available tool creation templates.

    Templates provide pre-filled request bodies for creating external tools.
    Use a template as a starting point, then customize the name, URL,
    parameters, etc. to fit your use case.

    Two sources of templates are available:
    - **static**: Hand-crafted examples (HTTP GET/POST, webhook, code, etc.)
    - **inner_tool**: Auto-generated from every registered InnerTool via ``to_template()``

    Args:
        source: Optional filter by template source ("static" or "inner_tool")
    """
    templates = get_all_templates(source=source)
    return ToolTemplateListResponse(templates=templates, total=len(templates))


@router.get(
    "/templates/{template_id}",
    response_model=ToolTemplate,
    summary="Get a specific tool template",
)
async def get_template(template_id: str):
    """
    Get a specific tool template by ID.

    Returns a pre-filled UserToolCreate body that can be directly
    POST-ed to ``/tools/`` (after customizing name, URL, etc.).

    Template IDs include:
    - Static templates: ``http_get_api``, ``http_post_json``, ``http_webhook``,
      ``http_rest_crud``, ``http_form_submit``, ``code_basic``
    - Dynamic templates: ``inner_<tool_name>`` for each registered InnerTool
    """
    # Check static templates first, then dynamic
    template = TOOL_TEMPLATES.get(template_id)
    if not template:
        dynamic = get_inner_tool_templates()
        template = dynamic.get(template_id)

    if not template:
        # Build a list of all available IDs for the error message
        all_ids = list(TOOL_TEMPLATES.keys()) + list(get_inner_tool_templates().keys())
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found. "
            f"Available: {', '.join(sorted(all_ids))}",
        )
    return template


@router.get(
    "/{tool_id}/template",
    response_model=ToolTemplate,
    summary="Get a tool creation template derived from an existing tool",
)
async def get_tool_as_template(
    tool_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Generate a ToolTemplate from an existing tool record.

    Useful for cloning or deriving a new tool from any existing tool.
    The returned template is a pre-filled UserToolCreate body that the
    frontend can load into the create form.

    - For **inner tools** in the registry, the richer class-level
      ``InnerTool.to_template()`` data is used.
    - For **external user tools**, the template is derived from the DB record.

    Args:
        tool_id: UUID of the source tool.
        current_user: Authenticated user.
        db: Database session.

    Returns:
        ToolTemplate ready to be loaded into the create dialog.

    Raises:
        HTTPException 404: If tool not found or no access.
    """
    crud = ToolCRUD(db)
    tool = await crud.get_tool_by_id(tool_id, current_user.id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    # For inner tools, prefer the richer registry-level template
    if tool.tool_type == "inner":
        dynamic = get_inner_tool_templates()
        tpl = dynamic.get(f"inner_{tool.name}")
        if tpl:
            return tpl

    return tool_record_to_template(tool)


# ============================================================================
# Tool CRUD - Create, Read, Update, Delete external tools
# ============================================================================


@router.post(
    "/",
    response_model=UserToolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new external tool",
)
async def create_tool(
    tool_data: UserToolCreate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Create a new user-defined external tool.

    External tools delegate execution to a registered InnerTool backend.
    Specify `inner_tool_name` to choose which InnerTool to delegate to.

    Args:
        tool_data: Tool creation data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created tool information

    Raises:
        HTTPException 400: If validation fails or tool name already exists
    """
    crud = ToolCRUD(db)

    try:
        logger.info(f"Creating tool: name={tool_data.name}, user_id={current_user.id}")
        tool = await crud.create_tool(current_user.id, tool_data)
        from aiwen.celery_worker.tasks.context_sync_tasks import sync_tool_to_contexts
        sync_tool_to_contexts.delay(str(tool.id), str(current_user.id))
        logger.info(f"Tool created successfully: id={tool.id}, name={tool.name}")
        return _build_user_tool_response(tool)
    except ValueError as e:
        logger.warning(f"Tool creation failed: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error creating tool: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create tool: {str(e)}"
        )


@router.get(
    "/",
    response_model=UserToolListResponse,
    summary="List tools",
)
async def list_tools(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    workspace_id: UUID | None = None,
    enabled_only: bool = True,
    include_public: bool = True,
    tool_type: str | None = None,
    tags: str | None = None,
):
    """
    List all tools accessible to the current user.

    Returns:
    - User's own tools (both private and public)
    - Public tools from other users
    - Inner (built-in) tools

    Args:
        workspace_id: Optional filter by workspace
        enabled_only: If True, only return enabled tools
        include_public: If True, include public tools from other users
        tool_type: Optional filter - "inner" for built-in, "external" for user-defined
        tags: Optional comma-separated tags filter (e.g., "api,search")
        current_user: Current authenticated user
        db: Database session

    Returns:
        List of tools with pagination info
    """
    crud = ToolCRUD(db)

    # Parse tags from comma-separated string
    tag_list = [tag.strip() for tag in tags.split(",")] if tags else None

    tools = await crud.list_user_tools(
        user_id=current_user.id,
        workspace_id=workspace_id,
        enabled_only=enabled_only,
        include_public=include_public,
        tool_type=tool_type,
        tags=tag_list,
    )

    tool_responses = [_build_user_tool_response(tool) for tool in tools]

    return UserToolListResponse(tools=tool_responses, total=len(tool_responses))


@router.get(
    "/{tool_id}",
    response_model=UserToolResponse,
    summary="Get tool by ID",
)
async def get_tool(
    tool_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Get a specific tool by ID.

    Args:
        tool_id: Tool UUID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Tool information

    Raises:
        HTTPException 404: If tool not found
        HTTPException 403: If user doesn't have access to private tool
    """
    crud = ToolCRUD(db)

    tool = await crud.get_tool_by_id(tool_id, current_user.id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    return _build_user_tool_response(tool)


@router.patch(
    "/{tool_id}",
    response_model=UserToolResponse,
    summary="Update an external tool",
)
async def update_tool(
    tool_id: UUID,
    tool_data: UserToolUpdate,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Update an existing external tool (must be owner).

    Inner tools are code-defined and cannot be modified via API.

    Args:
        tool_id: Tool UUID
        tool_data: Update data
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated tool information

    Raises:
        HTTPException 400: If trying to update an inner tool
        HTTPException 404: If tool not found
        HTTPException 403: If user is not the owner
    """
    crud = ToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type in PROTECTED_TOOL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be modified",
        )

    tool = await crud.update_tool(tool_id, current_user.id, tool_data)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )

    return _build_user_tool_response(tool)


@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tool",
)
async def delete_tool(
    tool_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Delete a tool (must be owner).

    Inner tools are code-defined and cannot be deleted via API.

    Args:
        tool_id: Tool UUID
        current_user: Current authenticated user
        db: Database session

    Raises:
        HTTPException 400: If trying to delete an inner tool
        HTTPException 404: If tool not found
        HTTPException 403: If user is not the owner
    """
    crud = ToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type in PROTECTED_TOOL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be deleted",
        )

    success = await crud.delete_tool(tool_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )


@router.post(
    "/{tool_id}/toggle",
    response_model=UserToolResponse,
    summary="Enable/disable a tool",
)
async def toggle_tool(
    tool_id: UUID,
    enabled: bool,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Enable or disable a tool.

    Inner tools cannot be toggled via API.

    Args:
        tool_id: Tool UUID
        enabled: New enabled state
        current_user: Current authenticated user
        db: Database session

    Returns:
        Updated tool information

    Raises:
        HTTPException 403: If trying to toggle an inner tool
        HTTPException 404: If tool not found or no permission
    """
    crud = ToolCRUD(db)

    # Check if it's an inner tool
    tool = await crud.get_tool_by_id(tool_id)
    if tool and tool.tool_type in PROTECTED_TOOL_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in (inner) tools cannot be toggled",
        )

    tool = await crud.toggle_enabled(tool_id, current_user.id, enabled)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found or you don't have permission",
        )

    return _build_user_tool_response(tool)


# ============================================================================
# Tool Import / Export
# ============================================================================


@router.get(
    "/{tool_id}/export",
    response_model=ToolExportData,
    summary="Export a tool as JSON",
)
async def export_tool(
    tool_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Export a tool's configuration as a portable JSON object.

    The returned data can be saved to a file and later imported via
    ``POST /tools/import`` to recreate the tool.

    Args:
        tool_id: Tool UUID
        current_user: Current authenticated user
        db: Database session

    Returns:
        ToolExportData with all configuration fields

    Raises:
        HTTPException 404: If tool not found
    """
    crud = ToolCRUD(db)
    tool = await crud.get_tool_by_id(tool_id, current_user.id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    return ToolExportData(
        name=tool.name,
        display_name=tool.display_name,
        description=tool.description,
        execution_mode=getattr(tool, "execution_mode", None) or "inner",
        input_schema=tool.input_schema or {},
        output_schema=tool.output_schema,
        category=tool.category or "custom",
        tags=tool.tags or [],
        timeout=tool.timeout or 30,
        inner_tool_name=tool.inner_tool_name,
        parameter_mapping=tool.parameter_mapping,
        chain=tool.chain,
    )


@router.post(
    "/import",
    response_model=UserToolResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a tool from exported JSON",
)
async def import_tool(
    export_data: ToolExportData,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Import a tool from a previously exported JSON configuration.

    Creates a new tool with the same configuration as the exported data.

    Args:
        export_data: Exported tool configuration
        current_user: Current authenticated user
        db: Database session

    Returns:
        Created tool information

    Raises:
        HTTPException 400: If validation fails or tool name already exists
    """
    tool_data = UserToolCreate(
        name=export_data.name,
        display_name=export_data.display_name,
        description=export_data.description,
        input_schema=export_data.input_schema,
        output_schema=export_data.output_schema,
        category=export_data.category,
        tags=export_data.tags,
        timeout=export_data.timeout,
        inner_tool_name=export_data.inner_tool_name,
        parameter_mapping=export_data.parameter_mapping,
        chain=export_data.chain,
    )

    crud = ToolCRUD(db)
    try:
        tool = await crud.create_tool(current_user.id, tool_data)
        from aiwen.celery_worker.tasks.context_sync_tasks import sync_tool_to_contexts
        sync_tool_to_contexts.delay(str(tool.id), str(current_user.id))
        return _build_user_tool_response(tool)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ============================================================================
# Tool Loading & Execution - Dynamic tool registration and testing
# ============================================================================


@router.post(
    "/load",
    summary="Load user tools into agent",
)
async def load_user_tools(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
    workspace_id: UUID | None = None,
):
    """
    Load user tools into the agent's toolset.

    This endpoint dynamically loads tools from the database and registers
    them with the ToolRegistry so they can be used by agents.

    Args:
        workspace_id: Optional workspace filter
        current_user: Current authenticated user
        db: Database session

    Returns:
        Success message with count of loaded tools
    """
    loader = DynamicToolLoader(db)

    loaded_tools = await loader.load_user_tools(current_user.id, workspace_id)

    return {
        "success": True,
        "message": f"Loaded {len(loaded_tools)} tools",
        "tools": loaded_tools,
    }


@router.post(
    "/{tool_id}/reload",
    summary="Reload a specific tool",
)
async def reload_tool(
    tool_id: UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Reload a specific tool.

    Useful after updating a tool to apply changes immediately.

    Args:
        tool_id: Tool UUID
        current_user: Current authenticated user
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException 404: If tool not found
    """
    loader = DynamicToolLoader(db)

    success = await loader.reload_tool(tool_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    return {"success": True, "message": "Tool reloaded successfully"}





# ============================================================================
# Tool Registry - Introspection endpoints for registered tools
# ============================================================================


@router.get(
    "/registry/all",
    summary="Get all registered tools",
)
async def get_registered_tools():
    """
    Get all tools currently registered in the ToolRegistry.

    This includes both built-in tools and user-defined tools.

    Returns:
        List of tool metadata from the registry
    """
    tools = ToolRegistry.list_tools()
    tool_info = [ToolRegistry.get_tool_info(name) for name in tools]

    return {"tools": tool_info, "total": len(tools)}


@router.get(
    "/registry/schemas",
    summary="Get tool schemas for agent",
)
async def get_tool_schemas(format: str = "openai"):
    """
    Get JSON schemas of all registered tools.

    These schemas can be passed to LLM for function calling.

    Args:
        format: Schema format - "openai" (default) or "langchain"

    Returns:
        Tool schemas in the requested format
    """
    schemas = ToolRegistry.get_all_schemas(format=format)
    return {"schemas": schemas, "total": len(schemas)}


@router.get(
    "/registry/inner-tools",
    response_model=InnerToolListResponse,
    summary="List available inner tools with schemas",
)
async def get_inner_tools():
    """
    Get all registered InnerTools with their input/output schemas.

    Used by the frontend to display available tools when creating
    an external tool that wraps an inner tool (parameter mapping).

    Returns:
        List of inner tools with full schema information, sorted by (category, name).
    """
    from aiwen.core.interfaces.tool import InnerTool

    inner_tools: list[InnerToolInfo] = []

    for tool_name in ToolRegistry.list_tools(enabled_only=False):
        tool_class = ToolRegistry.get_tool_class(tool_name)
        if tool_class is None:
            continue
        if not (issubclass(tool_class, InnerTool) and tool_class is not InnerTool):
            continue

        metadata = tool_class.METADATA
        inner_tools.append(
            InnerToolInfo(
                name=metadata.name,
                display_name=metadata.display_name,
                description=metadata.description,
                category=metadata.category,
                tags=metadata.tags,
                timeout=metadata.timeout,
                input_schema=tool_class.InputSchema.model_json_schema(),
                output_schema=tool_class.OutputSchema.model_json_schema(),
            )
        )

    inner_tools.sort(key=lambda t: (t.category, t.name))

    return InnerToolListResponse(inner_tools=inner_tools, total=len(inner_tools))


@router.post(
    "/{tool_id}/test",
    response_model=UserToolTestResponse,
    summary="Test a tool with sample parameters",
)
async def test_tool(
    tool_id: UUID,
    test_request: UserToolTestRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
):
    """
    Test a tool by executing it with the provided parameters.

    Works for both inner tools and external (user-defined) tools.
    Returns the execution result along with timing information.

    Args:
        tool_id: Tool UUID
        test_request: Test parameters
        current_user: Current authenticated user
        db: Database session

    Returns:
        Test result with success/error, data, and execution time

    Raises:
        HTTPException 404: If tool not found
    """
    import time

    crud = ToolCRUD(db)
    tool = await crud.get_tool_by_id(tool_id, current_user.id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found"
        )

    start_time = time.monotonic()

    try:
        if tool.tool_type == "inner":
            # Inner tool: get instance directly from registry
            tool_instance = ToolRegistry.get_tool_instance(tool.name)
            if not tool_instance:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Inner tool '{tool.name}' not found in registry",
                )
            result = await tool_instance(**test_request.parameters)
        elif tool.tool_type == "mcp":
            from aiwen.registries.mcp_loader import build_mcp_tool_class

            tool_cls = build_mcp_tool_class(tool)
            tool_instance = tool_cls()
            result = await tool_instance(**test_request.parameters)
        else:
            # External tool: create dynamic class and execute
            from aiwen.registries.dynamic_loader import DynamicToolLoader

            tool_cls = DynamicToolLoader.create_tool_class(tool)
            tool_instance = tool_cls()
            result = await tool_instance(**test_request.parameters)

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
        logger.error(
            "Tool test failed for %s (id=%s): %s",
            tool.name,
            tool_id,
            e,
            exc_info=True,
        )
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


def _mcp_client_config(req: MCPServerConfig) -> str | dict:
    from aiwen.registries.mcp_loader import client_config_from_tool_config
    cfg = {
        "mcp_transport": req.transport,
        "mcp_url": req.url,
        "mcp_command": req.command,
        "mcp_args": req.args,
        "mcp_env": req.env,
    }
    return client_config_from_tool_config(cfg)


@router.post(
    "/probe-mcp",
    response_model=MCPProbeResponse,
    summary="Probe an MCP server and list its tools",
)
async def probe_mcp(
    body: MCPServerConfig,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> MCPProbeResponse:
    """Connect to an MCP server and return all tools it exposes.

    Use this endpoint before importing to preview what will be added.
    """
    from aiwen.registries.mcp_loader import probe_mcp_server

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
    db: Annotated[AsyncSession, Depends(get_aiwen_db)],
) -> MCPImportResponse:
    """Probe the MCP server, then bulk-create Tool records for the requested tools.

    Each imported tool gets ``tool_type="mcp"`` and stores the connection
    details in ``config``.  After import the tool is available to any
    executor via ``DynamicToolLoader`` — no workspace binding required.
    """
    from aiwen.registries.mcp_loader import probe_mcp_server
    from aiwen.models.context.tools.tool import Tool as ToolModel

    client_config = _mcp_client_config(body)

    # Probe the server to get schemas
    try:
        raw_tools = await probe_mcp_server(client_config)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to MCP server: {exc}",
        )

    tool_map = {t["name"]: t for t in raw_tools}
    requested = set(body.tool_names)
    unknown = requested - tool_map.keys()

    imported: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []

    # Build mcp_config (stored in Tool.config)
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

        # Skip if a tool with this name already belongs to this user
        from sqlalchemy import select as sa_select
        exists_stmt = sa_select(ToolModel.id).where(
            ToolModel.name == name,
            ToolModel.tool_type == "mcp",
            ToolModel.user_id == current_user.id,
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

        # Auto-create/update MCP toolset for this server
        try:
            from aiwen.models.context.tools.tool_bundle import ToolBundle, ToolBundleItem
            from sqlalchemy import select as sa_select
            from sqlalchemy.orm import selectinload

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

            tools_stmt = sa_select(ToolModel).where(
                ToolModel.name.in_(imported),
                ToolModel.user_id == current_user.id,
            )
            tools = (await db.execute(tools_stmt)).scalars().all()

            existing_item_ids = {item.tool_id for item in bundle.items}
            for idx, tool in enumerate(tools):
                if tool.id not in existing_item_ids:
                    db.add(ToolBundleItem(bundle_id=bundle.id, tool_id=tool.id, position=idx))

            await db.commit()
        except Exception as exc:
            logger.error("Failed to create MCP toolset: %s", exc)

    return MCPImportResponse(imported=imported, skipped=skipped, failed=failed)

