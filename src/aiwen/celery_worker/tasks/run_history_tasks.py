"""Celery task: persist run the conversation history to WorkspaceContext after completion."""

import asyncio
import json
import logging
from typing import Any

from celery.signals import worker_process_init, worker_process_shutdown

from aiwen.celery_worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_worker_loop: asyncio.AbstractEventLoop | None = None
_db_initialized: bool = False


@worker_process_init.connect
def _init_worker_process(**kwargs):
    global _worker_loop, _db_initialized
    _worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_worker_loop)
    from aiwen.extensions.database import _ensure_registered
    _ensure_registered()
    _db_initialized = True


@worker_process_shutdown.connect
def _shutdown_worker_process(**kwargs):
    global _worker_loop, _db_initialized
    if _worker_loop is not None:
        async def _cleanup():
            from aiwen.extensions.database import dispose_all
            await dispose_all()
        try:
            _worker_loop.run_until_complete(_cleanup())
        except Exception as e:
            logger.warning(f"Shutdown cleanup error: {e}")
        _worker_loop.close()
        _worker_loop = None
    _db_initialized = False


def run_async(coro):
    global _worker_loop, _db_initialized
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    if not _db_initialized:
        from aiwen.extensions.database import _ensure_registered
        _ensure_registered()
        _db_initialized = True
    return _worker_loop.run_until_complete(coro)


@celery_app.task(
    bind=True,
    name="runs.save_run_history",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def save_run_history(self, run_id: str, workspace_id: str) -> dict[str, Any]:
    """After a run finishes, write its conversation to WorkspaceContext.

    Reads all user.message and agent.message events for the run and stores
    a single context entry at ``{workspace_id}/history/{run_id}``.
    """
    logger.info(f"save_run_history started: run_id={run_id}")

    async def _execute() -> dict[str, Any]:
        from sqlalchemy import select

        from aiwen.core.enums.events import EventType
        from aiwen.extensions.database import get_session
        from aiwen.models.events.event import Event
        from aiwen.models.runs.run import Run
        from aiwen.services.workspace_context.workspace_context_service import (
            WorkspaceContextService,
        )

        async with get_session("aiwen") as session:
            # 1. Load run metadata
            run_result = await session.execute(
                select(Run).where(Run.id == run_id)
            )
            run = run_result.scalar_one_or_none()
            if not run:
                logger.warning(f"save_run_history: run {run_id} not found, skipping")
                return {"status": "skipped", "reason": "run not found"}

            # 2. Load ordered events for this run
            events_result = await session.execute(
                select(Event)
                .where(Event.run_id == run_id)
                .order_by(Event.sequence)
            )
            events = events_result.scalars().all()

            # 3. Extract user / agent messages
            turns: list[dict] = []
            for event in events:
                if event.event_type == EventType.USER_MESSAGE.value:
                    payload = event.payload or {}
                    msg = payload.get("message", "")
                    if msg:
                        turns.append({"role": "user", "content": msg})
                elif event.event_type == EventType.AGENT_MESSAGE.value:
                    payload = event.payload or {}
                    msg = payload.get("message", "")
                    if msg:
                        turns.append({"role": "assistant", "content": msg})

            if not turns:
                logger.info(f"save_run_history: no messages in run {run_id}, skipping")
                return {"status": "skipped", "reason": "no messages"}

            # 4. Build progressive disclosure layers
            first_user = next((t["content"] for t in turns if t["role"] == "user"), "")
            first_assistant = next(
                (t["content"] for t in turns if t["role"] == "assistant"), ""
            )
            glance = (
                f"[{run.created_at.strftime('%m-%d %H:%M')}] "
                f"{first_user[:60]}{'…' if len(first_user) > 60 else ''}"
            )
            overview = {
                "run_id": run_id,
                "turn_count": len(turns),
                "user_message": first_user[:200],
                "agent_response": first_assistant[:200],
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            }
            detail = json.dumps(turns, ensure_ascii=False)

            # 5. Write to WorkspaceContext
            path = f"history/{run_id}"
            service = WorkspaceContextService(session, workspace_id)
            await service.set(
                path=path,
                glance=glance,
                overview=overview,
                detail=detail,
                tags=["history", "run"],
                meta={"run_id": run_id, "workspace_id": workspace_id},
                created_by=run.user_id,
                content_type="application/json",
            )

            logger.info(f"save_run_history: wrote {len(turns)} turns at {path}")
            return {"status": "success", "path": path, "turns": len(turns)}

    try:
        return run_async(_execute())
    except Exception as exc:
        logger.error(f"save_run_history failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)
