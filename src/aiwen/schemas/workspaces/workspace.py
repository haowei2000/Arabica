# aiwen/schemas/workspace/workspace.py
"""Pydantic schemas for Workspace API endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from aiwen.core.enums import (
    InvitationStatus,
    MemberRole,
    WorkspaceStatus,
    WorkspaceVisibility,
)
from aiwen.utils.schema_mixins import ResponseMixin


class WorkspaceContextConfig(BaseModel):
    """Resources to pre-populate into workspace context on creation."""

    tool_ids: list[UUID] = Field(default_factory=list, description="工具 ID 列表")
    knowledge_ids: list[UUID] = Field(default_factory=list, description="知识库 ID 列表")
    skill_ids: list[UUID] = Field(default_factory=list, description="技能 ID 列表")
    source_workspace_ids: list[UUID] = Field(
        default_factory=list,
        description="从这些工作区复制历史对话记录到新工作区 context",
    )
    memory_ids: list[UUID] = Field(
        default_factory=list,
        description="加载到 workspace context 的用户记忆 ID 列表",
    )
    trigger_ids: list[UUID] = Field(
        default_factory=list,
        description="从用户级触发器模板复制到 workspace 的触发器 ID 列表",
    )


class WorkspaceCreate(BaseModel):
    """Schema for creating a new workspace."""

    name: str = Field(..., min_length=1, max_length=255, description="工作空间名称")
    description: str | None = Field(None, max_length=1000, description="工作空间描述")
    app_id: str | UUID | None = Field(None, description="Default app ID")
    visibility: WorkspaceVisibility = Field(
        WorkspaceVisibility.PRIVATE, description="可见性"
    )
    settings: dict[str, Any] | None = Field(None, description="工作空间配置")
    context_config: WorkspaceContextConfig | None = Field(
        None, description="创建时预置到 workspace context 的资源"
    )


class WorkspaceUpdate(BaseModel):
    """Schema for updating a workspace."""

    name: str | None = Field(
        None, min_length=1, max_length=255, description="工作空间名称"
    )
    description: str | None = Field(None, max_length=1000, description="工作空间描述")
    app_id: str | UUID | None = Field(None, description="Default app ID")
    visibility: WorkspaceVisibility | None = Field(None, description="可见性")
    is_shared: bool | None = Field(None, description="是否共享")
    settings: dict[str, Any] | None = Field(None, description="工作空间配置")
    status: WorkspaceStatus | None = Field(None, description="状态")


class WorkspaceResponse(ResponseMixin, BaseModel):
    """Schema for workspace response."""

    id: str
    name: str
    description: str | None = None
    owner_id: str
    app_id: str | UUID | None = Field(None, description="Default app ID")
    visibility: str
    is_shared: bool
    settings: dict[str, Any] | None = None
    status: str
    run_count: int
    member_count: int
    legacy_conversation_id: str | None = None
    # created_at, updated_at, UUID conversion, ORM config inherited from ResponseMixin


class WorkspaceListResponse(BaseModel):
    """Schema for paginated list of workspaces."""

    total: int = Field(..., description="工作空间总数")
    items: list[WorkspaceResponse] = Field(..., description="工作空间列表")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")


class WorkspaceMemberCreate(BaseModel):
    """Schema for adding a workspace member."""

    user_id: str | UUID = Field(..., description="用户ID")
    role: MemberRole = Field(MemberRole.VIEWER, description="成员角色")


class WorkspaceMemberUpdate(BaseModel):
    """Schema for updating a workspace member."""

    role: MemberRole | None = Field(None, description="成员角色")
    invitation_status: InvitationStatus | None = Field(None, description="邀请状态")


class WorkspaceMemberResponse(ResponseMixin, BaseModel):
    """Schema for workspace member response."""

    id: str
    workspace_id: str
    user_id: str
    role: str
    invited_by: str | None = None
    invitation_status: str
    joined_at: datetime | None = None
    # created_at, updated_at, UUID conversion, ORM config inherited from ResponseMixin
