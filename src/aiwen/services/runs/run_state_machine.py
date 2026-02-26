# aiwen/services/runs/run_state_machine.py
"""Run State Machine for managing run lifecycle transitions."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any
from uuid import UUID

import redis.asyncio as redis_async
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aiwen.models.runs.run import Run
from aiwen.schemas.events.event_payloads import EventType
from aiwen.schemas.runs.run import RunStatus
from aiwen.services.events.event_publisher import EventPublisher

logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current_state: str, target_state: str, reason: str = ""):
        self.current_state = current_state
        self.target_state = target_state
        self.reason = reason
        message = f"Invalid transition from '{current_state}' to '{target_state}'"
        if reason:
            message += f": {reason}"
        super().__init__(message)


class RunStateMachine:
    """Run State Machine.

    Manages the lifecycle of runs through valid state transitions.

    State Diagram:
        pending -> running | cancelled
        running -> waiting | finished | failed | cancelled
        waiting -> running | failed | cancelled
        finished, cancelled, failed -> (terminal states)

    Each transition emits a run.state.change event.
    """

    # Valid transitions: {current_state: [allowed_target_states]}
    VALID_TRANSITIONS = {
        RunStatus.PENDING.value: [RunStatus.RUNNING.value, RunStatus.CANCELLED.value],
        RunStatus.RUNNING.value: [
            RunStatus.WAITING.value,
            RunStatus.FINISHED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        ],
        RunStatus.WAITING.value: [
            RunStatus.RUNNING.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        ],
        # Terminal states - no valid transitions
        RunStatus.FINISHED.value: [],
        RunStatus.CANCELLED.value: [],
        RunStatus.FAILED.value: [],
    }

    def __init__(
        self,
        db: AsyncSession,
        redis_client: redis_async.Redis | None = None,
        event_publisher: EventPublisher | None = None,
    ):
        """Initialize RunStateMachine.

        Args:
            db: SQLAlchemy async session
            redis_client: Redis client for event publishing (optional)
            event_publisher: Shared EventPublisher instance. If ``None``,
                a new one is created (backward-compatible).
        """
        self.db = db
        self.redis = redis_client
        self.event_publisher = event_publisher or EventPublisher(db, redis_client)

    async def transition(
        self,
        run_id: UUID | str,
        target_state: RunStatus | str,
        reason: str | None = None,
        triggered_by: str | None = None,
        auto_commit: bool = False,
    ) -> Run:
        """Transition a run to a new state.

        Args:
            run_id: Run ID to transition
            target_state: Target state
            reason: Reason for the transition
            triggered_by: Who/what triggered this transition
            auto_commit: Whether to commit the transaction

        Returns:
            Updated Run instance

        Raises:
            ValueError: If run not found
            InvalidTransitionError: If transition is not valid
        """
        run_id_str = str(run_id) if isinstance(run_id, UUID) else run_id
        target_state_str = (
            target_state.value if isinstance(target_state, RunStatus) else target_state
        )

        # Get the run
        stmt = select(Run).where(Run.id == run_id_str)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()

        if not run:
            raise ValueError(f"Run not found: {run_id_str}")

        # Validate transition
        current_state = run.status
        if not self._is_valid_transition(current_state, target_state_str):
            raise InvalidTransitionError(
                current_state,
                target_state_str,
                f"Allowed transitions from '{current_state}': {self.VALID_TRANSITIONS.get(current_state, [])}",
            )

        # Update run state
        previous_state = run.status
        run.status = target_state_str
        run.updated_at = datetime.now(UTC)

        # Update timestamps based on transition
        if (
            target_state_str == RunStatus.RUNNING.value
            and previous_state == RunStatus.PENDING.value
        ):
            run.started_at = datetime.now(UTC)
        elif target_state_str in (
            RunStatus.FINISHED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        ):
            run.completed_at = datetime.now(UTC)

        # Emit state change event
        await self.event_publisher.publish(
            event_type=EventType.RUN_STATE_CHANGE,
            workspace_id=run.workspace_id,
            run_id=run.id,
            payload={
                "previous_state": previous_state,
                "new_state": target_state_str,
                "reason": reason,
                "triggered_by": triggered_by,
            },
            auto_commit=False,
        )

        if auto_commit:
            await self.db.commit()
        else:
            await self.db.flush()

        await self.db.refresh(run)

        logger.info(
            f"Run {run_id_str} transitioned: {previous_state} -> {target_state_str}"
            + (f" (reason: {reason})" if reason else "")
        )

        return run

    def _is_valid_transition(self, current_state: str, target_state: str) -> bool:
        """Check if a state transition is valid.

        Args:
            current_state: Current state
            target_state: Target state

        Returns:
            True if transition is valid, False otherwise
        """
        allowed = self.VALID_TRANSITIONS.get(current_state, [])
        return target_state in allowed

    async def start(
        self,
        run_id: UUID | str,
        triggered_by: str | None = None,
        auto_commit: bool = False,
    ) -> Run:
        """Start a pending run.

        Transition: pending -> running

        Args:
            run_id: Run ID
            triggered_by: Who started the run
            auto_commit: Whether to commit

        Returns:
            Updated Run instance
        """
        return await self.transition(
            run_id=run_id,
            target_state=RunStatus.RUNNING,
            reason="Run started",
            triggered_by=triggered_by,
            auto_commit=auto_commit,
        )

    async def pause_for_tool(
        self,
        run_id: UUID | str,
        waiting_for: dict[str, Any],
        auto_commit: bool = False,
    ) -> Run:
        """Pause a running run while waiting for tool approval or result.

        Transition: running -> waiting

        Args:
            run_id: Run ID
            waiting_for: Information about what the run is waiting for
            auto_commit: Whether to commit

        Returns:
            Updated Run instance
        """
        run_id_str = str(run_id) if isinstance(run_id, UUID) else run_id

        # Update waiting_for field before transition
        stmt = select(Run).where(Run.id == run_id_str)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()

        if run:
            run.waiting_for = waiting_for

        return await self.transition(
            run_id=run_id,
            target_state=RunStatus.WAITING,
            reason=f"Waiting for: {waiting_for.get('type', 'unknown')}",
            triggered_by="agent",
            auto_commit=auto_commit,
        )

    async def resume_from_tool(
        self,
        run_id: UUID | str,
        auto_commit: bool = False,
    ) -> Run:
        """Resume a waiting run after tool completion.

        Transition: waiting -> running

        Args:
            run_id: Run ID
            auto_commit: Whether to commit

        Returns:
            Updated Run instance
        """
        run_id_str = str(run_id) if isinstance(run_id, UUID) else run_id

        # Clear waiting_for field before transition
        stmt = select(Run).where(Run.id == run_id_str)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()

        if run:
            run.waiting_for = None

        return await self.transition(
            run_id=run_id,
            target_state=RunStatus.RUNNING,
            reason="Resumed from waiting",
            triggered_by="tool_callback",
            auto_commit=auto_commit,
        )

    async def complete(
        self,
        run_id: UUID | str,
        output_data: dict[str, Any] | None = None,
        auto_commit: bool = False,
    ) -> Run:
        """Complete a running run successfully.

        Transition: running -> finished

        Args:
            run_id: Run ID
            output_data: Final output data
            auto_commit: Whether to commit

        Returns:
            Updated Run instance
        """
        run_id_str = str(run_id) if isinstance(run_id, UUID) else run_id

        # Update output_data before transition
        stmt = select(Run).where(Run.id == run_id_str)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()

        if run and output_data:
            run.output_data = output_data

        return await self.transition(
            run_id=run_id,
            target_state=RunStatus.FINISHED,
            reason="Run completed successfully",
            triggered_by="agent",
            auto_commit=auto_commit,
        )

    async def fail(
        self,
        run_id: UUID | str,
        error: str,
        error_code: str | None = None,
        auto_commit: bool = False,
    ) -> Run:
        """Mark a running run as failed.

        Transition: running -> failed

        Args:
            run_id: Run ID
            error: Error message
            error_code: Error code
            auto_commit: Whether to commit

        Returns:
            Updated Run instance
        """
        run_id_str = str(run_id) if isinstance(run_id, UUID) else run_id

        # Update error fields before transition
        stmt = select(Run).where(Run.id == run_id_str)
        result = await self.db.execute(stmt)
        run = result.scalar_one_or_none()

        if run:
            run.error = error
            run.error_code = error_code

        return await self.transition(
            run_id=run_id,
            target_state=RunStatus.FAILED,
            reason=error,
            triggered_by="system",
            auto_commit=auto_commit,
        )

    async def cancel(
        self,
        run_id: UUID | str,
        reason: str = "Cancelled by user",
        triggered_by: str = "user",
        auto_commit: bool = False,
    ) -> Run:
        """Cancel a running or waiting run.

        Transition: running/waiting -> cancelled

        Args:
            run_id: Run ID
            reason: Cancellation reason
            triggered_by: Who cancelled the run
            auto_commit: Whether to commit

        Returns:
            Updated Run instance
        """
        return await self.transition(
            run_id=run_id,
            target_state=RunStatus.CANCELLED,
            reason=reason,
            triggered_by=triggered_by,
            auto_commit=auto_commit,
        )

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        """Check if a status is terminal (no further transitions allowed).

        Args:
            status: Status to check

        Returns:
            True if terminal, False otherwise
        """
        return status in (
            RunStatus.FINISHED.value,
            RunStatus.CANCELLED.value,
            RunStatus.FAILED.value,
        )

    @classmethod
    def is_active(cls, status: str) -> bool:
        """Check if a status is active (run is still processing).

        Args:
            status: Status to check

        Returns:
            True if active, False otherwise
        """
        return status in (
            RunStatus.PENDING.value,
            RunStatus.RUNNING.value,
            RunStatus.WAITING.value,
        )
