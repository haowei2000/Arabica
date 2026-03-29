"""Simple executor — direct, concise responses with minimal tooling."""

from typing import Any, ClassVar

from structure.plugins.executors.default.concrete import DefaultExecutor
from structure.registries.core import register_executor
from structure.schemas.app import AppConfig
from structure.schemas.llm.chat_llm import ChatLLM

_SYSTEM_PROMPT = """\
You are a helpful assistant. Answer clearly and concisely.
workspace_id: {workspace_id}  run_id: {run_id}
Use tools only when necessary. Prefer direct answers.
"""


@register_executor
class SimpleExecutor(DefaultExecutor):
    """Lightweight agent: direct answers, minimal tool use."""

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "executor_code": "SimpleAgent",
        "executor_name": "Simple Agent",
        "enabled": True,
        "version": 1,
        "config": AppConfig(
            model=ChatLLM(provider="tongyi", name="qwen-plus"), context=None
        ),
    }

    def __init__(self, config: dict):
        super().__init__(config)
        self.system_prompt = _SYSTEM_PROMPT.format(
            workspace_id=self.workspace_id,
            run_id=self.run_id,
        )
