"""Freeze the protocol artifacts required by ``benchmarks/PROTOCOL.md``.

Writes:

- ``benchmarks/protocol/checksums.txt``: SHA-256 of every file under
  ``benchmarks/data/`` in ``sha256sum`` format.
- ``benchmarks/protocol/case_lists/<benchmark>.txt``: the frozen case-ID
  lists produced by the harness's hash sampling with
  ``sample_seed = 20260710`` and a 200-case target (full split when the
  dataset is smaller), matching PROTOCOL.md section 1.3 exactly.

Re-running after datasets change requires a dated PROTOCOL.md amendment.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.protocol_gate import (
    CASE_LISTS_DIR,
    CHECKSUMS_FILE,
    PROTOCOL_MAX_CASES,
    PROTOCOL_SAMPLE_MODE,
    PROTOCOL_SAMPLE_SEED,
    dataset_files,
    sha256_file,
)
from benchmarks.scripts.run_full_memory_benchmark import (
    DEFAULT_DATA_DIR,
    _benchmark_config,
    sample_cases,
)

FROZEN_BENCHMARKS = (
    "locomo",
    "longmemeval-s",
    "longmemeval-v2-small",
    "longmemeval-v2-medium",
)


def write_checksums(data_dir: Path, output: Path) -> int:
    lines = []
    for file_path in dataset_files(data_dir):
        relative = file_path.relative_to(data_dir).as_posix()
        lines.append(f"{sha256_file(file_path)}  {relative}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def write_case_list(benchmark: str, data_dir: Path, output_dir: Path) -> Path:
    dataset_path, loader, _scorer = _benchmark_config(benchmark, data_dir)
    if not dataset_path.exists():
        raise FileNotFoundError(f"{benchmark}: dataset missing at {dataset_path}")
    cases = loader(dataset_path)
    sampled, info = sample_cases(
        cases,
        sample_mode=PROTOCOL_SAMPLE_MODE,
        sample_seed=PROTOCOL_SAMPLE_SEED,
        max_cases=PROTOCOL_MAX_CASES,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{benchmark}.txt"
    header = [
        f"# benchmark: {benchmark}",
        f"# sample_mode: {info['sample_mode']}",
        f"# sample_seed: {info['sample_seed']}",
        f"# sample_size: {info['sample_size']} of {info['source_cases']}",
        "# frozen per benchmarks/PROTOCOL.md section 1.3 -- do not regenerate",
        "# without a dated amendment.",
    ]
    body = [case.task_id for case in sampled]
    output.write_text("\n".join(header + body) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--checksums-output", type=Path, default=CHECKSUMS_FILE)
    parser.add_argument("--case-lists-dir", type=Path, default=CASE_LISTS_DIR)
    parser.add_argument(
        "--benchmarks",
        nargs="+",
        default=list(FROZEN_BENCHMARKS),
        help="Benchmark names understood by run_full_memory_benchmark.py.",
    )
    args = parser.parse_args(argv)

    count = write_checksums(args.data_dir, args.checksums_output)
    print(f"checksums: {count} files -> {args.checksums_output}")

    for benchmark in args.benchmarks:
        output = write_case_list(benchmark, args.data_dir, args.case_lists_dir)
        ids = [
            line
            for line in output.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        ]
        print(f"case list: {benchmark}: {len(ids)} cases -> {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
