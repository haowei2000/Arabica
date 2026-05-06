"""
A module that provides a set of utility functions for common tasks, such as data manipulation and validation.

This module includes several functions designed to simplify the process of handling and validating data. These
functions are intended to be used in various parts of an application where there is a need for consistent
data processing logic. The utilities provided here focus on ensuring data integrity and ease of use.
"""

from structure.core.interfaces.structurer import BaseStructurer
from structure.models.context import Knowledge
from structure.plugins.structurers import register_structurer
from structure.schemas.context.context_schema import ContextCore
from structure.utils.context import build_context_path


@register_structurer
class KnowledgeStructurer(BaseStructurer):
    name = "Knowledge"
    description = "Table-row extraction structuring (one section per header+row pair)"

    def structure(self, input: Knowledge) -> list[ContextCore]:
        kb = input

        # glance: name + optional description
        glance = kb.name
        if kb.description:
            glance = f"{kb.name} — {kb.description}"

        # content: tree listing of documents
        docs = list(kb.documents) if kb.documents else []
        lines: list[str] = [f"{kb.name}/"]
        if not docs:
            lines.append("    (no documents)")
        else:
            for i, doc in enumerate(docs):
                connector = "└── " if i == len(docs) - 1 else "├── "
                size_kb = doc.file_size / 1024 if doc.file_size else 0
                lines.append(
                    f"    {connector}{doc.original_name}"
                    f"  [{doc.status}]"
                    f"  ({doc.chunk_count} chunks, {size_kb:.1f} KB)"
                )

        return [
            ContextCore(
                glance=glance,
                content="\n".join(lines),
                path=build_context_path("knowledge", kb.name),
            ),
            ContextCore(
                glance=glance,
                content="\n".join(lines),
                path=build_context_path("knowledge", kb.name, "overview.ctx"),
            ),
        ]
