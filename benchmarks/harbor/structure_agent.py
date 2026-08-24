"""Thin Harbor bridge for the Rust Structure runtime.

All provider, agent-loop, event-history, and memory-policy behavior stays in
the Rust binary. This adapter only translates shell requests to Harbor's
isolated BaseEnvironment interface.
"""

import asyncio
import json
import os
import shlex
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class StructureAgent(BaseAgent):
    """Run Structure on the host and execute its tools in a Harbor environment."""

    @staticmethod
    def name() -> str:
        return "structure"

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        strategy: str = "FBGC",
        max_steps: int = 128,
        max_tokens: int = 8192,
        checkpoint_batches: int = 8,
        pgc_effort: int | None = None,
        pgc_continuation_probability_bps: int | None = None,
        thinking: bool | str | None = None,
        binary_path: str | None = None,
        **kwargs,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        normalized = strategy.upper()
        if normalized not in {"B0", "PGC", "FBGC"}:
            raise ValueError("strategy must be B0, PGC, or FBGC")
        self.strategy = normalized
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.checkpoint_batches = checkpoint_batches
        self.pgc_effort = (
            pgc_effort
            if pgc_effort is not None
            else int(
                os.environ.get(
                    "COMPACTION_EFFORT", os.environ.get("PGC_EFFORT", "1")
                )
            )
        )
        if self.pgc_effort < 1:
            raise ValueError("pgc_effort must be positive")
        self.pgc_continuation_probability_bps = (
            pgc_continuation_probability_bps
            if pgc_continuation_probability_bps is not None
            else int(os.environ.get("PGC_CONTINUATION_PROBABILITY_BPS", "7500"))
        )
        if not 0 <= self.pgc_continuation_probability_bps <= 10_000:
            raise ValueError(
                "pgc_continuation_probability_bps must be between 0 and 10000"
            )
        if thinking is None:
            thinking_env = os.environ.get("STRUCTURE_THINKING", "true")
            self.thinking = str(thinking_env).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        elif isinstance(thinking, bool):
            self.thinking = thinking
        else:
            self.thinking = str(thinking).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        self.binary_path = binary_path or os.environ.get(
            "STRUCTURE_HARBOR_AGENT_BIN",
            "target/release/harbor_agent",
        )

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment
        binary = Path(self.binary_path).expanduser().resolve()
        if not binary.is_file():
            raise FileNotFoundError(
                f"Structure Harbor binary not found at {binary}; build --bin harbor_agent"
            )
        self.binary_path = str(binary)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        report_path = self.logs_dir / "structure-report.json"
        model = (self.model_name or os.environ.get("OPENAI_MODEL") or "").split(
            "/", maxsplit=1
        )[-1]
        command = [
            self.binary_path,
            "--strategy",
            self.strategy,
            "--report",
            str(report_path),
            "--max-steps",
            str(self.max_steps),
            "--max-tokens",
            str(self.max_tokens),
            "--checkpoint-batches",
            str(self.checkpoint_batches),
            "--pgc-effort",
            str(self.pgc_effort),
            "--pgc-continuation-probability-bps",
            str(self.pgc_continuation_probability_bps),
            "--thinking",
            "true" if self.thinking else "false",
        ]
        if model:
            command.extend(["--model", model])

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_task = asyncio.create_task(
            self._capture_stderr(process.stderr, self.logs_dir / "structure-stderr.log")
        )
        tool_exchange_index = 0

        try:
            ready = await self._read_message(process.stdout)
            if ready.get("type") != "ready":
                raise RuntimeError(f"Structure bridge did not become ready: {ready}")
            await self._write_message(
                process.stdin, {"type": "start", "instruction": instruction}
            )

            while True:
                message = await self._read_message(process.stdout)
                message_type = message.get("type")
                if message_type == "done":
                    break
                if message_type != "exec":
                    raise RuntimeError(f"unknown Structure bridge message: {message}")
                tool_exchange_index += 1
                raw_exchange_dir = self._begin_tool_raw_exchange(
                    tool_exchange_index, message
                )
                try:
                    command = message["command"]
                    if message.get("strict_pipeline", False):
                        command = f"bash -o pipefail -c {shlex.quote(command)}"
                    result = await environment.exec(
                        command=command,
                        cwd=message.get("cwd") or "/app",
                        timeout_sec=int(message.get("timeout_sec", 120)),
                    )
                    response = {
                        "type": "exec_result",
                        "id": message["id"],
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "return_code": result.return_code,
                    }
                except Exception as error:  # Provider timeout types vary.
                    response = {
                        "type": "exec_result",
                        "id": message["id"],
                        "stdout": None,
                        "stderr": f"Harbor environment execution failed: {error}",
                        "return_code": 124,
                    }
                self._finish_tool_raw_exchange(raw_exchange_dir, response)
                await self._write_message(process.stdin, response)

            return_code = await process.wait()
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            await stderr_task
            if not report_path.is_file():
                self._populate_context_from_partial(context)
        if return_code != 0:
            raise RuntimeError(f"Structure agent exited with code {return_code}")
        report = json.loads(report_path.read_text())
        context.n_input_tokens = report["input_tokens"]
        context.n_cache_tokens = report["cached_input_tokens"]
        context.n_output_tokens = report["output_tokens"]
        context.metadata = {
            "strategy": report["strategy"],
            "compaction_strategy": report["compaction_strategy"],
            "report_path": str(report_path),
            "terminal_success": report["terminal_success"],
            "uncached_input_tokens": report["uncached_input_tokens"],
            "peak_input_tokens": report["peak_input_tokens"],
            "peak_model_input_bytes": report["peak_model_input_bytes"],
            "cache_reset_count": report["cache_reset_count"],
            "pointer_transition_cache_reset_count": report[
                "pointer_transition_cache_reset_count"
            ],
            "pgc_continuation_probability_bps": report[
                "pgc_continuation_probability_bps"
            ],
            "pointer_gc_admission_checks": report["pointer_gc_admission_checks"],
            "pointer_gc_admissions": report["pointer_gc_admissions"],
            "provider_latency_ms": report["provider_latency_ms"],
            "tool_calls": report["tool_calls"],
            "tool_errors": report["tool_errors"],
            "memory_search_calls": report["memory_search_calls"],
            "memory_read_calls": report["memory_read_calls"],
            "memory_pointer_appearances": report["memory_pointer_appearances"],
            "auto_hydration_count": report["auto_hydration_count"],
            "auto_hydrated_bytes": report["auto_hydrated_bytes"],
            "gc_quality_gate": report.get("gc_quality_gate"),
            "provider_raw_dir": str(self.logs_dir / "provider-raw"),
            "tool_raw_dir": str(self.logs_dir / "tool-raw"),
        }

    def _populate_context_from_partial(self, context: AgentContext) -> None:
        provider_path = self.logs_dir / "provider-calls.partial.json"
        if not provider_path.is_file():
            return
        calls = json.loads(provider_path.read_text())
        admissions_path = self.logs_dir / "pointer-gc-admissions.partial.json"
        admissions = (
            json.loads(admissions_path.read_text())
            if admissions_path.is_file()
            else []
        )
        input_tokens = sum(call.get("input_tokens", 0) for call in calls)
        cached_tokens = sum(call.get("cached_input_tokens", 0) for call in calls)
        context.n_input_tokens = input_tokens
        context.n_cache_tokens = cached_tokens
        context.n_output_tokens = sum(call.get("output_tokens", 0) for call in calls)
        cache_resets = sum(
            previous.get("cached_input_tokens", 0) > 0
            and current.get("cached_input_tokens", 0) == 0
            for previous, current in zip(calls, calls[1:])
        )
        context.metadata = {
            "strategy": self.strategy,
            "partial_report": True,
            "uncached_input_tokens": input_tokens - cached_tokens,
            "cache_reset_count": cache_resets,
            "provider_calls": len(calls),
            "memory_pointer_appearances": sum(
                call.get("memory_pointer_entries", 0) for call in calls
            ),
            "pgc_continuation_probability_bps": (
                self.pgc_continuation_probability_bps
            ),
            "pointer_gc_admission_checks": len(admissions),
            "pointer_gc_admissions": sum(
                bool(observation.get("admitted")) for observation in admissions
            ),
            "provider_raw_dir": str(self.logs_dir / "provider-raw"),
            "tool_raw_dir": str(self.logs_dir / "tool-raw"),
        }

    def _begin_tool_raw_exchange(self, index: int, message: dict) -> Path:
        call_id = self._safe_path_component(str(message.get("id", "call")))
        exchange_dir = self.logs_dir / "tool-raw" / f"{index:04d}-{call_id}"
        exchange_dir.mkdir(parents=True, exist_ok=False)
        request = {
            "id": message.get("id"),
            "command": message.get("command"),
            "cwd": message.get("cwd") or "/app",
            "timeout_sec": int(message.get("timeout_sec", 120)),
            "strict_pipeline": bool(message.get("strict_pipeline", False)),
        }
        (exchange_dir / "request.raw.json").write_text(
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return exchange_dir

    @staticmethod
    def _finish_tool_raw_exchange(exchange_dir: Path, response: dict) -> None:
        stdout = response.get("stdout") or ""
        stderr = response.get("stderr") or ""
        (exchange_dir / "stdout.raw").write_text(stdout, encoding="utf-8")
        (exchange_dir / "stderr.raw").write_text(stderr, encoding="utf-8")
        result = {
            "id": response.get("id"),
            "return_code": response.get("return_code"),
            "stdout_bytes": len(stdout.encode()),
            "stderr_bytes": len(stderr.encode()),
        }
        (exchange_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    @staticmethod
    def _safe_path_component(value: str) -> str:
        sanitized = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in value
        )[:80]
        return sanitized or "call"

    @staticmethod
    async def _read_message(stream: asyncio.StreamReader) -> dict:
        line = await stream.readline()
        if not line:
            raise RuntimeError("Structure bridge closed unexpectedly")
        return json.loads(line)

    @staticmethod
    async def _write_message(
        stream: asyncio.StreamWriter, message: dict
    ) -> None:
        stream.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await stream.drain()

    @staticmethod
    async def _capture_stderr(
        stream: asyncio.StreamReader, destination: Path
    ) -> None:
        with destination.open("wb") as output:
            while chunk := await stream.read(8192):
                output.write(chunk)
