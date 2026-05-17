"""Executor command stream helpers.

The workspace and run Redis streams are broadcast channels for SSE clients.
Workers consume this separate command stream so UI replay/backpressure is not
coupled to executor scheduling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from structure.config.factory import get_settings

_redis_cfg = get_settings().redis
REDIS_CONSUMER_GROUP = _redis_cfg.consumer_group
REDIS_EXECUTOR_LABEL = _redis_cfg.executor_label


def executor_command_stream_name(workspace_id: str) -> str:
    """Return the worker command stream for a workspace."""
    return f"{REDIS_EXECUTOR_LABEL}:{workspace_id}:commands"


def command_created_at_ms() -> int:
    """Return current UTC epoch milliseconds for queue-lag metrics."""
    return int(datetime.now(UTC).timestamp() * 1000)


def _decode_fields(fields: dict[Any, Any]) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in fields.items():
        k = key.decode() if isinstance(key, bytes) else str(key)
        if isinstance(value, bytes):
            decoded[k] = value.decode()
        else:
            decoded[k] = str(value)
    return decoded


@dataclass(frozen=True, slots=True)
class EventCommand:
    """A durable event scheduled for worker-side handling."""

    event_id: str
    event_type: str
    workspace_id: str
    run_id: str | None
    executor_code: str | None
    created_at_ms: int | None

    @classmethod
    def from_redis_fields(cls, fields: dict[Any, Any]) -> EventCommand:
        decoded = _decode_fields(fields)
        created_at_raw = decoded.get("created_at_ms")
        return cls(
            event_id=decoded["event_id"],
            event_type=decoded.get("event_type", ""),
            workspace_id=decoded["workspace_id"],
            run_id=decoded.get("run_id") or None,
            executor_code=decoded.get("executor_code") or None,
            created_at_ms=int(created_at_raw) if created_at_raw else None,
        )
