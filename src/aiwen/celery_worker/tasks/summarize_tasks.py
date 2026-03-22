"""Celery tasks for LLM-powered run and workspace summarization.

After a run reaches a terminal state, ``summarize_run`` generates a short
title and a multi-sentence summary from the conversation content and writes
them back to ``Run.title`` and ``Run.summary``.

After each run is summarized, ``summarize_workspace`` aggregates the most
recent run summaries and generates an updated ``Workspace.summary``.

Both tasks also keep the corresponding Context entries (managed by
ContextSyncer) up to date so the in-memory ContextStore reflects the
latest summaries.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from aiwen.celery_worker.celery_app import celery_app
from aiwen.celery_worker.tasks.knowledge_tasks import run_async

logger = logging.getLogger(__name__)

# Maximum number of recent run summaries fed into the workspace prompt.
_WORKSPACE_RUN_WINDOW = 10

# Default LLM used for summarization (cheap, fast model).
_SUMMARY_PROVIDER = "tongyi"
_SUMMARY_MODEL = "qwen-turbo"


# ─── LLM helper ───────────────────────────────────────────────────────────────

def _call_llm(prompt: str) -> str:
    """Call the summarization LLM synchronously and return raw text."""
    from langchain_core.messages import HumanMessage

    from aiwen.extensions.llm.llm import get_llm

    llm = get_llm(_SUMMARY_MODEL, provider=_SUMMARY_PROVIDER)
    response = llm.invoke([HumanMessage(content=prompt)])
    return response.content


def _parse_title_summary(raw: str) -> tuple[str, str]:
    """Extract (title, summary) from the LLM JSON response.

    Falls back gracefully if the model returns plain text instead of JSON.
    """
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
        title = str(data.get("title", "")).strip()
        summary = str(data.get("summary", "")).strip()
        if title and summary:
            return title, summary
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: use first line as title, rest as summary
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    title = lines[0][:255] if lines else "Untitled"
    summary = " ".join(lines[1:]) if len(lines) > 1 else title
    return title, summary


# ─── Tasks ────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="summarize.run",
    max_retries=2,
    default_retry_delay=10,
    queue="default",
)
def summarize_run(self, run_id: str, user_id: str) -> None:
    """Generate a title and summary for a completed run using an LLM.

    Writes results to ``Run.title`` and ``Run.summary``, then refreshes the
    corresponding Context entry (glance + summary fields) and dispatches
    ``summarize_workspace`` to keep the workspace-level summary current.
    """
    async def _execute() -> None:
        from aiwen.extensions.database import get_session
        from aiwen.models.runs.run import Run
        from aiwen.services.context.context_syncer import ContextSyncer

        async with get_session("aiwen") as session:
            run = await session.get(Run, UUID(run_id))
            if not run:
                logger.warning("summarize_run: run %s not found", run_id)
                return

            if not run.is_terminal:
                logger.info(
                    "summarize_run: run %s is not terminal (%s), skipping",
                    run_id,
                    run.status,
                )
                return

            input_msg = (run.input_data or {}).get("message", "")
            output_msg = (run.output_data or {}).get("message", "") or str(
                run.output_data or ""
            )
            workspace_id = str(run.workspace_id)

        if not input_msg and not output_msg:
            logger.info("summarize_run: run %s has no content to summarize", run_id)
            return

        # ── LLM call (outside DB session, blocking) ───────────────────────
        prompt = _build_run_prompt(
            input_msg=input_msg,
            output_msg=output_msg,
            status=run.status,
        )
        raw = _call_llm(prompt)
        title, summary = _parse_title_summary(raw)

        # ── Write back to Run and Context ─────────────────────────────────
        async with get_session("aiwen") as session:
            run = await session.get(Run, UUID(run_id))
            if run is None:
                return
            run.title = title
            run.summary = summary

            syncer = ContextSyncer(session)
            await syncer.sync_run(run)
            await session.commit()

        logger.info(
            "summarize_run: run=%s title=%r summary_len=%d",
            run_id,
            title,
            len(summary),
        )

        # ── Trigger workspace summary refresh ─────────────────────────────
        summarize_workspace.delay(workspace_id, user_id)

    try:
        run_async(_execute())
    except Exception as exc:
        logger.error("summarize_run failed for %s: %s", run_id, exc)
        self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="summarize.workspace",
    max_retries=2,
    default_retry_delay=15,
    queue="default",
)
def summarize_workspace(self, workspace_id: str, user_id: str) -> None:
    """Generate an updated summary for a workspace from its recent runs.

    Reads the last ``_WORKSPACE_RUN_WINDOW`` completed runs (those with an
    LLM-generated title/summary), builds a prompt, and writes the result to
    ``Workspace.summary``.  Also refreshes the Context entry for the workspace.
    """
    async def _execute() -> None:
        from sqlalchemy import select

        from aiwen.extensions.database import get_session
        from aiwen.models.runs.run import Run
        from aiwen.models.workspaces.workspace import Workspace
        from aiwen.services.context.context_syncer import ContextSyncer

        async with get_session("aiwen") as session:
            workspace = await session.get(Workspace, UUID(workspace_id))
            if not workspace:
                logger.warning(
                    "summarize_workspace: workspace %s not found", workspace_id
                )
                return

            ws_name = workspace.name
            ws_description = workspace.description or ""

            # Fetch recent completed runs that already have an LLM summary
            stmt = (
                select(Run)
                .where(
                    Run.workspace_id == UUID(workspace_id),
                    Run.status.in_(("finished", "failed", "cancelled")),
                    Run.summary.isnot(None),
                )
                .order_by(Run.completed_at.desc())
                .limit(_WORKSPACE_RUN_WINDOW)
            )
            result = await session.execute(stmt)
            recent_runs = result.scalars().all()

        if not recent_runs:
            logger.info(
                "summarize_workspace: workspace %s has no summarized runs yet",
                workspace_id,
            )
            return

        # ── LLM call (outside DB session) ─────────────────────────────────
        prompt = _build_workspace_prompt(
            workspace_name=ws_name,
            workspace_description=ws_description,
            runs=recent_runs,
        )
        raw = _call_llm(prompt)
        _, summary = _parse_title_summary(raw)

        # ── Write back to Workspace and Context ───────────────────────────
        async with get_session("aiwen") as session:
            workspace = await session.get(Workspace, UUID(workspace_id))
            if workspace is None:
                return
            workspace.summary = summary

            syncer = ContextSyncer(session)
            await syncer.sync_workspace(workspace)
            await session.commit()

        logger.info(
            "summarize_workspace: workspace=%s summary_len=%d based_on=%d runs",
            workspace_id,
            len(summary),
            len(recent_runs),
        )

    try:
        run_async(_execute())
    except Exception as exc:
        logger.error("summarize_workspace failed for %s: %s", workspace_id, exc)
        self.retry(exc=exc)


# ─── Prompt builders ──────────────────────────────────────────────────────────

def _build_run_prompt(
    *,
    input_msg: str,
    output_msg: str,
    status: str,
) -> str:
    sections: list[str] = []
    if input_msg:
        sections.append(f"User: {input_msg[:1000]}")
    if output_msg:
        sections.append(f"Assistant: {output_msg[:1000]}")
    if status != "finished":
        sections.append(f"Status: {status}")

    conversation = "\n".join(sections)
    return f"""You are a helpful assistant that summarizes conversations.

Given the following conversation:

{conversation}

Generate:
1. A concise title (at most 10 words) that captures the main topic.
2. A brief summary (2-3 sentences) describing what was discussed and accomplished.

Respond ONLY with valid JSON in this exact format:
{{"title": "<title here>", "summary": "<summary here>"}}"""


def _build_workspace_prompt(
    *,
    workspace_name: str,
    workspace_description: str,
    runs: list,
) -> str:
    run_lines: list[str] = []
    for i, run in enumerate(runs, 1):
        title = run.title or f"Run {str(run.id)[:8]}"
        summary = run.summary or ""
        status = run.status
        run_lines.append(f"{i}. [{status}] {title}: {summary}")

    runs_text = "\n".join(run_lines)
    desc_line = f"Description: {workspace_description}\n" if workspace_description else ""

    return f"""You are a helpful assistant that summarizes workspaces.

Workspace: {workspace_name}
{desc_line}
Recent activity ({len(runs)} runs):
{runs_text}

Generate:
1. A brief title for this workspace (at most 10 words).
2. A summary (3-5 sentences) describing the workspace's main purpose and recent activity.

Respond ONLY with valid JSON in this exact format:
{{"title": "<title here>", "summary": "<summary here>"}}"""
