"""LongMemEval-V2 adapter.

LongMemEval-V2 evaluates whether a memory system can ingest large
agent-environment histories and answer later queries with supporting
evidence. The local fixture is synthetic and intentionally tiny; it
keeps CI focused on schema and scorer wiring until the full upstream
dataset is integrated.
"""

from benchmarks.longmemeval_v2.dataset import load_longmemeval_v2
from benchmarks.longmemeval_v2.scorer import longmemeval_v2_scorer

__all__ = ["load_longmemeval_v2", "longmemeval_v2_scorer"]
