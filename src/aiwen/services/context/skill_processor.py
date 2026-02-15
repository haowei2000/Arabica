"""Skill processor for parsing Markdown and generating embeddings."""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.context.context import Context
from aiwen.services.context.skill_crud import SkillCRUD

logger = logging.getLogger(__name__)


class SkillProcessor:
    """Process skills: parse Markdown, extract metadata, generate embeddings."""

    def __init__(self, db_session: AsyncSession):
        """Initialize processor with database session."""
        self.db = db_session
        self.crud = SkillCRUD(db_session)

    def parse_markdown(self, markdown_content: str) -> dict[str, Any]:
        """
        Parse Markdown content to extract structured information.

        Extracts:
        - Sections (headers and content)
        - Code blocks with language tags
        - Lists (ordered and unordered)
        - Metadata from YAML frontmatter if present

        Args:
            markdown_content: Raw Markdown text

        Returns:
            Structured data dictionary
        """
        result: dict[str, Any] = {
            "sections": [],
            "code_blocks": [],
            "metadata": {},
            "raw_text": markdown_content,
        }

        # Extract YAML frontmatter if present
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", markdown_content, re.DOTALL)
        if frontmatter_match:
            try:
                import yaml
                result["metadata"] = yaml.safe_load(frontmatter_match.group(1)) or {}
                markdown_content = markdown_content[frontmatter_match.end():]
            except Exception as e:
                logger.warning(f"Failed to parse YAML frontmatter: {e}")

        # Extract sections (headers and their content)
        lines = markdown_content.split("\n")
        current_section = {"level": 0, "title": "", "content": ""}

        for line in lines:
            header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if header_match:
                # Save previous section
                if current_section["title"] or current_section["content"]:
                    result["sections"].append(current_section.copy())

                # Start new section
                level = len(header_match.group(1))
                title = header_match.group(2).strip()
                current_section = {"level": level, "title": title, "content": ""}
            else:
                current_section["content"] += line + "\n"

        # Save last section
        if current_section["title"] or current_section["content"]:
            result["sections"].append(current_section)

        # Extract code blocks
        code_pattern = r"```(\w+)?\n(.*?)```"
        for match in re.finditer(code_pattern, markdown_content, re.DOTALL):
            language = match.group(1) or "text"
            code = match.group(2).strip()
            result["code_blocks"].append({"language": language, "code": code})

        return result

    def generate_summary(self, parsed_data: dict[str, Any]) -> str:
        """
        Generate a structured summary from parsed Markdown.

        Args:
            parsed_data: Output from parse_markdown()

        Returns:
            Summary text
        """
        summary_parts = []

        # Add metadata if present
        if parsed_data["metadata"]:
            summary_parts.append("Metadata:")
            for key, value in parsed_data["metadata"].items():
                summary_parts.append(f"  - {key}: {value}")

        # Add section outline
        sections = parsed_data["sections"]
        if sections:
            summary_parts.append("\nSections:")
            for section in sections:
                indent = "  " * (section["level"] - 1)
                summary_parts.append(f"{indent}- {section['title']}")

        # Add code block count
        code_count = len(parsed_data["code_blocks"])
        if code_count > 0:
            languages = set(block["language"] for block in parsed_data["code_blocks"])
            summary_parts.append(f"\nCode blocks: {code_count} ({', '.join(languages)})")

        return "\n".join(summary_parts)

    async def process_skill(
        self,
        skill_id: str | UUID,
        embedding_model: str | None = None,
        auto_commit: bool = True,
    ) -> Context | None:
        """
        Process a skill: parse Markdown, generate summary, and create embeddings.

        Args:
            skill_id: Skill ID
            embedding_model: Optional embedding model to use
            auto_commit: If True, immediately commit the transaction

        Returns:
            Updated skill or None if not found
        """
        # Get the skill
        skill = await self.crud.get_by_id(skill_id)
        if not skill:
            logger.error(f"Skill {skill_id} not found")
            return None

        try:
            # Parse Markdown content
            parsed_data = self.parse_markdown(skill.content)

            # Generate summary
            summary = self.generate_summary(parsed_data)

            # Update skill with parsed data
            skill.summary = summary

            # Store parsed structure in meta
            meta = skill.meta or {}
            meta["parsed_structure"] = {
                "section_count": len(parsed_data["sections"]),
                "code_block_count": len(parsed_data["code_blocks"]),
                "has_frontmatter": bool(parsed_data["metadata"]),
            }
            if parsed_data["metadata"]:
                meta["frontmatter"] = parsed_data["metadata"]
            skill.meta = meta

            # TODO: Generate embeddings using embedding service
            # For now, we'll leave embeddings as None
            # This should be implemented when the embedding service is available
            #
            # if embedding_model:
            #     from aiwen.services.embedding import EmbeddingService
            #     embedding_service = EmbeddingService()
            #     embeddings = await embedding_service.generate(skill.content, model=embedding_model)
            #     skill.embedding_768 = embeddings.get('768')
            #     # etc...

            if auto_commit:
                await self.db.commit()
                await self.db.refresh(skill)
            else:
                await self.db.flush()
                await self.db.refresh(skill)

            logger.info(f"Processed skill {skill_id}")
            return skill

        except Exception as e:
            logger.error(f"Failed to process skill {skill_id}: {e}")
            if auto_commit:
                await self.db.rollback()
            raise

    async def batch_process(
        self,
        user_id: str | UUID,
        embedding_model: str | None = None,
    ) -> dict[str, Any]:
        """
        Batch process all skills for a user.

        Args:
            user_id: User ID
            embedding_model: Optional embedding model to use

        Returns:
            Processing statistics
        """
        skills, total = await self.crud.list(user_id, skip=0, limit=1000)

        stats = {
            "total": total,
            "processed": 0,
            "failed": 0,
            "errors": [],
        }

        for skill in skills:
            try:
                await self.process_skill(skill.id, embedding_model, auto_commit=False)
                stats["processed"] += 1
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append({"skill_id": str(skill.id), "error": str(e)})
                logger.error(f"Failed to process skill {skill.id}: {e}")

        await self.db.commit()
        logger.info(f"Batch processed {stats['processed']}/{total} skills for user {user_id}")
        return stats
