"""Knowledge domain schemas."""

from structure.schemas.context.knowledge.document import (
    DocumentCreate,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from structure.schemas.context.knowledge.knowledge import (
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
