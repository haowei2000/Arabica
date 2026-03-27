from aiwen.core.interfaces.structurer import BaseStructurer
from aiwen.models.context.context import Context
from aiwen.plugins.structurers import register_structurer
from aiwen.schemas.context.context_schema import ContextCore
from aiwen.utils.context import slugify


@register_structurer
class MemoryStructurer(BaseStructurer):
    name = "Memory"
    description = "Single-level memory structurer: renders a Context entry with metadata"

    def structure(self, input: Context) -> list[ContextCore]:  # noqa: A002
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
            mem_path = slugify(f"/memory/{ctx.path.strip('/')}")
        else:
            mem_path = f"/memory/{ctx.id}"

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
