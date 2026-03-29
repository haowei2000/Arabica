"""Executor domain models."""

from structure.models.executor.executor import ExecutorTemplate

# Backward compatibility alias (deprecated)
Executor = ExecutorTemplate

__all__ = [
    "Executor",  # Deprecated: use ExecutorTemplate
    "ExecutorTemplate",
]
