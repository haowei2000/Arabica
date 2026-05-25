"""Reference baseline catalog and reported LoCoMo results from LightMem.

The values in this module are external reference numbers, not Structure
measurements. They seed the paper's baseline table and give the benchmark
harness a stable, testable artifact that mirrors the LightMem/MemBase
baseline family.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

LIGHTMEM_README_URL = "https://github.com/zjunlp/LightMem#experimental-results"
MEMBASE_README_URL = "https://github.com/zjunlp/MemBase"


@dataclass(frozen=True)
class MemoryBaselineSpec:
    """A baseline family aligned with the LightMem/MemBase suite."""

    name: str
    category: str
    memory_construction: str
    retrieval: str
    status: str
    source_url: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class LightMemLoCoMoOverview:
    """One overview row from the LightMem LoCoMo baseline table."""

    method: str
    backbone: str
    accuracy_gpt4o_judge: float
    accuracy_qwen_judge: float
    memory_construction_tokens_k: float | None
    qa_tokens_k: float
    total_tokens_k: float
    calls: int | None
    runtime_seconds: int
    source_url: str = LIGHTMEM_README_URL

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


MEMORY_BASELINE_CATALOG: tuple[MemoryBaselineSpec, ...] = (
    MemoryBaselineSpec(
        name="FullText",
        category="long-context",
        memory_construction="none",
        retrieval="append all available history to the prompt",
        status="reported-by-lightmem",
        source_url=LIGHTMEM_README_URL,
    ),
    MemoryBaselineSpec(
        name="NaiveRAG",
        category="retrieval",
        memory_construction="none",
        retrieval="retrieve top-k chunks with a vector index",
        status="reported-by-lightmem",
        source_url=LIGHTMEM_README_URL,
    ),
    MemoryBaselineSpec(
        name="A-MEM",
        category="agentic-memory",
        memory_construction="online memory extraction and update",
        retrieval="agentic memory search",
        status="reported-by-lightmem",
        source_url=LIGHTMEM_README_URL,
    ),
    MemoryBaselineSpec(
        name="MemoryOS",
        category="operating-system memory",
        memory_construction="summarise and update memory pages",
        retrieval="memory-page retrieval",
        status="reported-by-lightmem",
        source_url=LIGHTMEM_README_URL,
    ),
    MemoryBaselineSpec(
        name="Mem0",
        category="production memory layer",
        memory_construction="memory extraction with optional graph store",
        retrieval="memory search API",
        status="reported-by-lightmem",
        source_url=LIGHTMEM_README_URL,
    ),
    MemoryBaselineSpec(
        name="LangMem",
        category="framework memory layer",
        memory_construction="LangGraph/LangChain memory store",
        retrieval="memory search API",
        status="available-in-membase",
        source_url=MEMBASE_README_URL,
    ),
    MemoryBaselineSpec(
        name="EverMemOS",
        category="operating-system memory",
        memory_construction="persistent memory operating layer",
        retrieval="memory search API",
        status="available-in-membase",
        source_url=MEMBASE_README_URL,
    ),
    MemoryBaselineSpec(
        name="HippoRAG2",
        category="graph retrieval",
        memory_construction="graph-based retrieval index",
        retrieval="graph-aware retrieval",
        status="available-in-membase",
        source_url=MEMBASE_README_URL,
    ),
    MemoryBaselineSpec(
        name="Long-Context",
        category="long-context",
        memory_construction="none",
        retrieval="append context directly to the prompt",
        status="available-in-membase",
        source_url=MEMBASE_README_URL,
    ),
)


LIGHTMEM_LOCOMO_OVERVIEW: tuple[LightMemLoCoMoOverview, ...] = (
    LightMemLoCoMoOverview(
        method="FullText",
        backbone="gpt-4o-mini",
        accuracy_gpt4o_judge=73.83,
        accuracy_qwen_judge=73.18,
        memory_construction_tokens_k=None,
        qa_tokens_k=54884.479,
        total_tokens_k=54884.479,
        calls=None,
        runtime_seconds=6971,
    ),
    LightMemLoCoMoOverview(
        method="NaiveRAG",
        backbone="gpt-4o-mini",
        accuracy_gpt4o_judge=63.64,
        accuracy_qwen_judge=63.12,
        memory_construction_tokens_k=None,
        qa_tokens_k=3870.187,
        total_tokens_k=3870.187,
        calls=None,
        runtime_seconds=1884,
    ),
    LightMemLoCoMoOverview(
        method="A-MEM",
        backbone="gpt-4o-mini",
        accuracy_gpt4o_judge=64.16,
        accuracy_qwen_judge=60.71,
        memory_construction_tokens_k=11494.344,
        qa_tokens_k=10170.567,
        total_tokens_k=21664.907,
        calls=11754,
        runtime_seconds=67084,
    ),
    LightMemLoCoMoOverview(
        method="MemoryOS(eval)",
        backbone="gpt-4o-mini",
        accuracy_gpt4o_judge=58.25,
        accuracy_qwen_judge=61.04,
        memory_construction_tokens_k=2870.036,
        qa_tokens_k=7649.343,
        total_tokens_k=10519.379,
        calls=5534,
        runtime_seconds=26129,
    ),
    LightMemLoCoMoOverview(
        method="MemoryOS(pypi)",
        backbone="gpt-4o-mini",
        accuracy_gpt4o_judge=54.87,
        accuracy_qwen_judge=55.91,
        memory_construction_tokens_k=5264.801,
        qa_tokens_k=6126.111,
        total_tokens_k=11390.004,
        calls=10160,
        runtime_seconds=37912,
    ),
    LightMemLoCoMoOverview(
        method="Mem0",
        backbone="gpt-4o-mini",
        accuracy_gpt4o_judge=36.49,
        accuracy_qwen_judge=37.01,
        memory_construction_tokens_k=24304.872,
        qa_tokens_k=1488.618,
        total_tokens_k=25793.490,
        calls=19070,
        runtime_seconds=120175,
    ),
    LightMemLoCoMoOverview(
        method="Mem0(api)",
        backbone="gpt-4o-mini",
        accuracy_gpt4o_judge=61.69,
        accuracy_qwen_judge=61.69,
        memory_construction_tokens_k=68347.720,
        qa_tokens_k=4169.909,
        total_tokens_k=72517.629,
        calls=6022,
        runtime_seconds=10445,
    ),
    LightMemLoCoMoOverview(
        method="Mem0-g(api)",
        backbone="gpt-4o-mini",
        accuracy_gpt4o_judge=60.32,
        accuracy_qwen_judge=59.48,
        memory_construction_tokens_k=69684.818,
        qa_tokens_k=4389.147,
        total_tokens_k=74073.965,
        calls=6022,
        runtime_seconds=10926,
    ),
    LightMemLoCoMoOverview(
        method="FullText",
        backbone="qwen3-30b-a3b-instruct-2507",
        accuracy_gpt4o_judge=74.87,
        accuracy_qwen_judge=74.35,
        memory_construction_tokens_k=None,
        qa_tokens_k=60873.076,
        total_tokens_k=60873.076,
        calls=None,
        runtime_seconds=10555,
    ),
    LightMemLoCoMoOverview(
        method="NaiveRAG",
        backbone="qwen3-30b-a3b-instruct-2507",
        accuracy_gpt4o_judge=66.95,
        accuracy_qwen_judge=64.68,
        memory_construction_tokens_k=None,
        qa_tokens_k=4271.052,
        total_tokens_k=4271.052,
        calls=None,
        runtime_seconds=1252,
    ),
    LightMemLoCoMoOverview(
        method="A-MEM",
        backbone="qwen3-30b-a3b-instruct-2507",
        accuracy_gpt4o_judge=56.10,
        accuracy_qwen_judge=54.81,
        memory_construction_tokens_k=16267.997,
        qa_tokens_k=17340.881,
        total_tokens_k=33608.878,
        calls=11754,
        runtime_seconds=69339,
    ),
    LightMemLoCoMoOverview(
        method="MemoryOS(eval)",
        backbone="qwen3-30b-a3b-instruct-2507",
        accuracy_gpt4o_judge=61.04,
        accuracy_qwen_judge=59.81,
        memory_construction_tokens_k=3615.087,
        qa_tokens_k=9703.169,
        total_tokens_k=11946.442,
        calls=4147,
        runtime_seconds=13710,
    ),
    LightMemLoCoMoOverview(
        method="MemoryOS(pypi)",
        backbone="qwen3-30b-a3b-instruct-2507",
        accuracy_gpt4o_judge=51.30,
        accuracy_qwen_judge=51.95,
        memory_construction_tokens_k=6663.527,
        qa_tokens_k=7764.991,
        total_tokens_k=14428.518,
        calls=10046,
        runtime_seconds=20830,
    ),
    LightMemLoCoMoOverview(
        method="Mem0",
        backbone="qwen3-30b-a3b-instruct-2507",
        accuracy_gpt4o_judge=43.31,
        accuracy_qwen_judge=43.25,
        memory_construction_tokens_k=17994.035,
        qa_tokens_k=1765.570,
        total_tokens_k=19759.605,
        calls=16145,
        runtime_seconds=46500,
    ),
)


def iter_baseline_catalog(
    *,
    status: str | None = None,
) -> Iterable[MemoryBaselineSpec]:
    """Yield baseline specs, optionally filtered by implementation status."""
    for spec in MEMORY_BASELINE_CATALOG:
        if status is None or spec.status == status:
            yield spec


def iter_locomo_overview(
    *,
    backbone: str | None = None,
) -> Iterable[LightMemLoCoMoOverview]:
    """Yield LightMem LoCoMo overview rows, optionally filtered by backbone."""
    for row in LIGHTMEM_LOCOMO_OVERVIEW:
        if backbone is None or row.backbone == backbone:
            yield row


def locomo_overview_dicts(
    *,
    backbone: str | None = None,
) -> list[dict[str, object]]:
    """Return overview rows as JSON-serialisable dictionaries."""
    return [row.to_dict() for row in iter_locomo_overview(backbone=backbone)]
