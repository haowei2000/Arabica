"""Pydantic schemas for ContextSchema API endpoints."""

from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from aiwen.core.enums import ContextType
from aiwen.core.types import ContextPath
from aiwen.schemas.context.knowledge.knowledge import KnowledgeResponse
from aiwen.schemas.context.tools.tool import ToolResponse
from aiwen.utils.schema_mixins import ResponseMixin


class ContextSchema(BaseModel):
    """Schema for context configuration."""

    type: ContextType
    enabled: bool = Field(default=True, description="Whether the context is enabled")
    config: dict[str, Any] | None = Field(None, description="ContextSchema configuration")


class ContextCreate(BaseModel):
    """Schema for creating a new context entry."""

    context_type: ContextType = Field(
        default=ContextType.CONVERSATION,
        description="ContextSchema type: conversation, message, user_memory, skill, tool, knowledge, chunk",
    )
    source_id: str | UUID | None = Field(
        None, description="Related source ID (e.g., knowledge_id, conversation_id)"
    )
    path: ContextPath | None = Field(
        None, description="Virtual folder path (e.g. '/projects/demo/docs')"
    )
    s3_key: str | None = Field(
        None, description="S3 object key for storing large content"
    )
    # Progressive disclosure layers
    glance: str | None = Field(
        None, max_length=512, description="One-line summary for quick scanning (Layer 1)"
    )
    summary: str | None = Field(
        None, description="Structured overview summary (Layer 2)"
    )
    content: str = Field(
        ..., min_length=1, description="Full content detail (Layer 3)"
    )
    # Tags and keywords
    tags: list[str] | None = Field(
        None, description="Tags for filtering and categorization"
    )
    keywords: list[str] | None = Field(
        None, description="Keywords for search (deprecated, use tags instead)"
    )
    embedding_384: list[float] | None = Field(None, description="384-dim embedding")
    embedding_768: list[float] | None = Field(None, description="768-dim embedding")
    embedding_1024: list[float] | None = Field(None, description="1024-dim embedding")
    embedding_1536: list[float] | None = Field(None, description="1536-dim embedding")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int = Field(
        default=0, ge=0, le=100, description="Importance score 0-100"
    )

    @model_validator(mode="after")
    def validate_embeddings(self) -> ContextCreate:
        """Validate that embedding dimensions match their expected sizes."""
        embeddings = [
            (self.embedding_384, 384),
            (self.embedding_768, 768),
            (self.embedding_1024, 1024),
            (self.embedding_1536, 1536),
        ]
        for embedding, expected_dim in embeddings:
            if embedding is not None and len(embedding) != expected_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {expected_dim}, got {len(embedding)}"
                )
        return self

    def get_embedding_by_dimension(
        self, dimension: Literal[384, 768, 1024, 1536]
    ) -> list[float] | None:
        """Get embedding by dimension size.

        Args:
            dimension: The embedding dimension (384, 768, 1024, or 1536)

        Returns:
            The embedding vector or None if not available
        """
        mapping = {
            384: self.embedding_384,
            768: self.embedding_768,
            1024: self.embedding_1024,
            1536: self.embedding_1536,
        }
        return mapping.get(dimension)


class ContextUpdate(BaseModel):
    """Schema for updating a context entry."""

    context_type: ContextType | None = Field(None, description="ContextSchema type")
    source_id: str | UUID | None = Field(None, description="Related source ID")
    path: ContextPath | None = Field(None, description="Virtual folder path")
    s3_key: str | None = Field(None, description="S3 object key for storing large content")
    # Progressive disclosure layers
    glance: str | None = Field(None, max_length=512, description="One-line summary")
    summary: str | None = Field(None, description="Structured overview summary")
    content: str | None = Field(None, min_length=1, description="Full content detail")
    # Tags and keywords
    tags: list[str] | None = Field(None, description="Tags for filtering")
    keywords: list[str] | None = Field(None, description="Keywords (deprecated)")
    embedding_384: list[float] | None = Field(None, description="384-dim embedding")
    embedding_768: list[float] | None = Field(None, description="768-dim embedding")
    embedding_1024: list[float] | None = Field(None, description="1024-dim embedding")
    embedding_1536: list[float] | None = Field(None, description="1536-dim embedding")
    meta: dict[str, Any] | None = Field(None, description="Additional metadata")
    importance: int | None = Field(None, ge=0, le=100, description="Importance score")

    @model_validator(mode="after")
    def validate_embeddings(self) -> ContextUpdate:
        """Validate that embedding dimensions match their expected sizes."""
        embeddings = [
            (self.embedding_384, 384),
            (self.embedding_768, 768),
            (self.embedding_1024, 1024),
            (self.embedding_1536, 1536),
        ]
        for embedding, expected_dim in embeddings:
            if embedding is not None and len(embedding) != expected_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {expected_dim}, got {len(embedding)}"
                )
        return self


