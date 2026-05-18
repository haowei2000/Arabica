"""Core data types exchanged between a benchmark adapter and a runner.

Everything here is deliberately plain-Python and has no dependency on
the Structure service classes.  A benchmark adapter produces a stream of
:class:`BenchmarkCase` objects; the runner hands each one to an
:class:`AgentProtocol` implementation; the agent returns a
:class:`BenchmarkResult` plus a :class:`CostLedger` snapshot.

Keeping the contract this small lets the same runner drive (a) the
Structure executor stack, (b) a flat-window baseline, and (c) a
deterministic echo agent used in tests --- the three axes the paper's
Experiments section compares.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

EvidenceStatus = Literal["pass", "fail", "unknown"]


@dataclass(frozen=True)
class BenchmarkCase:
    """A single evaluation instance from a benchmark dataset.

    ``task_id`` is the stable identifier reported back to the scorer.
    ``inputs`` holds whatever the benchmark's schema requires (e.g.
    conversation history, tool list, seed environment state) and is
    opaque to the runner.
    ``reference`` holds the gold answer or whatever the scorer needs;
    also opaque to the runner.
    ``ability`` is a coarse-grained category tag used for per-ability
    breakdown (set by benchmarks that expose one; ``None`` otherwise).
    ``metadata`` is free-form (e.g. token-length bucket, difficulty).
    """

    task_id: str
    inputs: dict[str, Any]
    reference: Any
    ability: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CostLedger:
    """Per-case accounting of resource use.

    All fields are additive --- the runner sums them across a batch to
    produce a headline cost number. Agents that run inside Structure
    typically populate ``tokens_prompt`` and ``tokens_completion`` from
    event-sourced ``input_tokens`` / ``output_tokens``; pure baselines
    may populate only a subset.
    """

    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_cached: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    steps: int = 0
    tool_calls: int = 0
    latency_seconds: float = 0.0
    usd_cost: float = 0.0

    @property
    def tokens_total(self) -> int:
        return self.tokens_prompt + self.tokens_completion

    def __add__(self, other: CostLedger) -> CostLedger:
        return CostLedger(
            tokens_prompt=self.tokens_prompt + other.tokens_prompt,
            tokens_completion=self.tokens_completion + other.tokens_completion,
            tokens_cached=self.tokens_cached + other.tokens_cached,
            cache_creation_tokens=self.cache_creation_tokens
            + other.cache_creation_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            steps=self.steps + other.steps,
            tool_calls=self.tool_calls + other.tool_calls,
            latency_seconds=self.latency_seconds + other.latency_seconds,
            usd_cost=self.usd_cost + other.usd_cost,
        )


@dataclass(frozen=True)
class EvidenceRecord:
    """Optional audit metadata for benchmark results.

    Interactive agent benchmarks often need more than a scalar score:
    downstream readers need to know whether the score is backed by
    artifacts, whether the evidence is missing, and whether the score
    should be treated as a bound rather than a point estimate.
    """

    status: EvidenceStatus
    artifacts: tuple[str, ...] = ()
    notes: str | None = None
    score_bounds: tuple[float, float] | None = None


@dataclass(frozen=True)
class BenchmarkResult:
    """Outcome of running a single case through an agent."""

    task_id: str
    response: Any
    cost: CostLedger
    evidence: EvidenceRecord | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentProtocol(Protocol):
    """Minimal async contract a benchmark runner expects.

    Concrete implementations include:
    * ``benchmarks.baselines.echo_agent.EchoAgent`` --- deterministic, no LLM
    * (planned) ``benchmarks.adapters.structure.StructureAgent`` --- drives
      a Structure executor and harvests cost from the event log.
    """

    async def run(self, case: BenchmarkCase) -> BenchmarkResult:  # pragma: no cover
        ...
