from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
from uuid import UUID

from .models import ContextCreateRequest, ContextMetadata, ContextResponse

logger = logging.getLogger(__name__)


class ContextManager:
    """Manager for file-based workspace context with 'file' command metadata."""

    def __init__(self, data_root: str = "/data"):
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)

    def _get_workspace_dir(self, workspace_id: UUID) -> Path:
        """Get the base directory for a workspace."""
        path = self.data_root / str(workspace_id)
        path.mkdir(exist_ok=True)
        return path

    def _get_context_path(self, workspace_id: UUID, virtual_path: str) -> Path:
        """Map a virtual path to a physical directory."""
        # Ensure virtual_path is safe and absolute-like
        safe_path = virtual_path.strip("/")
        return self._get_workspace_dir(workspace_id) / safe_path

    def _get_metadata_file(self, context_dir: Path) -> Path:
        """Get the metadata file path within a context directory."""
        return context_dir / ".metadata.json"

    def _get_content_file(self, context_dir: Path) -> Path:
        """Get the content file path within a context directory."""
        return context_dir / "content"

    def _run_file_command(self, file_path: Path, args: list[str]) -> str | None:
        """Run the Linux 'file' command and return the output."""
        try:
            result = subprocess.run(
                ["file"] + args + [str(file_path)],  # noqa: RUF005
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except Exception as e:
            logger.error(f"Error running 'file' command: {e}")
            return None

    def _identify_file(self, file_path: Path) -> tuple[str | None, str | None]:
        """Identify MIME type and description using the 'file' command."""
        mime_type = self._run_file_command(file_path, ["--mime-type", "-b"])
        description = self._run_file_command(file_path, ["-b"])
        return mime_type, description

    def create_context(
        self, workspace_id: UUID, request: ContextCreateRequest
    ) -> ContextResponse:
        """Create a new context entry (file and metadata)."""
        context_dir = self._get_context_path(workspace_id, request.path)
        context_dir.mkdir(parents=True, exist_ok=True)

        content_file = self._get_content_file(context_dir)
        metadata_file = self._get_metadata_file(context_dir)

        # Write content
        content = request.content or ""
        content_file.write_text(content, encoding="utf-8")

        # Identify using 'file' command
        mime_type, file_desc = self._identify_file(content_file)

        # Build metadata
        meta = ContextMetadata(
            workspace_id=workspace_id,
            path=request.path,
            name=request.name or Path(request.path).name or "unnamed",
            content_type=request.content_type or mime_type,
            glance=request.glance or file_desc,
            summary=request.summary,
            size_bytes=content_file.stat().st_size,
            tags=request.tags,
            meta=request.meta,
            expires_at=request.expires_at,
        )

        metadata_file.write_text(meta.model_dump_json(), encoding="utf-8")

        return ContextResponse(
            path=meta.path,
            name=meta.name,
            content_type=meta.content_type,
            glance=meta.glance,
            summary=meta.summary,
            content=content,
            size_bytes=meta.size_bytes,
            tags=meta.tags,
            meta=meta.meta,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
        )

    def get_context(
        self, workspace_id: UUID, virtual_path: str
    ) -> ContextResponse | None:
        """Retrieve a context entry."""
        context_dir = self._get_context_path(workspace_id, virtual_path)
        metadata_file = self._get_metadata_file(context_dir)
        content_file = self._get_content_file(context_dir)

        if not metadata_file.exists():
            return None

        meta_dict = json.loads(metadata_file.read_text(encoding="utf-8"))
        content = (
            content_file.read_text(encoding="utf-8") if content_file.exists() else None
        )

        return ContextResponse(
            path=meta_dict["path"],
            name=meta_dict["name"],
            content_type=meta_dict.get("content_type"),
            glance=meta_dict.get("glance"),
            summary=meta_dict.get("summary"),
            content=content,
            size_bytes=meta_dict.get("size_bytes"),
            tags=meta_dict.get("tags", []),
            meta=meta_dict.get("meta", {}),
            created_at=datetime.fromisoformat(meta_dict["created_at"]),
            updated_at=datetime.fromisoformat(meta_dict["updated_at"]),
        )

    def delete_context(self, workspace_id: UUID, virtual_path: str) -> bool:
        """Delete a context entry (and its children)."""
        context_dir = self._get_context_path(workspace_id, virtual_path)
        if context_dir.exists():
            shutil.rmtree(context_dir)
            return True
        return False

    def list_contexts(
        self, workspace_id: UUID, prefix: str = "", recursive: bool = False
    ) -> list[ContextResponse]:
        """List context entries matching a prefix."""
        workspace_dir = self._get_workspace_dir(workspace_id)
        search_root = workspace_dir / prefix.strip("/") if prefix else workspace_dir

        if not search_root.exists():
            return []

        results = []
        pattern = "**/.metadata.json" if recursive else "*/.metadata.json"

        # If prefix is a direct context, add it too
        direct_meta = search_root / ".metadata.json"
        if direct_meta.exists():
            rel_path = search_root.relative_to(workspace_dir)
            ctx = self.get_context(workspace_id, str(rel_path))
            if ctx:
                results.append(ctx)

        for meta_file in search_root.glob(pattern):
            # Calculate relative path from workspace_dir
            context_dir = meta_file.parent
            rel_path = context_dir.relative_to(workspace_dir)
            ctx = self.get_context(workspace_id, str(rel_path))
            if ctx:
                results.append(ctx)

        return results
