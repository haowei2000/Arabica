"""Celery tasks: sync Workspace, Run, and RunEvents to the Context table."""

import logging
from uuid import UUID

from structure.celery_worker.celery_app import celery_app
from structure.celery_worker.tasks.knowledge_tasks import run_async
from structure.celery_worker.tasks.context_sync._base import (
    _fetch_embedding_service,
    _generate_embedding,
    _store_embedding,
    _upsert_context,
)
from structure.core.enums import ContextType
from structure.utils.context import slugify as _slugify

logger = logging.getLogger(__name__)

# Event types too noisy to include in a run's event log context
_NOISE_EVENT_TYPES = frozenset({"agent.token", "agent_token"})


@celery_app.task(
    bind=True,
    name="context_sync.sync_workspace",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_workspace_to_contexts(self, workspace_id: str, user_id: str):
    """Upsert a Workspace into the Context table and generate its embedding.

    Called when a workspace is created or its name/description changes.
    """
    async def _execute():
        from structure.extensions.database import get_session
        from structure.models.workspaces.workspace import Workspace

        async with get_session("structure") as session:
            ws = await session.get(Workspace, UUID(workspace_id))
            if not ws:
                logger.warning(f"sync_workspace: workspace {workspace_id} not found")
                return

            content = "\n".join(filter(None, [ws.name, ws.description]))

            ctx, needs_embedding = await _upsert_context(
                session,
                user_id=user_id,
                context_type=ContextType.WORKSPACE,
                source_id=workspace_id,
                glance=ws.name,
                content=content,
                path=f"workspaces/{_slugify(ws.name)}",
                tags=["workspace", str(ws.status)],
                meta={
                    "workspace_id": workspace_id,
                    "status": str(ws.status),
                    "run_count": ws.run_count,
                    "owner_id": str(ws.owner_id),
                },
            )

            await session.flush()
            ctx_id = str(ctx.id)
            emb_svc = await _fetch_embedding_service(session)
            await session.commit()

        logger.info(f"sync_workspace: upserted Context for workspace {workspace_id}")

        if needs_embedding and content.strip():
            vector, field = _generate_embedding(content, emb_svc)
            async with get_session("structure") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_workspace: embedded Context {ctx_id}")
        elif not needs_embedding:
            logger.debug(f"sync_workspace: content unchanged, skipped re-embedding {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_workspace failed for {workspace_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.sync_run",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_run_to_contexts(self, run_id: str, user_id: str):
    """Upsert a completed Run into the Context table as a conversation record.

    Only terminal runs (finished / failed / cancelled) are stored.
    Content = user input + agent output, making past conversations semantically searchable.
    """
    async def _execute():
        from structure.extensions.database import get_session
        from structure.models.runs.run import Run

        async with get_session("structure") as session:
            run = await session.get(Run, UUID(run_id))
            if not run:
                logger.warning(f"sync_run: run {run_id} not found")
                return

            if run.status not in ("finished", "failed", "cancelled"):
                logger.info(
                    f"sync_run: run {run_id} is not terminal ({run.status}), skipping"
                )
                return

            input_msg = (run.input_data or {}).get("message", "")
            output_msg = (run.output_data or {}).get("message", "") or str(run.output_data or "")

            glance = input_msg[:80] if input_msg else f"Run {run_id[:8]}"

            parts = []
            if input_msg:
                parts.append(f"User: {input_msg}")
            if output_msg:
                parts.append(f"Assistant: {output_msg}")
            content = "\n\n".join(parts) or f"Run {run_id} ({run.status})"

            run_path = f"runs/{_slugify(run.title)}" if run.title else f"runs/{run_id[:8]}"
            ctx, needs_embedding = await _upsert_context(
                session,
                user_id=user_id,
                context_type=ContextType.RUN,
                source_id=run_id,
                glance=glance,
                content=content,
                path=run_path,
                tags=["run", run.status],
                meta={
                    "run_id": run_id,
                    "workspace_id": str(run.workspace_id),
                    "status": run.status,
                    "app_id": str(run.app_id) if run.app_id else None,
                    "trigger_type": str(run.trigger_type),
                },
            )

            await session.flush()
            ctx_id = str(ctx.id)
            emb_svc = await _fetch_embedding_service(session)
            await session.commit()

        logger.info(f"sync_run: upserted Context for run {run_id} ({run.status})")

        if needs_embedding and content.strip():
            vector, field = _generate_embedding(content[:2000], emb_svc)
            async with get_session("structure") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_run: embedded Context {ctx_id}")
        elif not needs_embedding:
            logger.debug(f"sync_run: content unchanged, skipped re-embedding {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_run failed for {run_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.sync_run_events",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_run_events_to_context(self, run_id: str, user_id: str):
    """Aggregate a run's events into a single Context row as a structured timeline.

    Noise events (agent.token etc.) are filtered out.
    Each remaining event is formatted as: [seq] event_type: payload_preview
    The resulting text is embedded for semantic retrieval of past agent behaviour.
    """
    async def _execute():
        from sqlalchemy import select

        from structure.extensions.database import get_session
        from structure.models.events.event import Event

        async with get_session("structure") as session:
            stmt = (
                select(Event)
                .where(
                    Event.run_id == UUID(run_id),
                    Event.event_type.not_in(list(_NOISE_EVENT_TYPES)),
                )
                .order_by(Event.sequence.asc())
            )
            result = await session.execute(stmt)
            events = list(result.scalars().all())

            if not events:
                logger.info(f"sync_run_events: no events for run {run_id}")
                return

            lines = []
            for ev in events:
                text = ev.to_context()
                if text is not None:
                    lines.append(f"[{ev.sequence:03d}] {text}")

            content = "\n".join(lines)
            glance = f"{len(events)} events — run {run_id[:8]}"
            workspace_id = str(events[0].workspace_id)

            ctx, needs_embedding = await _upsert_context(
                session,
                user_id=user_id,
                context_type=ContextType.RUN_EVENTS,
                source_id=run_id,
                glance=glance,
                content=content,
                path=f"runs/{run_id[:8]}/events",
                tags=["events", "run"],
                meta={
                    "run_id": run_id,
                    "workspace_id": workspace_id,
                    "event_count": len(events),
                },
            )

            await session.flush()
            ctx_id = str(ctx.id)
            emb_svc = await _fetch_embedding_service(session)
            await session.commit()

        logger.info(
            f"sync_run_events: aggregated {len(events)} events for run {run_id}"
        )

        embed_text = content[:2000]  # cap to avoid oversized embedding inputs
        if needs_embedding and embed_text.strip():
            vector, field = _generate_embedding(embed_text, emb_svc)
            async with get_session("structure") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_run_events: embedded Context {ctx_id}")
        elif not needs_embedding:
            logger.debug(f"sync_run_events: content unchanged, skipped re-embedding {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_run_events failed for {run_id}: {e}")
        self.retry(exc=e)


@celery_app.task(
    bind=True,
    name="context_sync.sync_run_to_memory",
    max_retries=3,
    default_retry_delay=30,
    queue="default",
)
def sync_run_to_memory(self, run_id: str, user_id: str):
    """Create a SHORT_MEMORY context entry from a completed run.

    Called after summarize_run writes title/summary so the memory entry
    contains the LLM-generated summary rather than raw input/output.
    Falls back to raw input/output if summary is not available.
    """
    async def _execute():
        from structure.extensions.database import get_session
        from structure.models.runs.run import Run

        async with get_session("structure") as session:
            run = await session.get(Run, UUID(run_id))
            if not run:
                logger.warning(f"sync_run_to_memory: run {run_id} not found")
                return

            if run.status not in ("finished", "failed", "cancelled"):
                logger.info(
                    f"sync_run_to_memory: run {run_id} is not terminal ({run.status}), skipping"
                )
                return

            input_msg = (run.input_data or {}).get("message", "")
            output_msg = (run.output_data or {}).get("message", "") or str(run.output_data or "")

            # Prefer LLM-generated title/summary; fall back to raw content
            glance = run.title or (input_msg[:80] if input_msg else f"Run {run_id[:8]}")

            parts: list[str] = []
            if run.summary:
                parts.append(run.summary)
            if input_msg:
                parts.append(f"User: {input_msg}")
            if output_msg:
                parts.append(f"Assistant: {output_msg}")
            content = "\n\n".join(parts) or f"Run {run_id} ({run.status})"

            memory_path = f"memory/{_slugify(run.title)}" if run.title else f"memory/run-{run_id[:8]}"
            ctx, needs_embedding = await _upsert_context(
                session,
                user_id=user_id,
                context_type=ContextType.SHORT_MEMORY,
                source_id=run_id,
                glance=glance,
                content=content,
                path=memory_path,
                tags=["memory", "run", run.status],
                meta={
                    "run_id": run_id,
                    "workspace_id": str(run.workspace_id),
                    "status": run.status,
                    "app_id": str(run.app_id) if run.app_id else None,
                },
            )

            await session.flush()
            ctx_id = str(ctx.id)
            emb_svc = await _fetch_embedding_service(session)
            await session.commit()

        logger.info(f"sync_run_to_memory: upserted SHORT_MEMORY for run {run_id}")

        if needs_embedding and content.strip():
            vector, field = _generate_embedding(content[:2000], emb_svc)
            async with get_session("structure") as session:
                await _store_embedding(session, ctx_id, vector, field)
                await session.commit()
            logger.info(f"sync_run_to_memory: embedded Context {ctx_id}")
        elif not needs_embedding:
            logger.debug(f"sync_run_to_memory: content unchanged, skipped re-embedding {ctx_id}")

    try:
        run_async(_execute())
    except Exception as e:
        logger.error(f"sync_run_to_memory failed for {run_id}: {e}")
        self.retry(exc=e)