class ContextResponse(ResponseMixin, BaseModel):
    """Schema for context response (without embeddings for efficiency)."""

    id: str
    user_id: str
    source_id: str | None = None
    path: str | None = None
    s3_key: str | None = Field(
        None, description="S3 object key for retrieving actual content"
    )
    context_type: str
    # Progressive disclosure layers
    glance: str | None = Field(None, description="One-line summary (Layer 1)")
    summary: str | None = Field(None, description="Overview summary (Layer 2)")
    content: str = Field(..., description="Full content (Layer 3)")
    # Tags and metadata
    tags: list[str] | None = Field(None, description="Tags for categorization")
    keywords: list[str] | None = Field(None, description="Keywords (deprecated)")
    meta: dict[str, Any] | None = None
    importance: int | None = None
    # created_at, updated_at, UUID conversion, ORM config inherited from ResponseMixin

    @property
    def has_s3_content(self) -> bool:
        """Check if this context has content stored in S3."""
        return self.s3_key is not None and len(self.s3_key) > 0

    @property
    def content_preview(self) -> str:
        """Get a preview of the content (first 200 characters)."""
        if len(self.content) <= 200:
            return self.content
        return self.content[:200] + "..."

    def disclose(self, level: Literal["glance", "overview", "detail"] = "overview") -> dict[str, Any]:
        """Progressive disclosure of information.

        Args:
            level: Disclosure level - "glance", "overview", or "detail"

        Returns:
            Dictionary with appropriate level of information
        """
        result: dict[str, Any] = {"path": self.path}

        # Layer 1: Glance
        if self.glance:
            result["glance"] = self.glance
        elif self.summary:
            result["glance"] = self.summary[:100] + "..." if len(self.summary) > 100 else self.summary
        else:
            result["glance"] = self.content_preview

        if level == "glance":
            return result

        # Layer 2: Overview
        if level in ("overview", "detail"):
            if self.summary:
                result["overview"] = self.summary
            if self.tags:
                result["tags"] = self.tags

        if level == "overview":
            return result

        # Layer 3: Detail
        if level == "detail":
            result["content"] = self.content
            result["meta"] = self.meta or {}
            result["context_type"] = self.context_type
            result["importance"] = self.importance
            if self.s3_key:
                result["s3_key"] = self.s3_key
            if self.keywords:
                result["keywords"] = self.keywords

        return result


class ContextWithEmbeddingsResponse(ContextResponse):
    """Schema for context response including all embedding vectors.

    Use this when you need the full context data including embeddings,
    such as for reindexing or migration purposes. For normal API responses,
    use ContextResponse to save bandwidth.

    Note: Inherits glance, summary, content, tags fields from ContextResponse.
    """

    embedding_384: list[float] | None = Field(None, description="384-dim embedding")
    embedding_768: list[float] | None = Field(None, description="768-dim embedding")
    embedding_1024: list[float] | None = Field(None, description="1024-dim embedding")
    embedding_1536: list[float] | None = Field(None, description="1536-dim embedding")

    def get_embedding_by_dimension(
        self, dimension: Literal[384, 768, 1024, 1536]
    ) -> list[float] | None:
        """Get embedding by dimension size.

        Args:
            dimension: The embedding dimension (384, 768, 1024, or 1536)

        Returns:
            The embedding vector or None if not available
        """
        mapping = {
            384: self.embedding_384,
            768: self.embedding_768,
            1024: self.embedding_1024,
            1536: self.embedding_1536,
        }
        return mapping.get(dimension)

    @property
    def available_embeddings(self) -> list[int]:
        """Get list of available embedding dimensions."""
        return [
            dim
            for dim, embedding in [
                (384, self.embedding_384),
                (768, self.embedding_768),
                (1024, self.embedding_1024),
                (1536, self.embedding_1536),
            ]
            if embedding is not None
        ]


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
    def from_tool_response(cls, tool: ToolResponse) -> ToolContextInput:
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
            context_type=ContextType.SHORT_MEMORY,
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
        cls, knowledge: KnowledgeResponse
    ) -> KnowledgeContextInput:
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
    def validate_single_source(self) -> ContextFromSource:
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
    """ContextSchema response with similarity score for vector search."""

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
