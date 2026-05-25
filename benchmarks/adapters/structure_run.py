"""Structure HTTP run adapter for benchmark cases.

The memory adapters in this package exercise Structure's context store directly.
This adapter drives the public local API instead: authenticate, create or reuse a
workspace, start a run, wait for completion, then harvest the event log for the
benchmark response and cost ledger.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import os
from time import perf_counter
from typing import Any

import httpx

from benchmarks.core.types import (
    BenchmarkCase,
    BenchmarkResult,
    CostLedger,
    EvidenceRecord,
)

TERMINAL_RUN_STATUSES = {"finished", "failed", "cancelled"}
TERMINAL_EVENT_TYPES = {"run.completed", "run.failed", "run.cancelled"}


class StructureRunBenchmarkError(RuntimeError):
    """Raised when the Structure run adapter cannot complete a case."""


def _normalise_base_url(base_url: str) -> tuple[str, str]:
    root = base_url.rstrip("/")
    if root.endswith("/api"):
        return root[: -len("/api")], "/api"
    return root, "/api"


def _event_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _event_type(event: Mapping[str, Any]) -> str:
    return str(event.get("event_type") or event.get("type") or "")


def _event_sequence(event: Mapping[str, Any]) -> int:
    try:
        return int(event.get("sequence") or 0)
    except (TypeError, ValueError):
        return 0


def _event_token_sum(events: Sequence[Mapping[str, Any]], field_name: str) -> int:
    total = 0
    for event in events:
        try:
            total += int(event.get(field_name) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _format_sessions(sessions: object) -> str:
    if not isinstance(sessions, list):
        return ""

    rendered_sessions: list[str] = []
    for session_index, session in enumerate(sessions, start=1):
        if not isinstance(session, list):
            continue
        turns: list[str] = []
        for turn in session:
            if not isinstance(turn, Mapping):
                turns.append(str(turn))
                continue
            role = str(turn.get("role") or turn.get("speaker") or "unknown")
            content = str(turn.get("content") or turn.get("text") or "")
            if content:
                turns.append(f"{role}: {content}")
        if turns:
            rendered_sessions.append(
                f'<session index="{session_index}">\n'
                + "\n".join(turns)
                + "\n</session>"
            )
    return "\n\n".join(rendered_sessions)


def build_benchmark_prompt(case: BenchmarkCase) -> str:
    """Render a benchmark case into the user message sent to Structure."""
    question = str(case.inputs.get("question") or "")
    sessions = _format_sessions(case.inputs.get("sessions"))
    metadata = {
        "task_id": case.task_id,
        "ability": case.ability,
        "benchmark_metadata": case.metadata,
    }
    metadata_text = json.dumps(metadata, ensure_ascii=False, default=str)

    sections = [
        "You are running an evaluation case. Answer the question using only the "
        "provided benchmark context. Return the final answer directly.",
        f"Benchmark metadata:\n{metadata_text}",
    ]
    if sessions:
        sections.append(f"Benchmark context:\n{sessions}")
    sections.append(f"Question:\n{question}")
    return "\n\n".join(sections)


@dataclass
class StructureRunBenchmarkAgent:
    """Drive benchmark cases through a live Structure HTTP API."""

    base_url: str = "http://127.0.0.1:8000"
    token: str | None = None
    username: str | None = None
    password: str | None = None
    workspace_id: str | None = None
    app_id: str | None = None
    executor_code: str | None = "SimpleAgent"
    timeout_seconds: float = 300.0
    poll_interval_seconds: float = 1.0
    cleanup_workspaces: bool = False
    use_sse: bool = True
    client: Any | None = None
    workspace_name_prefix: str = "benchmark"
    extra_run_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        root_url, api_prefix = _normalise_base_url(self.base_url)
        self.root_url = root_url
        self.api_prefix = api_prefix
        self._created_workspace_ids: set[str] = set()
        self._owns_client = self.client is None
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.timeout_seconds)
        if self.token is None:
            self.token = os.getenv("STRUCTURE_BENCHMARK_TOKEN") or None
        if self.username is None:
            self.username = os.getenv("STRUCTURE_BENCHMARK_USERNAME") or None
        if self.password is None:
            self.password = os.getenv("STRUCTURE_BENCHMARK_PASSWORD") or None

    def _url(self, path: str) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        return f"{self.root_url}{self.api_prefix}{normalized}"

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    async def aclose(self) -> None:
        if self._owns_client and self.client is not None:
            await self.client.aclose()

    async def _ensure_authenticated(self) -> None:
        if self.token:
            return
        if not self.username or not self.password:
            return

        response = await self.client.post(
            self._url("/auth/login"),
            data={"username": self.username, "password": self.password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        payload = response.json()
        self.token = str(payload.get("access_token") or "")
        if not self.token:
            raise StructureRunBenchmarkError("login response did not include token")

    async def _create_workspace(self, case: BenchmarkCase) -> str:
        if self.workspace_id:
            return self.workspace_id

        safe_task = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in case.task_id[:80]
        ).strip("-")
        payload: dict[str, Any] = {
            "name": f"{self.workspace_name_prefix}-{safe_task or 'case'}",
            "description": f"Benchmark workspace for {case.task_id}",
        }
        if self.app_id:
            payload["app_id"] = self.app_id
        if self.executor_code:
            payload["executor_code"] = self.executor_code

        response = await self.client.post(
            self._url("/workspaces"),
            json=payload,
            headers=self._headers(),
        )
        response.raise_for_status()
        workspace = response.json()
        workspace_id = str(workspace["id"])
        self._created_workspace_ids.add(workspace_id)
        return workspace_id

    async def _delete_workspace(self, workspace_id: str) -> None:
        if (
            not self.cleanup_workspaces
            or workspace_id not in self._created_workspace_ids
        ):
            return
        response = await self.client.delete(
            self._url(f"/workspaces/{workspace_id}"),
            headers=self._headers(),
        )
        if getattr(response, "status_code", 204) not in {200, 202, 204, 404}:
            response.raise_for_status()

    async def _start_run(
        self, case: BenchmarkCase, workspace_id: str
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_type": "user.message",
            "workspace_id": workspace_id,
            "payload": {"message": build_benchmark_prompt(case)},
            "metadata": {
                "benchmark_task_id": case.task_id,
                "benchmark_ability": case.ability,
                **self.extra_run_metadata,
            },
        }
        if self.app_id:
            payload["app_id"] = self.app_id

        response = await self.client.post(
            self._url(f"/workspaces/{workspace_id}/runs"),
            json=payload,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def _get_run(self, run_id: str) -> dict[str, Any]:
        response = await self.client.get(
            self._url(f"/runs/{run_id}"),
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def _stream_until_terminal(self, run_id: str) -> dict[str, Any] | None:
        if not self.use_sse or not hasattr(self.client, "stream"):
            return None

        try:
            async with self.client.stream(
                "GET",
                self._url(f"/runs/{run_id}/events/stream"),
                headers=self._headers(),
                timeout=self.timeout_seconds,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    try:
                        event = json.loads(stripped.removeprefix("data:").strip())
                    except json.JSONDecodeError:
                        continue
                    if _event_type(event) in TERMINAL_EVENT_TYPES:
                        return await self._get_run(run_id)
                    if event.get("reason") == "run_finished":
                        return await self._get_run(run_id)
        except Exception:
            return None
        return None

    async def _poll_until_terminal(
        self, run_id: str, started_at: float
    ) -> dict[str, Any]:
        while True:
            run = await self._get_run(run_id)
            if str(run.get("status")) in TERMINAL_RUN_STATUSES:
                return run
            if perf_counter() - started_at >= self.timeout_seconds:
                raise TimeoutError(f"run {run_id} did not finish before timeout")
            await asyncio.sleep(self.poll_interval_seconds)

    async def _wait_for_terminal_run(
        self,
        run_id: str,
        started_at: float,
    ) -> dict[str, Any]:
        streamed = await self._stream_until_terminal(run_id)
        if streamed and str(streamed.get("status")) in TERMINAL_RUN_STATUSES:
            return streamed
        return await self._poll_until_terminal(run_id, started_at)

    async def _fetch_events(self, run_id: str) -> list[dict[str, Any]]:
        response = await self.client.get(
            self._url(f"/runs/{run_id}/events"),
            params={"from_sequence": 0, "limit": 1000},
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]

    async def _fetch_state(self, run_id: str) -> dict[str, Any] | None:
        response = await self.client.get(
            self._url(f"/runs/{run_id}/state"),
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def extract_final_response(
        events: Sequence[Mapping[str, Any]],
        run_state: Mapping[str, Any] | None = None,
    ) -> str:
        """Extract the latest assistant content from replayed run events."""
        agent_events = [
            event
            for event in sorted(events, key=_event_sequence)
            if _event_type(event) == "agent.message"
        ]
        for event in reversed(agent_events):
            payload = _event_payload(event)
            content = payload.get("content") or payload.get("message")
            if isinstance(content, str) and content.strip():
                return content.strip()

        if run_state:
            for key in ("final_response", "response", "answer", "content"):
                value = run_state.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _cost_from_run_and_events(
        run: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        latency_seconds: float,
    ) -> CostLedger:
        input_tokens = int(run.get("input_tokens") or 0) or _event_token_sum(
            events,
            "input_tokens",
        )
        output_tokens = int(run.get("output_tokens") or 0) or _event_token_sum(
            events,
            "output_tokens",
        )
        steps = sum(1 for event in events if _event_type(event).startswith("agent."))
        tool_calls = sum(1 for event in events if _event_type(event) == "tool.call")
        return CostLedger(
            tokens_prompt=input_tokens,
            tokens_completion=output_tokens,
            steps=steps,
            tool_calls=tool_calls,
            latency_seconds=latency_seconds,
        )

    @staticmethod
    def _evidence_for_run(
        run: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        response: str,
    ) -> EvidenceRecord:
        status = str(run.get("status") or "")
        if status == "finished" and response:
            evidence_status = "pass"
        elif status in {"failed", "cancelled"}:
            evidence_status = "fail"
        else:
            evidence_status = "unknown"
        return EvidenceRecord(
            status=evidence_status,
            artifacts=tuple(
                str(event.get("id")) for event in events if event.get("id")
            ),
            notes=f"Structure run status: {status or 'unknown'}",
        )

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:
        await self._ensure_authenticated()
        workspace_id = await self._create_workspace(case)
        run: dict[str, Any] | None = None
        started_at = perf_counter()
        try:
            run = await self._start_run(case, workspace_id)
            run_id = str(run["id"])
            final_run = await self._wait_for_terminal_run(run_id, started_at)
            events = await self._fetch_events(run_id)
            run_state = await self._fetch_state(run_id)
            latency_seconds = perf_counter() - started_at
            response = self.extract_final_response(events, run_state)
            cost = self._cost_from_run_and_events(
                final_run,
                events,
                latency_seconds,
            )
            return BenchmarkResult(
                task_id=case.task_id,
                response=response,
                cost=cost,
                evidence=self._evidence_for_run(final_run, events, response),
                metadata={
                    "adapter": "StructureRunBenchmarkAgent",
                    "workspace_id": workspace_id,
                    "run_id": run_id,
                    "app_id": self.app_id,
                    "executor_code": self.executor_code,
                    "run_status": str(final_run.get("status") or ""),
                    "event_count": len(events),
                    "final_event_sequence": max(
                        (_event_sequence(event) for event in events),
                        default=0,
                    ),
                },
            )
        finally:
            if run is not None:
                await self._delete_workspace(workspace_id)
