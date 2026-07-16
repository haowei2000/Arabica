"""Tests for the Structure HTTP run benchmark adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from benchmarks.adapters.structure_run import (
    StructureRunBenchmarkAgent,
    build_benchmark_prompt,
)
from benchmarks.core import BenchmarkCase
import pytest


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeStream:
    def __init__(self, lines: list[str]):
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _FakeStructureClient:
    def __init__(
        self,
        *,
        run_statuses: list[str] | None = None,
        stream_lines: list[str] | None = None,
        events: list[dict[str, Any]] | None = None,
        state_payload: dict[str, Any] | None = None,
        run_payload: dict[str, Any] | None = None,
    ):
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.run_statuses = run_statuses or ["finished"]
        self.stream_lines = stream_lines or []
        self.state_payload = state_payload if state_payload is not None else {}
        self.run_payload = run_payload or {}
        self.closed = False
        self.events = events or [
            {
                "id": "event-user",
                "event_type": "user.message",
                "sequence": 1,
                "payload": {"message": "question"},
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "id": "event-tool",
                "event_type": "tool.call",
                "sequence": 2,
                "payload": {"tool_name": "read_context"},
                "input_tokens": 0,
                "output_tokens": 0,
            },
            {
                "id": "event-agent",
                "event_type": "agent.message",
                "sequence": 3,
                "payload": {"content": "Lisbon"},
                "input_tokens": 23,
                "output_tokens": 4,
            },
        ]

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        if url.endswith("/auth/login"):
            return _FakeResponse({"access_token": "token-1"})
        if url.endswith("/workspaces"):
            return _FakeResponse({"id": "workspace-1"})
        if url.endswith("/workspaces/workspace-1/runs"):
            return _FakeResponse({"id": "run-1", "status": "running"})
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        if url.endswith("/runs/run-1/events"):
            return _FakeResponse({"items": self.events})
        if url.endswith("/runs/run-1/state"):
            return _FakeResponse(self.state_payload)
        if url.endswith("/runs/run-1"):
            status = self.run_statuses.pop(0) if self.run_statuses else "finished"
            payload = {
                "id": "run-1",
                "workspace_id": "workspace-1",
                "status": status,
                "input_tokens": 23,
                "output_tokens": 4,
            }
            payload.update(self.run_payload)
            return _FakeResponse(payload)
        raise AssertionError(f"unexpected GET {url}")

    async def delete(self, url, **kwargs):
        self.requests.append(("DELETE", url, kwargs))
        return _FakeResponse({}, status_code=204)

    def stream(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return _FakeStream(self.stream_lines)

    async def aclose(self):
        self.closed = True


@pytest.mark.unit
def test_structure_run_adapter_completes_case_through_http_api():
    case = BenchmarkCase(
        task_id="case/1",
        inputs={
            "question": "Where did the user live?",
            "sessions": [[{"role": "user", "content": "The user lived in Lisbon."}]],
        },
        reference="Lisbon",
    )
    client = _FakeStructureClient()
    agent = StructureRunBenchmarkAgent(
        base_url="http://structure.test",
        username="admin",
        password="secret",
        client=client,
        use_sse=False,
        cleanup_workspaces=True,
        poll_interval_seconds=0,
    )

    result = asyncio.run(agent.run(case))

    assert result.response == "Lisbon"
    assert result.cost.tokens_prompt == 23
    assert result.cost.tokens_completion == 4
    assert result.cost.tool_calls == 1
    assert result.evidence is not None
    assert result.evidence.status == "pass"
    assert result.metadata["workspace_id"] == "workspace-1"
    assert result.metadata["run_id"] == "run-1"

    auth_request = client.requests[0]
    assert auth_request[0] == "POST"
    assert auth_request[1].endswith("/api/auth/login")

    run_request = next(
        request for request in client.requests if request[1].endswith("/runs")
    )
    assert run_request[2]["json"]["event_type"] == "user.message"
    assert run_request[2]["json"]["workspace_id"] == "workspace-1"
    assert "Where did the user live?" in run_request[2]["json"]["payload"]["message"]
    assert any(request[0] == "DELETE" for request in client.requests)


@pytest.mark.unit
def test_structure_run_adapter_uses_sse_before_polling():
    case = BenchmarkCase(
        task_id="case-2",
        inputs={"question": "Q", "sessions": []},
        reference="A",
    )
    client = _FakeStructureClient(
        stream_lines=[
            "event: run.completed",
            f"data: {json.dumps({'event_type': 'run.completed'})}",
            "",
        ]
    )
    agent = StructureRunBenchmarkAgent(
        token="token-1",
        client=client,
        poll_interval_seconds=0,
    )

    result = asyncio.run(agent.run(case))

    assert result.response == "Lisbon"
    assert any(url.endswith("/events/stream") for _, url, _ in client.requests)


@pytest.mark.unit
def test_structure_run_adapter_extracts_nested_state_costs_and_artifacts():
    case = BenchmarkCase(
        task_id="case-nested",
        inputs={"question": "Q", "sessions": []},
        reference="Nested answer",
    )
    client = _FakeStructureClient(
        run_payload={
            "input_tokens": 0,
            "output_tokens": 0,
            "metadata": {
                "cost_ledger": {
                    "tokens_prompt": 101,
                    "tokens_completion": 17,
                    "tokens_cached": 9,
                    "cache_read_tokens": 9,
                    "tool_calls": 3,
                    "steps": 5,
                    "usd_cost": 0.0123,
                }
            },
        },
        events=[
            {
                "event_id": "evt-1",
                "event_type": "agent.message",
                "sequence": 1,
                "payload": {
                    "content": "",
                    "tool_calls": [{"name": "a"}, {"name": "b"}],
                },
            },
            {
                "event_id": "evt-2",
                "event_type": "tool.result",
                "sequence": 2,
                "payload": {
                    "artifact_id": "artifact-1",
                    "artifacts": [{"path": "/tmp/answer.md"}],
                },
            },
        ],
        state_payload={
            "messages": [
                {"role": "user", "content": "Q"},
                {"role": "assistant", "content": "Nested answer"},
            ]
        },
    )
    agent = StructureRunBenchmarkAgent(
        token="token-1",
        client=client,
        use_sse=False,
        poll_interval_seconds=0,
    )

    result = asyncio.run(agent.run(case))

    assert result.response == "Nested answer"
    assert result.cost.tokens_prompt == 101
    assert result.cost.tokens_completion == 17
    assert result.cost.tokens_cached == 9
    assert result.cost.cache_read_tokens == 9
    assert result.cost.tool_calls == 3
    assert result.cost.steps == 5
    assert result.cost.usd_cost == 0.0123
    assert result.evidence is not None
    assert "evt-1" in result.evidence.artifacts
    assert "artifact-1" in result.evidence.artifacts
    assert "/tmp/answer.md" in result.evidence.artifacts


@pytest.mark.unit
def test_structure_run_adapter_times_out_running_case():
    case = BenchmarkCase(
        task_id="case-3",
        inputs={"question": "Q", "sessions": []},
        reference="A",
    )
    client = _FakeStructureClient(run_statuses=["running", "running"])
    agent = StructureRunBenchmarkAgent(
        token="token-1",
        client=client,
        timeout_seconds=0,
        poll_interval_seconds=0,
        use_sse=False,
    )

    with pytest.raises(TimeoutError):
        asyncio.run(agent.run(case))


@pytest.mark.unit
def test_build_benchmark_prompt_includes_sessions_and_metadata():
    case = BenchmarkCase(
        task_id="prompt-case",
        inputs={
            "question": "What city?",
            "sessions": [[{"role": "user", "content": "Paris"}]],
        },
        reference="Paris",
        ability="qa",
        metadata={"source": "fixture"},
    )

    prompt = build_benchmark_prompt(case)

    assert "prompt-case" in prompt
    assert "qa" in prompt
    assert "user: Paris" in prompt
    assert "What city?" in prompt
