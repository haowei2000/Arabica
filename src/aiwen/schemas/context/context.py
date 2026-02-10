"""Pydantic schemas for Context API endpoints."""

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from aiwen.schemas.agents.app import ContextType

if TYPE_CHECKING:
    from aiwen.schemas.knowledge.knowledge import KnowledgeResponse
    from aiwen.schemas.tools.tool import ToolResponse


class ContextCreate(BaseModel):
    """Schema for creating a new context entry."""

    context_type: ContextType = Field(
        default=ContextType.CONVERSATION,
        description="Context type: conversation, message, user_memory, skill, tool, knowledge, chunk",
    )
    source_id: str | UUID | None = Field(
        None, description="Related source ID (e.g., knowledge_id, conversation_id)"
    )
    content: str = Field(..., min_length=1, description="Context content")
    summary: str | None = Field(None, description="Context summary")
    keywords: list[str] | None = Field(None, description="Keywords for search")
    embedding_384: list[float] | None = Field(None, description="384-dim embedding")
    embedding_768: list[float] | None = Field(None, description="768-dim embedding")
    embedding_1024: list[float] | None = Field(None, description="1024-dim embedding")
    embedding_1536: list[float] | None = Field(None, description="1536-dim embedding")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int = Field(
        default=0, ge=0, le=100, description="Importance score 0-100"
    )


class ContextUpdate(BaseModel):
    """Schema for updating a context entry."""

    context_type: ContextType | None = Field(None, description="Context type")
    source_id: str | UUID | None = Field(None, description="Related source ID")
    content: str | None = Field(None, min_length=1, description="Context content")
    summary: str | None = Field(None, description="Context summary")
    keywords: list[str] | None = Field(None, description="Keywords for search")
    embedding_384: list[float] | None = Field(None, description="384-dim embedding")
    embedding_768: list[float] | None = Field(None, description="768-dim embedding")
    embedding_1024: list[float] | None = Field(None, description="1024-dim embedding")
    embedding_1536: list[float] | None = Field(None, description="1536-dim embedding")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int | None = Field(None, ge=0, le=100, description="Importance score")


class ContextResponse(BaseModel):
    """Schema for context response."""

    id: str
    user_id: str
    source_id: str | None = None
    context_type: str
    content: str
    summary: str | None = None
    keywords: list[str] | None = None
    meta: dict[str, Any] | None = None
    importance: int | None = None
    created_at: datetime
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def convert_uuids(cls, data: Any) -> Any:
        """Convert UUIDs to strings."""
        if hasattr(data, "__dict__"):
            result = {}
            for field_name in cls.model_fields.keys():
                value = getattr(data, field_name, None)
                if isinstance(value, UUID):
                    result[field_name] = str(value)
                else:
                    result[field_name] = value
            return result
        return data

    class Config:
        from_attributes = True


class ToolContextInput(BaseModel):
    """Schema for converting tool content into a context entry."""

    tool_id: str | UUID | None = Field(None, description="Tool ID")
    tool_code: str | None = Field(None, description="Unique tool code")
    name: str = Field(..., min_length=1, description="Tool display name")
    description: str | None = Field(None, description="Tool description")
    input_schema: dict[str, Any] | None = Field(
        None, description="JSON Schema for tool input parameters"
    )
    keywords: list[str] | None = Field(None, description="Keywords for search")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int = Field(default=0, ge=0, le=100, description="Importance score")

    def to_context_create(self) -> ContextCreate:
        parts = [f"Tool: {self.name}"]
        if self.tool_code:
            parts.append(f"Code: {self.tool_code}")
        if self.description:
            parts.append(f"Description: {self.description}")
        if self.input_schema:
            schema_text = json.dumps(self.input_schema, ensure_ascii=True, indent=2)
            parts.append(f"Input Schema:\n{schema_text}")
        content = "\n".join(parts)
        return ContextCreate(
            context_type=ContextType.TOOL,
            source_id=self.tool_id,
            content=content,
            summary=self.description,
            keywords=self.keywords,
            meta=self.meta,
            importance=self.importance,
        )

    @classmethod
    def from_tool_response(cls, tool: "ToolResponse") -> "ToolContextInput":
        return cls(
            tool_id=tool.id,
            tool_code=tool.tool_code,
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            meta={
                "tool_type": tool.tool_type,
                "version": tool.version,
                "enabled": tool.enabled,
                "is_public": tool.is_public,
            },
        )


class UserMemoryContextInput(BaseModel):
    """Schema for converting user memory into a context entry."""

    memory: str = Field(..., min_length=1, description="User memory content")
    source_id: str | UUID | None = Field(
        None, description="Related source ID (e.g., conversation_id)"
    )
    summary: str | None = Field(None, description="Memory summary")
    keywords: list[str] | None = Field(None, description="Keywords for search")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int = Field(default=50, ge=0, le=100, description="Importance score")

    def to_context_create(self) -> ContextCreate:
        return ContextCreate(
            context_type=ContextType.USER_MEMORY,
            source_id=self.source_id,
            content=self.memory,
            summary=self.summary,
            keywords=self.keywords,
            meta=self.meta,
            importance=self.importance,
        )


