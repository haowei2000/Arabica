# aiwen/services/triggers/trigger_processor.py
"""Trigger processor - evaluates workspace trigger conditions and executes actions.

Architecture:
    TriggerConditionEvaluator  - Pure condition matching (no I/O)
    TriggerProcessor - Loads triggers from DB, evaluates, executes actions

Action execution:
    ``tool_name`` is any registered tool name (e.g. ``glance_context``,
    ``read_context``, ``http_request``, …).  ``action_params`` are passed
    directly as keyword arguments to the tool.  If the tool's InputSchema
    declares a ``workspace_id`` field and it is not already in
    ``action_params``, the current workspace ID is injected automatically.
"""

import logging
import re
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.workspaces.workspace_trigger import WorkspaceTrigger

logger = logging.getLogger(__name__)


def _get_nested_value(obj: Any, field_path: str) -> Any:
    """Get a value from a nested dict using dot-notation.

    Args:
        obj: The dict to traverse.
        field_path: Dot-separated path, e.g. "data.text".

    Returns:
        The value at the path, or None if not found.
    """
    parts = field_path.split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


class TriggerConditionEvaluator:
    """Pure condition evaluator - no I/O, no side effects.

    Supported condition types:
    - always:     Always matches regardless of payload.
    - keyword:    Checks if condition_value substring exists in the target field.
    - regex:      Applies re.search on the target field string.
    - jsonpath:   Evaluates a JSONPath expression; matches when result is non-empty.
    - first_run:  Matches only when the workspace is on its first run ever.
                  Requires ``context["is_first_run"]`` to be pre-computed by the
                  caller (TriggerProcessor) and passed in via the context arg.
    """

    def evaluate(
        self,
        trigger: WorkspaceTrigger,
        event_payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Evaluate whether the trigger condition matches the event payload.

        Args:
            trigger: The WorkspaceTrigger ORM instance.
            event_payload: The raw event payload dict.
            context: Optional pre-computed workspace context (e.g. ``is_first_run``).

        Returns:
            True if the trigger should fire, False otherwise.
        """
        condition_type = trigger.condition_type or "always"

        if condition_type == "always":
            return True

        if condition_type == "first_run":
            return bool((context or {}).get("is_first_run", False))

        # Extract the target field value
        field = trigger.condition_field or "message"
        field_value = _get_nested_value(event_payload, field)
        field_str = str(field_value) if field_value is not None else ""

        if condition_type == "keyword":
            keyword = trigger.condition_value or ""
            return keyword in field_str

        if condition_type == "regex":
            pattern = trigger.condition_value or ""
            try:
                return bool(re.search(pattern, field_str))
            except re.error as exc:
                logger.warning(
                    "Trigger %s has invalid regex pattern '%s': %s",
                    trigger.id,
                    pattern,
                    exc,
                )
                return False

        if condition_type == "jsonpath":
            return self._evaluate_jsonpath(trigger, event_payload)

        logger.warning(
            "Trigger %s has unknown condition_type '%s', skipping",
            trigger.id,
            condition_type,
        )
        return False

    def _evaluate_jsonpath(
        self, trigger: WorkspaceTrigger, event_payload: dict[str, Any]
    ) -> bool:
        """Evaluate a JSONPath expression against the event payload."""
        try:
            from jsonpath_ng import parse as jsonpath_parse  # type: ignore[import]
        except ImportError:
            logger.error(
                "jsonpath-ng is not installed; cannot evaluate jsonpath trigger %s",
                trigger.id,
            )
            return False

        expression = trigger.condition_value or ""
        try:
            matches = jsonpath_parse(expression).find(event_payload)
            return bool(matches)
        except Exception as exc:
            logger.warning(
                "Trigger %s jsonpath evaluation error for '%s': %s",
                trigger.id,
                expression,
                exc,
            )
            return False


class TriggerProcessor:
    """Loads enabled triggers from DB, evaluates conditions, and executes actions.

    Usage (in EventWorker):
        proc = TriggerProcessor(db, workspace_id)
        results = await proc.process_event(event)
        # results: list of {"trigger_id", "trigger_name", "tool_name", "result"}
    """

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: str,
        publisher: Any | None = None,
        run_id: str | None = None,
        publish_workspace_id: str | None = None,
    ):
        self.db = db
        self.workspace_id = workspace_id
        self._publisher = publisher
        self._run_id = run_id
        self._publish_workspace_id = publish_workspace_id or workspace_id
        self._evaluator = TriggerConditionEvaluator()

    async def process_event(self, event: Any) -> list[dict[str, Any]]:
        """Process an event through all matching workspace triggers.

        Args:
            event: The Event ORM instance from Redis stream (has .event_type, .payload).

        Returns:
            List of result dicts with keys: trigger_id, trigger_name, tool_name, result.
        """
        event_type = getattr(event, "event_type", None) or getattr(event, "type", None)
        payload = getattr(event, "payload", {}) or {}

        if not event_type:
            return []

        triggers = await self._load_triggers(event_type)
        logger.debug(
            "Trigger lookup: workspace=%s event_type=%s found=%d",
            self.workspace_id,
            event_type,
            len(triggers),
        )
        if not triggers:
            return []

        # Lazily compute workspace context required by stateful condition types.
        # Currently only "first_run" needs DB access; other types are pure.
        context: dict[str, Any] = {}
        if any(t.condition_type == "first_run" for t in triggers):
            context["is_first_run"] = await self._is_first_run()

        results = []
        for trigger in triggers:
            try:
                matched = self._evaluator.evaluate(trigger, payload, context)
                logger.debug(
                    "Trigger '%s' (id=%s) condition=%s matched=%s",
                    trigger.name,
                    trigger.id,
                    trigger.condition_type,
                    matched,
                )
                if not matched:
                    continue
                result = await self._execute_action(trigger)
                logger.info(
                    "Trigger '%s' fired: tool=%s result_type=%s",
                    trigger.name,
                    trigger.tool_name,
                    type(result).__name__,
                )
                results.append(
                    {
                        "trigger_id": str(trigger.id),
                        "trigger_name": trigger.name,
                        "tool_name": trigger.tool_name,
                        "result": result,
                    }
                )
            except Exception as exc:
                logger.error(
                    "Error processing trigger %s (%s): %s",
                    trigger.id,
                    trigger.name,
                    exc,
                    exc_info=True,
                )

        return results

    async def _load_triggers(self, event_type: str) -> list[WorkspaceTrigger]:
        """Load enabled triggers matching workspace_id + event_type, sorted by priority."""
        stmt = (
            select(WorkspaceTrigger)
            .where(
                WorkspaceTrigger.workspace_id == self.workspace_id,
                WorkspaceTrigger.event_type == event_type,
                WorkspaceTrigger.enabled == True,  # noqa: E712
            )
            .order_by(WorkspaceTrigger.priority)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _is_first_run(self) -> bool:
        """Return True if this workspace has exactly one run (the current one).

        Called lazily only when at least one ``first_run`` trigger exists.
        The run is already persisted when ``user.message`` is processed, so a
        count of 1 means no previous runs have ever been created.
        """
        from aiwen.models.runs.run import Run  # local import to avoid circular deps

        stmt = select(func.count()).select_from(Run).where(
            Run.workspace_id == self.workspace_id
        )
        result = await self.db.execute(stmt)
        return result.scalar_one() == 1

    async def _execute_action(self, trigger: WorkspaceTrigger) -> Any:
        """Execute the trigger action by invoking a registered tool by name.

        ``trigger.tool_name`` is the tool name.  ``trigger.action_params``
        are forwarded as keyword arguments.  If the tool's InputSchema has a
        ``workspace_id`` field and none was provided in action_params, the
        current workspace ID is injected automatically.

        When a publisher is available, emits TOOL_CALL before execution
        and TOOL_RESULT / TOOL_ERROR after.  All three carry
        ``"_source": "trigger"`` in the payload so the worker skips
        re-execution when it consumes them from the executor stream.

        Args:
            trigger: The matched WorkspaceTrigger.

        Returns:
            The tool's output dict, or None if the tool was not found.
        """
        from aiwen.core.enums.events import EventType
        from aiwen.registries.core import ToolRegistry

        tool_name = trigger.tool_name
        tool_instance = ToolRegistry.get_tool_instance(tool_name)

        if tool_instance is None:
            logger.warning(
                "Trigger '%s' references unknown tool '%s'; skipping",
                trigger.name,
                tool_name,
            )
            return None

        # Build call kwargs from action_params
        kwargs: dict[str, Any] = dict(trigger.action_params or {})

        # Auto-inject workspace_id if the tool's schema declares it
        if "workspace_id" not in kwargs:
            input_fields = tool_instance.InputSchema.model_fields
            if "workspace_id" in input_fields:
                kwargs["workspace_id"] = self.workspace_id

        tool_id = str(uuid4())

        # Publish TOOL_CALL for real-time observability.
        if self._publisher and self._run_id:
            try:
                await self._publisher.publish(
                    event_type=EventType.TOOL_CALL,
                    workspace_id=self._publish_workspace_id,
                    run_id=self._run_id,
                    payload={
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "arguments": kwargs,
                        "trigger_name": trigger.name,
                        "trigger_id": str(trigger.id),
                        "_source": "trigger",
                    },
                    auto_commit=True,
                )
            except Exception as pub_err:
                logger.warning("Failed to publish trigger tool call event: %s", pub_err)

        start_time = time.time()
        try:
            result = await tool_instance(**kwargs)
            elapsed_ms = int((time.time() - start_time) * 1000)

            if self._publisher and self._run_id:
                try:
                    await self._publisher.publish(
                        event_type=EventType.TOOL_RESULT,
                        workspace_id=self._publish_workspace_id,
                        run_id=self._run_id,
                        payload={
                            "tool_name": tool_name,
                            "tool_id": tool_id,
                            "result": result,
                            "execution_time_ms": elapsed_ms,
                            "trigger_name": trigger.name,
                            "trigger_id": str(trigger.id),
                            "_source": "trigger",
                        },
                        auto_commit=True,
                    )
                except Exception as pub_err:
                    logger.warning("Failed to publish trigger tool result event: %s", pub_err)

            return result

        except Exception as exc:
            elapsed_ms = int((time.time() - start_time) * 1000)

            if self._publisher and self._run_id:
                try:
                    await self._publisher.publish(
                        event_type=EventType.TOOL_ERROR,
                        workspace_id=self._publish_workspace_id,
                        run_id=self._run_id,
                        payload={
                            "tool_name": tool_name,
                            "tool_id": tool_id,
                            "error_message": str(exc),
                            "execution_time_ms": elapsed_ms,
                            "trigger_name": trigger.name,
                            "trigger_id": str(trigger.id),
                            "_source": "trigger",
                        },
                        auto_commit=True,
                    )
                except Exception as pub_err:
                    logger.warning("Failed to publish trigger tool error event: %s", pub_err)

            raise


async def process_event_triggers(
    db: AsyncSession,
    workspace_id: str,
    event: Any,
    publisher: Any | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Convenience function for use in EventWorker.handle_event.

    Args:
        db: Database session.
        workspace_id: Workspace UUID string.
        event: Event ORM instance.
        publisher: Optional EventPublisher for emitting trigger tool events.
        run_id: Run ID string to associate trigger tool events with.

    Returns:
        List of trigger result dicts (may be empty).
    """
    proc = TriggerProcessor(
        db,
        workspace_id,
        publisher=publisher,
        run_id=run_id,
        publish_workspace_id=workspace_id,
    )
    return await proc.process_event(event)
