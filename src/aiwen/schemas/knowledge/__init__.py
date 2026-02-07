"""Knowledge domain schemas."""

from aiwen.schemas.knowledge.document import (
    DocumentCreate,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from aiwen.schemas.knowledge.knowledge import (
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
