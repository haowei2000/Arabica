"""LongMemEval-V2 adapter.

LongMemEval-V2 evaluates whether a memory system can ingest large
agent-environment histories and answer later queries with supporting
evidence. The local fixture is synthetic and intentionally tiny; it
keeps CI focused on schema and scorer wiring until the full upstream
dataset is integrated.
"""

from benchmarks.longmemeval_v2.dataset import load_longmemeval_v2
from benchmarks.longmemeval_v2.release import (
    build_trajectory_offset_index,
    extract_trajectory_evidence,
    load_longmemeval_v2_release,
    read_trajectories_by_id,
    trajectory_evidence_id,
    trajectory_state_to_text,
)
from benchmarks.longmemeval_v2.scorer import (
    longmemeval_v2_scorer,
    parse_eval_function,
    score_with_eval_function,
)

__all__ = [
    "build_trajectory_offset_index",
    "extract_trajectory_evidence",
    "load_longmemeval_v2",
    "load_longmemeval_v2_release",
    "longmemeval_v2_scorer",
    "parse_eval_function",
    "read_trajectories_by_id",
    "score_with_eval_function",
    "trajectory_evidence_id",
    "trajectory_state_to_text",
]
