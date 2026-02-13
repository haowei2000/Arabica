# aiwen/services/agent/concrete.py
from collections.abc import AsyncGenerator
from typing import Any, ClassVar

from aiwen.interfaces.executor import AgentEvent, Executor
from aiwen.registries.core import register_executor
from aiwen.schemas.events.event_payloads import UserMessage


@register_executor
class ConflictExecutor(Executor):
    """Natural Language to SQL conversion agent."""

    TEMPLATE: ClassVar[dict[str, Any]] = {
        "executor_code": "ConflictExecutor",
        "executor_name": "Conflict Detection Executor",
        "enabled": True,
        "version": 1,
        "config": {},
    }

    async def setup(self) -> None:
        """Setup any resources needed by the executor."""
        pass

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the NL2SQL workflow."""
        query = input_data["query"]

        intent = await self.parse_intent(query)
        sql = await self.generate_sql(intent)
        data = await self.execute_sql(sql)

        return {"intent": intent, "sql": sql, "data": data}

    async def stream(
        self, user_message: UserMessage,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Stream NL2SQL progress as typed plan-step events.

        Each logical phase emits an ``AGENT_PLAN_STEP`` with status
        transitions ``pending → in_progress → completed``.  The final
        result is emitted as a single ``AGENT_MESSAGE``.
        """
        # ── step 1: parse intent ─────────────────────────────────
        yield self._emit_plan_step(1, "Parse intent", status="in_progress")
        intent = await self.parse_intent(user_message.message)
        yield self._emit_plan_step(
            1,
            "Parse intent",
            status="completed",
            output=str(intent),
        )

        # ── step 2: generate SQL ─────────────────────────────────
        yield self._emit_plan_step(2, "Generate SQL", status="in_progress")
        sql = await self.generate_sql(intent)
        yield self._emit_plan_step(2, "Generate SQL", status="completed", output=sql)

        # ── step 3: execute SQL ──────────────────────────────────
        yield self._emit_plan_step(3, "Execute SQL", status="in_progress")
        data = await self.execute_sql(sql)
        yield self._emit_plan_step(
            3,
            "Execute SQL",
            status="completed",
            output=f"{len(data)} rows",
        )

        # ── final result ─────────────────────────────────────────
        result = {"intent": intent, "sql": sql, "data": data}
        yield self._emit_message(str(result))

    # ── placeholder implementations ────────────────────────────

    async def parse_intent(self, query: str) -> dict[str, Any]:
        return {"original_query": query, "parsed_elements": {}}

    async def generate_sql(self, _intent: dict[str, Any]) -> str:
        return "SELECT * FROM table LIMIT 10;"

    async def execute_sql(self, _sql: str) -> list[dict[str, Any]]:
        return [{"placeholder": "result"}]
