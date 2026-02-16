"""Executor domain models."""

from aiwen.models.executor.executor import ExecutorTemplate

# Backward compatibility alias (deprecated)
Executor = ExecutorTemplate

__all__ = [
    "Executor",  # Deprecated: use ExecutorTemplate
    "ExecutorTemplate",
]
