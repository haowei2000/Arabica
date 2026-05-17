"""Download public benchmark datasets used by full memory evaluation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlretrieve

from benchmarks.longmemeval_v2 import build_trajectory_offset_index

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "benchmarks" / "data"


@dataclass(frozen=True)
class DatasetFile:
    url: str
    path: Path
    large: bool = False


def _dataset_files(root: Path, *, include_screenshots: bool) -> list[DatasetFile]:
    files = [
        DatasetFile(
            "https://raw.githubusercontent.com/snap-research/LoCoMo/main/data/locomo10.json",
            root / "locomo" / "locomo10.json",
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json",
            root / "longmemeval" / "longmemeval_s_cleaned.json",
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_m_cleaned.json",
            root / "longmemeval" / "longmemeval_m_cleaned.json",
            large=True,
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json",
            root / "longmemeval" / "longmemeval_oracle.json",
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main/questions.jsonl",
            root / "longmemeval_v2" / "questions.jsonl",
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main/haystacks/lme_v2_small.json",
            root / "longmemeval_v2" / "haystacks" / "lme_v2_small.json",
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main/haystacks/lme_v2_medium.json",
            root / "longmemeval_v2" / "haystacks" / "lme_v2_medium.json",
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main/trajectories.jsonl",
            root / "longmemeval_v2" / "trajectories.jsonl",
            large=True,
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main/SCHEMA.md",
            root / "longmemeval_v2" / "SCHEMA.md",
        ),
        DatasetFile(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main/DATA_CARD.md",
            root / "longmemeval_v2" / "DATA_CARD.md",
        ),
    ]
    if include_screenshots:
        files.extend(
            [
                DatasetFile(
                    "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main/trajectory_screenshots/web_screenshots.tar.gz",
                    root
                    / "longmemeval_v2"
                    / "trajectory_screenshots"
                    / "web_screenshots.tar.gz",
                    large=True,
                ),
                DatasetFile(
                    "https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/resolve/main/trajectory_screenshots/enterprise_screenshots_base.tar.gz",
                    root
                    / "longmemeval_v2"
                    / "trajectory_screenshots"
                    / "enterprise_screenshots_base.tar.gz",
                    large=True,
                ),
            ]
        )
    return files


def download_file(item: DatasetFile, *, force: bool) -> bool:
    item.path.parent.mkdir(parents=True, exist_ok=True)
    if item.path.exists() and not force:
        return False
    urlretrieve(item.url, item.path)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download full public benchmark datasets for memory evaluation.",
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--skip-large",
        action="store_true",
        help="Skip LongMemEval-M and LongMemEval-V2 trajectories.",
    )
    parser.add_argument(
        "--include-screenshots",
        action="store_true",
        help="Also download LongMemEval-V2 screenshot tarballs.",
    )
    parser.add_argument(
        "--build-lme-v2-index",
        action="store_true",
        help="Build trajectories.jsonl byte-offset index after download.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files = _dataset_files(args.data_dir, include_screenshots=args.include_screenshots)
    downloaded = 0
    skipped = 0
    for item in files:
        if args.skip_large and item.large:
            skipped += 1
            print(f"skip-large {item.path}")
            continue
        changed = download_file(item, force=args.force)
        if changed:
            downloaded += 1
            print(f"downloaded {item.path}")
        else:
            skipped += 1
            print(f"exists {item.path}")

    trajectories = args.data_dir / "longmemeval_v2" / "trajectories.jsonl"
    if args.build_lme_v2_index and trajectories.exists():
        index = build_trajectory_offset_index(trajectories)
        print(f"indexed {index}")

    print(f"done downloaded={downloaded} skipped={skipped}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
