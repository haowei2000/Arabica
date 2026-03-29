from structure.core.interfaces.structurer import BaseStructurer
from structure.models.context import Skill
from structure.plugins.structurers import register_structurer
from structure.plugins.structurers.file_structure import FileInput, FileStructurer
from structure.schemas.context.context_schema import ContextCore
from structure.utils.context import slugify

_file_structurer = FileStructurer()


@register_structurer
class SkillStructurer(BaseStructurer):
    name = "Skill"
    description = "Multi-level skill structurer: overview → per-file outline → per-section chunks"

    def structure(self, input: Skill) -> list[ContextCore]:  # noqa: A002
        skill = input
        results: list[ContextCore] = []

        glance = skill.name
        if skill.description:
            glance = f"{skill.name} — {skill.description}"

        files_meta: dict = skill.files or {}
        skill_path = slugify(f"/skill/{skill.name}")

        # Level 1: skill overview with file listing tree
        lines: list[str] = [f"{skill.name}/"]
        if not files_meta:
            lines.append("    (no files)")
        else:
            items = list(files_meta.items())
            for i, (file_path, file_info) in enumerate(items):
                connector = "└── " if i == len(items) - 1 else "├── "
                size_kb = (file_info.get("size") or 0) / 1024
                content_type = file_info.get("content_type", "")
                lines.append(
                    f"    {connector}{file_path}"
                    f"  [{content_type}]"
                    f"  ({size_kb:.1f} KB)"
                )

        results.append(ContextCore(
            glance=glance,
            content="\n".join(lines),
            path=skill_path,
        ))

        # Level 2 + 3: delegate each file to FileStructurer
        for file_path, file_info in files_meta.items():
            s3_key = file_info.get("s3_key") or file_info.get("key")
            if not s3_key:
                continue

            fi = FileInput(
                s3_key=s3_key,
                file_name=file_path.split("/")[-1],
                base_path=skill_path,
                content_type=file_info.get("content_type", ""),
                size=file_info.get("size") or 0,
            )
            for chunk in _file_structurer.structure(fi):
                chunk.glance = f"{skill.name} / {chunk.glance}"
                results.append(chunk)

        return results
