"""LongMemEval adapter.

Reference: Wu et al., ICLR 2025 (arXiv:2410.10813).  Dataset and full
download instructions live in ``README.md`` next to this file; the
production loader reads the upstream JSON schema, and a tiny synthetic
fixture at ``fixtures/sample.json`` exercises the harness in tests
without requiring the real dataset.
"""

from benchmarks.longmemeval.dataset import load_longmemeval
from benchmarks.longmemeval.scorer import longmemeval_scorer

__all__ = ["load_longmemeval", "longmemeval_scorer"]
