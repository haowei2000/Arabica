"""Tests for the LightMem/MemBase reference baseline artifact."""

import json
from pathlib import Path
import subprocess
import sys

from benchmarks.baselines import (
    MEMORY_BASELINE_CATALOG,
    iter_locomo_overview,
)
from benchmarks.scripts.lightmem_baseline_report import render_latex
import pytest

REPO_ROOT = Path(__file__).parents[2]
PAPER_TABLE = REPO_ROOT / "paper" / "tables" / "lightmem_locomo_baselines.tex"


@pytest.mark.unit
def test_lightmem_catalog_tracks_reported_and_membase_baselines():
    names = {spec.name for spec in MEMORY_BASELINE_CATALOG}
    assert {
        "FullText",
        "NaiveRAG",
        "A-MEM",
        "MemoryOS",
        "Mem0",
        "LangMem",
        "EverMemOS",
    }.issubset(names)

    reported = {
        spec.name
        for spec in MEMORY_BASELINE_CATALOG
        if spec.status == "reported-by-lightmem"
    }
    assert {"FullText", "NaiveRAG", "A-MEM", "MemoryOS", "Mem0"}.issubset(reported)


@pytest.mark.unit
def test_lightmem_locomo_overview_contains_expected_reference_rows():
    rows = list(iter_locomo_overview(backbone="gpt-4o-mini"))
    by_method = {row.method: row for row in rows}

    assert len(rows) == 8
    assert by_method["FullText"].accuracy_gpt4o_judge == pytest.approx(73.83)
    assert by_method["NaiveRAG"].total_tokens_k == pytest.approx(3870.187)
    assert by_method["Mem0(api)"].calls == 6022
    assert by_method["Mem0-g(api)"].runtime_seconds == 10926


@pytest.mark.unit
def test_lightmem_report_script_outputs_machine_readable_json():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.scripts.lightmem_baseline_report",
            "--format",
            "json",
            "--backbone",
            "gpt-4o-mini",
        ],
        check=True,
        capture_output=True,
        cwd=REPO_ROOT,
        text=True,
    )
    rows = json.loads(completed.stdout)
    assert rows[0]["method"] == "FullText"
    assert rows[0]["source_url"].startswith("https://github.com/zjunlp/LightMem")


@pytest.mark.unit
def test_paper_lightmem_table_is_generated_from_code_data():
    expected = render_latex(iter_locomo_overview(backbone="gpt-4o-mini"))
    assert PAPER_TABLE.read_text(encoding="utf-8") == expected


@pytest.mark.unit
def test_report_script_can_check_paper_table():
    subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.scripts.lightmem_baseline_report",
            "--check-paper-table",
            str(PAPER_TABLE),
        ],
        check=True,
        cwd=REPO_ROOT,
    )
