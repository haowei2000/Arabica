"""File structurer — assembles the default pipeline and exposes FileStructurer.

This module is intentionally thin: data types live in ``file_context``,
stage implementations live in ``stages``.  Only the pipeline wiring and the
BaseStructurer wrapper belong here.

Default pipeline::

    S3TextStage  →  SplitStage  →  BuildChunksStage

Custom pipeline example::

    FileStructurer(pipeline=Pipeline(
        BytesStage(raw_bytes),          # bypass S3 (e.g. in tests)
        SplitStage(structurer=CodeStructurer()),
        BuildChunksStage(),
    ))
"""

from __future__ import annotations

from structure.core.interfaces.pipeline import Pipeline, Stage
from structure.core.interfaces.structurer import BaseStructurer
from structure.plugins.structurers import register_structurer
from structure.plugins.structurers.file_context import FileContext, FileInput
from structure.plugins.structurers.stages import (
    BuildChunksStage,
    BytesStage,
    S3TextStage,
    SplitStage,
    pick_text_structurer,
)
from structure.schemas.context.context_schema import ContextCore

# Re-export so callers only need one import
__all__ = [
    "BuildChunksStage",
    "BytesStage",
    "FileContext",
    "FileInput",
    "FileStructurer",
    "Pipeline",
    "S3TextStage",
    "SplitStage",
    "Stage",
    "build_file_pipeline",
    "pick_text_structurer",
]


def build_file_pipeline(*stages: Stage[FileContext]) -> Pipeline[FileContext]:
    """Construct a file pipeline from an explicit list of stages.

    Equivalent to ``Pipeline(*stages)`` but reads more clearly at call sites
    and keeps the return type explicit.
    """
    return Pipeline(*stages)


_DEFAULT_PIPELINE = build_file_pipeline(
    S3TextStage(),
    SplitStage(),
    BuildChunksStage(),
)


@register_structurer
class FileStructurer(BaseStructurer):
    """BaseStructurer wrapper that runs a file pipeline.

    Args:
        pipeline: Custom :class:`~structure.core.interfaces.pipeline.Pipeline`
                  to use instead of the default
                  S3Fetch → Decode → Split → Build chain.
    """

    name = "file"
    description = (
        "Downloads a single file from S3 and splits it into multi-level "
        "ContextCore chunks via a composable stage pipeline"
    )

    def __init__(self, pipeline: Pipeline[FileContext] | None = None) -> None:
        self.pipeline = pipeline or _DEFAULT_PIPELINE

    def structure(self, input: FileInput) -> list[ContextCore]:  # noqa: A002
        ctx = self.pipeline.run(FileContext(file_input=input))
        return ctx.chunks
