"""Knowledge domain schemas."""

from aiwen.schemas.context.knowledge.document import (
    DocumentCreate,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from aiwen.schemas.context.knowledge.knowledge import (
    KnowledgeCreate,
    KnowledgeListResponse,
    KnowledgeResponse,
    KnowledgeUpdate,
)

__all__ = [
    "DocumentCreate",
    "DocumentListResponse",
    "DocumentResponse",
    "DocumentUploadResponse",
    "KnowledgeCreate",
    "KnowledgeListResponse",
    "KnowledgeResponse",
    "KnowledgeUpdate",
]
