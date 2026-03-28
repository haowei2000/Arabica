"""REST API endpoints for document upload and management."""

import hashlib
import logging
from typing import Annotated
from uuid import uuid4

logger = logging.getLogger(__name__)

from celery.result import AsyncResult
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel

from aiwen.celery_worker.celery_app import celery_app, example_task
from aiwen.celery_worker.tasks.knowledge_tasks import process_document_structured
from aiwen.core.dependencies.agents import get_context_crud, get_document_crud, get_knowledge_crud
from aiwen.core.dependencies.auth import get_current_user
from aiwen.extensions.storage.global_storage import get_global_s3_storage
from aiwen.schemas.auth.user import UserResponse
from aiwen.schemas.context.context_schema import ContextListResponse
from aiwen.schemas.context.knowledge.document import (
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from aiwen.services.context.context_crud import ContextCRUD
from aiwen.services.context.knowledge.document_crud import DocumentCRUD
from aiwen.services.context.knowledge.knowledge_crud import KnowledgeCRUD


class TaskStatusResponse(BaseModel):
    """Schema for task status response."""

    task_id: str
    status: str
    progress: int | None = None
    result: dict | None = None
    error: str | None = None


router = APIRouter(prefix="/document", tags=["document"])


@router.get("/test-celery")
async def test_celery():
    """Test if Celery is working by sending a default task."""
    try:
        result = example_task.delay()
        return {
            "message": "Task submitted successfully",
            "task_id": result.id,
            "status": result.status,
        }
    except Exception as e:
        return {
            "message": "Failed to submit task",
            "error": str(e),
        }


_CODE_EXTENSIONS = {
    "py", "js", "ts", "tsx", "jsx", "java", "go", "rb", "cpp", "c", "h", "cs",
    "php", "swift", "rs", "kt", "scala", "sh", "bash", "lua", "r", "sql",
    "vue", "svelte", "dart", "ex", "exs", "ml", "hs", "clj", "elm",
}


def detect_structure_type(filename: str, mime_type: str | None) -> str:
    """Infer structuring strategy from filename and MIME type."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "csv" or (mime_type and "csv" in mime_type):
        return "table"
    if ext in _CODE_EXTENSIONS:
        return "code"
    if ext in {"md", "markdown"} or (mime_type and "markdown" in mime_type):
        return "markdown"
    return "document"


def compute_file_hash(content: bytes) -> str:
    """Compute SHA256 hash of file content.

    Args:
        content: File content as bytes.

    Returns:
        str: SHA256 hash as hex string.
    """
    return hashlib.sha256(content).hexdigest()


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    document_crud: Annotated[DocumentCRUD, Depends(get_document_crud)],
    knowledge_crud: Annotated[KnowledgeCRUD, Depends(get_knowledge_crud)],
    file: UploadFile = File(..., description="File to upload"),
    knowledge_id: str = Form(..., description="Knowledge base ID"),
    embedding_provider: str = Form(default="tongyi", description="Embedding provider"),
    embedding_model: str = Form(default="text-embedding-v3", description="Embedding model"),
    embedding_dimension: int = Form(default=1024, description="Embedding dimension"),
    structure_type: str = Form(default="document", description="Structuring strategy: document | table | code"),
):
    """
    Upload a document to a knowledge base.

    The document will be:
    1. Uploaded to S3 storage
    2. Parsed and structured into semantic sections via Celery
    3. Each section stored directly in the Context table with embeddings

    Args:
        file: The file to upload
        knowledge_id: ID of the knowledge base
        embedding_provider: Provider for embeddings
        embedding_model: ChatLLM for embeddings
        embedding_dimension: Dimension of embeddings
        current_user: Current authenticated user
        document_crud: Document CRUD service
        knowledge_crud: Knowledge CRUD service

    Returns:
        Document details and task ID for tracking

    Raises:
        HTTPException: If knowledge base not found or unauthorized
    """
    # Validate knowledge base exists and user has access
    knowledge = await knowledge_crud.get_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base {knowledge_id} not found",
        )
    if str(knowledge.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to upload to this knowledge base",
        )

    # Read file content
    content = await file.read()
    file_size = len(content)

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot upload empty file",
        )

    # Compute file hash (kept for tracking, but no longer blocks duplicates)
    file_hash = compute_file_hash(content)

    # Generate object key: {knowledge_id}/{uuid}_{filename}
    original_name = file.filename or "unknown"
    object_key = f"{knowledge_id}/{uuid4()}_{original_name}"

    # Upload to S3 (RustFS)
    try:
        storage = get_global_s3_storage()
        bucket_name = storage.bucket
        storage.put_bytes(
            key=object_key,
            data=content,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file to storage: {e!s}",
        )
    # Create document record
    document = await document_crud.create(
        knowledge_id=knowledge_id,
        user_id=current_user.id,
        original_name=original_name,
        object_key=object_key,
        file_size=file_size,
        file_hash=file_hash,
        mime_type=file.content_type,
        storage_type="s3",
        bucket_name=bucket_name,
    )

    # Increment knowledge document count
    await knowledge_crud.increment_document_count(knowledge_id)

    # Trigger Celery task chain
    logger.info(f"Triggering document processing for document_id={document.id}")

    task_id = process_document_structured(
        document_id=str(document.id),
        knowledge_id=knowledge_id,
        object_key=object_key,
        mime_type=file.content_type or "application/octet-stream",
        user_id=str(current_user.id),
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        structure_type=structure_type,
    )

    return DocumentUploadResponse(
        document=DocumentResponse.model_validate(document),
        task_id=task_id,
    )


class FolderUploadResponse(BaseModel):
    """Schema for folder upload response — one entry per file."""

    uploads: list[DocumentUploadResponse]
    total: int
    failed: int


@router.post(
    "/upload-folder",
    response_model=FolderUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_folder(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    document_crud: Annotated[DocumentCRUD, Depends(get_document_crud)],
    knowledge_crud: Annotated[KnowledgeCRUD, Depends(get_knowledge_crud)],
    files: list[UploadFile] = File(..., description="All files in the folder"),
    knowledge_id: str = Form(..., description="Knowledge base ID"),
    embedding_provider: str = Form(default="tongyi", description="Embedding provider"),
    embedding_model: str = Form(default="text-embedding-v3", description="Embedding model"),
    embedding_dimension: int = Form(default=1024, description="Embedding dimension"),
):
    """
    Upload an entire folder to a knowledge base.

    Each file's structuring strategy is auto-detected from its extension:
    - CSV → table
    - Source code files → code
    - Everything else → document

    Files that fail to upload are skipped; the response reports how many failed.
    """
    # Validate knowledge base
    knowledge = await knowledge_crud.get_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base {knowledge_id} not found",
        )
    if str(knowledge.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to upload to this knowledge base",
        )

    uploads: list[DocumentUploadResponse] = []
    failed = 0

    for upload_file in files:
        original_name = upload_file.filename or "unknown"
        try:
            content = await upload_file.read()
            if not content:
                logger.warning(f"Skipping empty file: {original_name}")
                failed += 1
                continue

            file_size = len(content)
            file_hash = compute_file_hash(content)
            structure_type = detect_structure_type(original_name, upload_file.content_type)
            object_key = f"{knowledge_id}/{uuid4()}_{original_name}"

            storage = get_global_s3_storage()
            bucket_name = storage.bucket
            storage.put_bytes(
                key=object_key,
                data=content,
                content_type=upload_file.content_type or "application/octet-stream",
            )

            document = await document_crud.create(
                knowledge_id=knowledge_id,
                user_id=current_user.id,
                original_name=original_name,
                object_key=object_key,
                file_size=file_size,
                file_hash=file_hash,
                mime_type=upload_file.content_type,
                storage_type="s3",
                bucket_name=bucket_name,
            )

            await knowledge_crud.increment_document_count(knowledge_id)

            task_id = process_document_structured(
                document_id=str(document.id),
                knowledge_id=knowledge_id,
                object_key=object_key,
                mime_type=upload_file.content_type or "application/octet-stream",
                user_id=str(current_user.id),
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                embedding_dimension=embedding_dimension,
                structure_type=structure_type,
            )

            uploads.append(
                DocumentUploadResponse(
                    document=DocumentResponse.model_validate(document),
                    task_id=task_id,
                )
            )
            logger.info(
                f"Folder upload: queued {original_name} as {structure_type}, task={task_id}"
            )
        except Exception as e:
            logger.error(f"Folder upload: failed to process {original_name}: {e}")
            failed += 1

    return FolderUploadResponse(uploads=uploads, total=len(files), failed=failed)


@router.get("/task/{task_id}/status", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
):
    """
    Get the status of a document processing task.

    Args:
        task_id: The Celery task ID
        current_user: Current authenticated user

    Returns:
        Task status information
    """
    if task_id == "duplicate":
        return TaskStatusResponse(
            task_id=task_id,
            status="SUCCESS",
            progress=100,
            result={"message": "Document already exists"},
        )

    try:
        result = AsyncResult(task_id, app=celery_app)
        task_status = result.status

        response = TaskStatusResponse(
            task_id=task_id,
            status=task_status,
        )

        if task_status == "PROGRESS":
            response.progress = result.info.get("progress", 0) if result.info else 0
        elif task_status == "SUCCESS":
            response.progress = 100
            response.result = (
                result.result
                if isinstance(result.result, dict)
                else {"result": str(result.result)}
            )
        elif task_status == "FAILURE":
            response.error = str(result.result) if result.result else "Unknown error"

        return response
    except Exception as e:
        # Redis connection error or other issues - return PENDING status
        return TaskStatusResponse(
            task_id=task_id,
            status="PENDING",
            error=f"Unable to fetch task status: {e!s}",
        )


@router.get("/knowledge/{knowledge_id}", response_model=DocumentListResponse)
async def list_documents_by_knowledge(
    knowledge_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    document_crud: Annotated[DocumentCRUD, Depends(get_document_crud)],
    knowledge_crud: Annotated[KnowledgeCRUD, Depends(get_knowledge_crud)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
):
    """
    List documents in a knowledge base.

    Args:
        knowledge_id: The knowledge base ID
        page: Page number (starting from 1)
        page_size: Number of items per page
        current_user: Current authenticated user
        document_crud: Document CRUD service
        knowledge_crud: Knowledge CRUD service

    Returns:
        Paginated list of documents

    Raises:
        HTTPException: If knowledge base not found or unauthorized
    """
    # Validate knowledge base exists and user has access
    knowledge = await knowledge_crud.get_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Knowledge base {knowledge_id} not found",
        )
    if str(knowledge.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to access this knowledge base",
        )

    skip = (page - 1) * page_size
    items, total = await document_crud.list(
        knowledge_id=knowledge_id,
        user_id=current_user.id,
        skip=skip,
        limit=page_size,
    )

    return DocumentListResponse(
        total=total,
        items=items,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}/context", response_model=ContextListResponse)
async def get_document_context(
    document_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    context_crud: Annotated[ContextCRUD, Depends(get_context_crud)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(100, ge=1, le=1000, description="Items per page"),
):
    """Return all Context chunks synced from the given document."""
    skip = (page - 1) * page_size
    items, total = await context_crud.list_by_document_id(
        document_id=document_id,
        user_id=current_user.id,
        skip=skip,
        limit=page_size,
    )
    return ContextListResponse(total=total, items=items, page=page, page_size=page_size)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    document_crud: Annotated[DocumentCRUD, Depends(get_document_crud)],
):
    """
    Get document by ID.

    Args:
        document_id: The document ID
        current_user: Current authenticated user
        document_crud: Document CRUD service

    Returns:
        Document details

    Raises:
        HTTPException: If document not found or unauthorized
    """
    document = await document_crud.get_by_id_and_user(document_id, current_user.id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )
    return document


@router.post("/{document_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    document_crud: Annotated[DocumentCRUD, Depends(get_document_crud)],
    knowledge_crud: Annotated[KnowledgeCRUD, Depends(get_knowledge_crud)],
):
    """
    Delete a document and its chunks.

    Args:
        document_id: The document ID
        current_user: Current authenticated user
        document_crud: Document CRUD service
        knowledge_crud: Knowledge CRUD service

    Raises:
        HTTPException: If document not found or unauthorized
    """
    # Get document first to get knowledge_id
    document = await document_crud.get_by_id_and_user(document_id, current_user.id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )

    # Soft delete the document
    await document_crud.delete(document_id, current_user.id)

    # Decrement knowledge document count
    await knowledge_crud.increment_document_count(
        str(document.knowledge_id), increment=-1
    )

    return


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    document_crud: Annotated[DocumentCRUD, Depends(get_document_crud)],
):
    """
    Download the original document file.

    Args:
        document_id: The document ID
        current_user: Current authenticated user
        document_crud: Document CRUD service

    Returns:
        StreamingResponse with the file content

    Raises:
        HTTPException: If document not found or unauthorized
    """
    document = await document_crud.get_by_id_and_user(document_id, current_user.id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )

    try:
        storage = get_global_s3_storage()
        file_data = storage.get_bytes(document.object_key)

        # Determine content type
        content_type = document.mime_type or "application/octet-stream"

        # URL encode the filename for Content-Disposition header
        from urllib.parse import quote

        encoded_filename = quote(document.original_name)

        return Response(
            content=file_data,
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
                "Content-Length": str(len(file_data)),
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download file: {e!s}",
        )


class DocumentPreviewResponse(BaseModel):
    """Schema for document preview response."""

    document_id: str
    original_name: str
    mime_type: str | None
    content: str | None
    content_length: int


@router.get("/{document_id}/preview", response_model=DocumentPreviewResponse)
async def preview_document(
    document_id: str,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    document_crud: Annotated[DocumentCRUD, Depends(get_document_crud)],
):
    """
    Get a preview of the document content (parsed text).

    Args:
        document_id: The document ID
        current_user: Current authenticated user
        document_crud: Document CRUD service

    Returns:
        Document preview with parsed text content

    Raises:
        HTTPException: If document not found or unauthorized
    """
    document = await document_crud.get_by_id_and_user(document_id, current_user.id)
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )

    return DocumentPreviewResponse(
        document_id=str(document.id),
        original_name=document.original_name,
        mime_type=document.mime_type,
        content=document.content,
        content_length=len(document.content) if document.content else 0,
    )
