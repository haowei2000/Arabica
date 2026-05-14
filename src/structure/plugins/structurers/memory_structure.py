from structure.core.interfaces.structurer import BaseStructurer
from structure.models.context.context import Context
from structure.plugins.structurers import register_structurer
from structure.schemas.context.context_schema import ContextCore
from structure.utils.context import build_path


@register_structurer
class MemoryStructurer(BaseStructurer):
    name = "Memory"
    description = (
        "Single-level memory structurer: renders a Context entry with metadata"
    )

    def structure(self, input: Context) -> list[ContextCore]:
        ctx = input

        # Derive glance: use stored glance, or truncate content
        if ctx.glance:
            glance = ctx.glance
        elif ctx.content:
            glance = ctx.content[:80] + "..." if len(ctx.content) > 80 else ctx.content
        else:
            glance = "(empty)"

        # Derive path: use stored path or fall back to id
        if ctx.path:
            raw_path = ctx.path.strip("/")
            mem_path = (
                build_path(raw_path)
                if raw_path.startswith("memory/")
                else build_path("memory", raw_path)
            )
        else:
            mem_path = build_path("memory", str(ctx.id))

        # Build content block: metadata header + full content
        lines: list[str] = []
        lines.append(f"scope: {ctx.scope}")
        lines.append(f"type: {ctx.context_type}")
        if ctx.importance:
            lines.append(f"importance: {ctx.importance}")
        if ctx.tags:
            lines.append(f"tags: {', '.join(ctx.tags)}")
        lines.append("---")
        lines.append(ctx.content or "")

        return [
            ContextCore(
                glance=glance,
                content="\n".join(lines),
                path=mem_path,
            )
        ]
