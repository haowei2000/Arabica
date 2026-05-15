"""Baseline agents and external reference data used by the harness.

* :class:`EchoAgent` --- deterministic, no-LLM; the harness regression tests
  drive it to verify runner/metric plumbing without paying for tokens.
* LightMem/MemBase reference tables --- reported baseline data used to keep
  code, paper tables, and benchmark planning aligned.
"""

from benchmarks.baselines.echo_agent import EchoAgent
from benchmarks.baselines.lightmem_reference import (
    LIGHTMEM_LOCOMO_OVERVIEW,
    MEMORY_BASELINE_CATALOG,
    LightMemLoCoMoOverview,
    MemoryBaselineSpec,
    iter_baseline_catalog,
    iter_locomo_overview,
    locomo_overview_dicts,
)
from benchmarks.baselines.llm_agent import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LLMBenchmarkAgent,
)
from benchmarks.baselines.memory_agents import (
    RetrievalOracleBaseline,
    make_memory_baseline,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LIGHTMEM_LOCOMO_OVERVIEW",
    "MEMORY_BASELINE_CATALOG",
    "EchoAgent",
    "LLMBenchmarkAgent",
    "LightMemLoCoMoOverview",
    "MemoryBaselineSpec",
    "RetrievalOracleBaseline",
    "iter_baseline_catalog",
    "iter_locomo_overview",
    "locomo_overview_dicts",
    "make_memory_baseline",
]
