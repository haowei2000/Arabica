# aiwen/services/agent/concrete.py
from typing import Any, ClassVar

from aiwen.interfaces.executor import Executor
from aiwen.plugins.executors.conflict import prompts
from aiwen.registries.core import register_executor


@register_executor
class ConflictExecutor(Executor):
    """Agent to process a conflict task."""

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "executor_code": "ConflictExecutor",
        "executor_name": "Conflict Detection Executor",
        "enabled": True,
        "version": 1,
        "config": {},
    }

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        # prompts module contains all Jinja2 templates
        self.prompts = prompts