class SkillContextInput(BaseModel):
    """Schema for converting skill content into a context entry."""

    skill_id: str | UUID | None = Field(None, description="Skill ID")
    name: str = Field(..., min_length=1, description="Skill name")
    description: str | None = Field(None, description="Skill description")
    content: str = Field(..., min_length=1, description="Skill content or instructions")
    keywords: list[str] | None = Field(None, description="Keywords for search")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int = Field(default=0, ge=0, le=100, description="Importance score")

    def to_context_create(self) -> ContextCreate:
        parts = [f"Skill: {self.name}"]
        if self.description:
            parts.append(f"Description: {self.description}")
        parts.append(f"Content:\n{self.content}")
        content = "\n".join(parts)
        return ContextCreate(
            context_type=ContextType.SKILL,
            source_id=self.skill_id,
            content=content,
            summary=self.description,
            keywords=self.keywords,
            meta=self.meta,
            importance=self.importance,
        )


class KnowledgeContextInput(BaseModel):
    """Schema for converting knowledge content into a context entry."""

    knowledge_id: str | UUID | None = Field(None, description="Knowledge base ID")
    name: str = Field(..., min_length=1, description="Knowledge base name")
    description: str | None = Field(None, description="Knowledge base description")
    keywords: list[str] | None = Field(None, description="Keywords for search")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int = Field(default=0, ge=0, le=100, description="Importance score")

    def to_context_create(self) -> ContextCreate:
        parts = [f"Knowledge Base: {self.name}"]
        if self.description:
            parts.append(f"Description: {self.description}")
        content = "\n".join(parts)
        return ContextCreate(
            context_type=ContextType.KNOWLEDGE,
            source_id=self.knowledge_id,
            content=content,
            summary=self.description,
            keywords=self.keywords,
            meta=self.meta,
            importance=self.importance,
        )

    @classmethod
    def from_knowledge_response(
        cls, knowledge: "KnowledgeResponse"
    ) -> "KnowledgeContextInput":
        return cls(
            knowledge_id=knowledge.id,
            name=knowledge.name,
            description=knowledge.description,
            meta={
                "provider": knowledge.provider,
                "indexing_technique": knowledge.indexing_technique,
                "embedding_model": knowledge.embedding_model,
                "permission": knowledge.permission,
                "status": knowledge.status,
            },
        )


class ContextFromSource(BaseModel):
    """Schema for converting different content types into a context entry."""

    tool: ToolContextInput | None = None
    user_memory: UserMemoryContextInput | None = None
    skill: SkillContextInput | None = None
    knowledge: KnowledgeContextInput | None = None

    @model_validator(mode="after")
    def validate_single_source(self) -> "ContextFromSource":
        provided = [
            self.tool,
            self.user_memory,
            self.skill,
            self.knowledge,
        ]
        if sum(item is not None for item in provided) != 1:
            raise ValueError(
                "Provide exactly one of: tool, user_memory, skill, knowledge."
            )
        return self

    def to_context_create(self) -> ContextCreate:
        if self.tool:
            return self.tool.to_context_create()
        if self.user_memory:
            return self.user_memory.to_context_create()
        if self.skill:
            return self.skill.to_context_create()
        if self.knowledge:
            return self.knowledge.to_context_create()
        raise ValueError("No context source provided.")


class ContextWithScore(ContextResponse):
    """Context response with similarity score for vector search."""

    score: float = Field(..., description="Similarity score (cosine distance)")


class ContextListResponse(BaseModel):
    """Schema for paginated list of contexts."""

    total: int = Field(..., description="Total number of contexts")
    items: list[ContextResponse] = Field(..., description="List of contexts")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")


class ContextSearchResponse(BaseModel):
    """Schema for vector search results."""

    total: int = Field(..., description="Total number of results")
    items: list[ContextWithScore] = Field(
        ..., description="List of contexts with scores"
    )


class VectorSearchRequest(BaseModel):
    """Schema for vector similarity search request."""

    embedding: list[float] = Field(..., description="Query embedding vector")
    dimension: Literal[384, 768, 1024, 1536] = Field(
        default=1536, description="Embedding dimension to search"
    )
    context_type: ContextType | None = Field(None, description="Filter by context type")
    source_id: str | UUID | None = Field(None, description="Filter by source ID")
    top_k: int = Field(
        default=10, ge=1, le=100, description="Number of results to return"
    )
    threshold: float | None = Field(
        None, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )


class GrepSearchRequest(BaseModel):
    """Schema for text grep search request."""

    query: str = Field(..., min_length=1, description="Search query string")
    context_type: ContextType | None = Field(None, description="Filter by context type")
    source_id: str | UUID | None = Field(None, description="Filter by source ID")
    search_in: list[Literal["content", "summary", "keywords"]] = Field(
        default=["content", "summary"], description="Fields to search in"
    )
    case_sensitive: bool = Field(default=False, description="Case sensitive search")
    skip: int = Field(default=0, ge=0, description="Number of records to skip")
    limit: int = Field(
        default=20, ge=1, le=100, description="Maximum results to return"
    )
