"""Core abstractions shared by every benchmark adapter."""

from benchmarks.core.metrics import BenchmarkReport, aggregate
from benchmarks.core.runner import BenchmarkRunner
from benchmarks.core.types import (
    AgentProtocol,
    BenchmarkCase,
    BenchmarkResult,
    CostLedger,
    EvidenceRecord,
)

__all__ = [
    "AgentProtocol",
    "BenchmarkCase",
    "BenchmarkReport",
    "BenchmarkResult",
    "BenchmarkRunner",
    "CostLedger",
    "EvidenceRecord",
    "aggregate",
]
