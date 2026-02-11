"""Knowledge domain services - document processing and CRUD."""

from aiwen.services.knowledge.chunker import TextChunker
from aiwen.services.knowledge.document_crud import DocumentCRUD
from aiwen.services.knowledge.embeddings import EmbeddingService
from aiwen.services.knowledge.knowledge_crud import KnowledgeCRUD
from aiwen.services.knowledge.parser import DocumentParser

__all__ = [
    "DocumentCRUD",
    "DocumentParser",
    "EmbeddingService",
    "KnowledgeCRUD",
    "TextChunker",
]
