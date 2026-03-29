"""Detect and recover runs that have been silent for too long."""

from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from structure.core.enums.runs import RunStatus
from structure.models.runs.run import Run
from structure.services.runs.run_state_machine import RunStateMachine

logger = logging.getLogger(__name__)

STUCK_TIMEOUT_MINUTES = 5


class StuckRunDetector:
    """Detect runs stuck in running/waiting state and transition them to failed."""

    def __init__(self, state_machine: RunStateMachine):
        self.state_machine = state_machine

    async def detect_and_recover(self, db: AsyncSession) -> list[str]:
        """Find runs that haven't been updated in STUCK_TIMEOUT_MINUTES and fail them.

        Returns:
            List of recovered run IDs.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=STUCK_TIMEOUT_MINUTES)

        stmt = select(Run).where(
            Run.status.in_([RunStatus.RUNNING.value, RunStatus.WAITING.value]),
            Run.updated_at < cutoff,
        )
        result = await db.execute(stmt)
        stuck_runs = result.scalars().all()

        recovered_ids: list[str] = []
        for run in stuck_runs:
            run_id = run.id if isinstance(run.id, UUID) else UUID(str(run.id))
            try:
                await self.state_machine.fail(
                    run_id,
                    error="Run timed out after being silent for too long",
                    error_code="STUCK_RUN_TIMEOUT",
                    auto_commit=True,
                )
                recovered_ids.append(str(run_id))
                logger.warning(f"Recovered stuck run {run_id} (status: {run.status}, last updated: {run.updated_at})")
            except Exception as e:
                logger.error(f"Failed to recover stuck run {run_id}: {e}", exc_info=True)

        if recovered_ids:
            logger.info(f"Stuck run detector recovered {len(recovered_ids)} run(s)")

        return recovered_ids
