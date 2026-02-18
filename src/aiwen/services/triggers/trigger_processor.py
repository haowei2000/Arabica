# aiwen/services/triggers/trigger_processor.py
"""Trigger processor - evaluates workspace trigger conditions and executes actions.

Architecture:
    TriggerConditionEvaluator  - Pure condition matching (no I/O)
    TriggerProcessor           - Loads triggers from DB, evaluates, executes actions
"""

import logging
import re
from typing import Any

from sqlalchemy import select
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
    - always:   Always matches regardless of payload.
    - keyword:  Checks if condition_value substring exists in the target field.
    - regex:    Applies re.search on the target field string.
    - jsonpath: Evaluates a JSONPath expression; matches when result is non-empty.
    """

    def evaluate(self, trigger: WorkspaceTrigger, event_payload: dict[str, Any]) -> bool:
        """Evaluate whether the trigger condition matches the event payload.

        Args:
            trigger: The WorkspaceTrigger ORM instance.
            event_payload: The raw event payload dict.

        Returns:
            True if the trigger should fire, False otherwise.
        """
        condition_type = trigger.condition_type or "always"

        if condition_type == "always":
            return True

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
        # results: list of {"trigger_id", "trigger_name", "action_type", "result"}
    """

    def __init__(self, db: AsyncSession, workspace_id: str):
        self.db = db
        self.workspace_id = workspace_id
        self._evaluator = TriggerConditionEvaluator()

    async def process_event(self, event: Any) -> list[dict[str, Any]]:
        """Process an event through all matching workspace triggers.

        Args:
            event: The Event ORM instance from Redis stream (has .event_type, .payload).

        Returns:
            List of result dicts with keys: trigger_id, trigger_name, action_type, result.
        """
        event_type = getattr(event, "event_type", None) or getattr(event, "type", None)
        payload = getattr(event, "payload", {}) or {}

        if not event_type:
            return []

        triggers = await self._load_triggers(event_type)
        if not triggers:
            return []

        results = []
        for trigger in triggers:
            try:
                matched = self._evaluator.evaluate(trigger, payload)
                if not matched:
                    continue
                result = await self._execute_action(trigger)
                results.append(
                    {
                        "trigger_id": str(trigger.id),
                        "trigger_name": trigger.name,
                        "action_type": trigger.action_type,
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

    async def _execute_action(self, trigger: WorkspaceTrigger) -> Any:
        """Execute the trigger's action using WorkspaceContextService.

        Args:
            trigger: The matched WorkspaceTrigger.

        Returns:
            The action result (serializable).
        """
        from aiwen.services.workspace_context.workspace_context_service import (
            WorkspaceContextService,
        )

        service = WorkspaceContextService(self.db, self.workspace_id)
        params = trigger.action_params or {}
        action = trigger.action_type

        if action == "glance_context":
            prefix = params.get("prefix")
            result = await service.glance(prefix)
            return result

        if action == "list_context":
            prefix = params.get("prefix", "")
            level = params.get("level", "overview")
            query_result = await service.children(prefix)
            return self._disclose_query_result(query_result, level)

        if action == "read_context":
            path = params.get("path", "")
            level = params.get("level", "overview")
            result = await service.get(path, level)
            return result

        if action == "glob_context":
            pattern = params.get("pattern", "**")
            level = params.get("level", "overview")
            query_result = await service.glob(pattern)
            return self._disclose_query_result(query_result, level)

        if action == "search_context":
            query = params.get("query", "")
            level = params.get("level", "glance")
            # Search via glob("**") + keyword filter on glance text
            query_result = await service.glob("**")
            return self._search_query_result(query_result, query, level)

        logger.warning("Trigger %s has unknown action_type '%s'", trigger.id, action)
        return None

    def _disclose_query_result(self, query_result: Any, level: str) -> Any:
        """Convert a QueryResult to a serializable structure at the given level."""
        try:
            from aiwen.frameworks.context import DetailLevel

            level_enum = DetailLevel.from_str(level)
            if hasattr(query_result, "disclose_all"):
                return query_result.disclose_all(level_enum)
            if hasattr(query_result, "to_dict"):
                return query_result.to_dict()
        except Exception as exc:
            logger.warning("Failed to disclose query result: %s", exc)
        # Fallback: return as-is
        return query_result

    def _search_query_result(self, query_result: Any, keyword: str, level: str) -> Any:
        """Filter query result by keyword in glance text, then disclose."""
        try:
            from aiwen.frameworks.context import DetailLevel

            level_enum = DetailLevel.from_str(level)
            keyword_lower = keyword.lower()

            if hasattr(query_result, "entries"):
                filtered = [
                    e for e in query_result.entries
                    if keyword_lower in (getattr(e, "glance", "") or "").lower()
                ]
                if hasattr(query_result, "with_entries"):
                    filtered_result = query_result.with_entries(filtered)
                    if hasattr(filtered_result, "disclose_all"):
                        return filtered_result.disclose_all(level_enum)
                # Fallback: return glance strings for matching entries
                return [getattr(e, "glance", str(e)) for e in filtered]
        except Exception as exc:
            logger.warning("Failed to search query result: %s", exc)
        return self._disclose_query_result(query_result, level)


async def process_event_triggers(
    db: AsyncSession,
    workspace_id: str,
    event: Any,
) -> list[dict[str, Any]]:
    """Convenience function for use in EventWorker.handle_event.

    Args:
        db: Database session.
        workspace_id: Workspace UUID string.
        event: Event ORM instance.

    Returns:
        List of trigger result dicts (may be empty).
    """
    proc = TriggerProcessor(db, workspace_id)
    return await proc.process_event(event)
