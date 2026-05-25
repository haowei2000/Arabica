"""Benchmark adapters for project-specific systems."""

__all__ = [
    "StructureMemoryBenchmarkAgent",
    "StructurePathMemoryBenchmarkAgent",
    "StructureRunBenchmarkAgent",
]


def __getattr__(name: str):
    if name in {"StructureMemoryBenchmarkAgent", "StructurePathMemoryBenchmarkAgent"}:
        from benchmarks.adapters.structure_memory import (
            StructureMemoryBenchmarkAgent,
            StructurePathMemoryBenchmarkAgent,
        )

        return {
            "StructureMemoryBenchmarkAgent": StructureMemoryBenchmarkAgent,
            "StructurePathMemoryBenchmarkAgent": StructurePathMemoryBenchmarkAgent,
        }[name]
    if name == "StructureRunBenchmarkAgent":
        from benchmarks.adapters.structure_run import StructureRunBenchmarkAgent

        return StructureRunBenchmarkAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
