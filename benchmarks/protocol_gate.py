"""Protocol integrity gate mandated by ``benchmarks/PROTOCOL.md``.

Two artifacts freeze the experimental surface before any live run:

- ``benchmarks/protocol/checksums.txt`` — SHA-256 of every dataset file,
  in ``sha256sum`` format (``<hex>  <path relative to the data dir>``).
  Keying by data-dir-relative path keeps the ledger valid wherever the
  gitignored datasets physically live (main checkout, worktree, CI).
- ``benchmarks/protocol/case_lists/<benchmark>.txt`` — the frozen case-ID
  list produced by hash sampling with ``sample_seed = 20260710``.

The runner refuses to execute when a dataset file is missing from the
ledger or its checksum differs (PROTOCOL.md §1.2). This module has no
dependency on ``src/structure/`` or on ``benchmarks/core/``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_DIR = Path(__file__).resolve().parent / "protocol"
CHECKSUMS_FILE = PROTOCOL_DIR / "checksums.txt"
CASE_LISTS_DIR = PROTOCOL_DIR / "case_lists"

PROTOCOL_SAMPLE_SEED = "20260710"
PROTOCOL_SAMPLE_MODE = "hash"
PROTOCOL_MAX_CASES = 200

_IGNORED_FILE_NAMES = {".DS_Store", ".gitkeep"}


class ProtocolViolationError(RuntimeError):
    """A live-run precondition from PROTOCOL.md is not satisfied."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_files(dataset_path: Path) -> list[Path]:
    """All files a benchmark reads: the file itself, or a directory tree."""
    if dataset_path.is_file():
        return [dataset_path]
    if dataset_path.is_dir():
        return sorted(
            path
            for path in dataset_path.rglob("*")
            if path.is_file()
            and path.name not in _IGNORED_FILE_NAMES
            and "__pycache__" not in path.parts
        )
    raise ProtocolViolationError(f"dataset path does not exist: {dataset_path}")


def load_checksums(checksums_path: Path = CHECKSUMS_FILE) -> dict[str, str]:
    """Parse the ledger into ``{relative_path: sha256_hex}``."""
    if not checksums_path.exists():
        raise ProtocolViolationError(
            f"checksum ledger not found: {checksums_path}. "
            "Run benchmarks/scripts/freeze_protocol.py before any live run "
            "(PROTOCOL.md section 1.2)."
        )
    ledger: dict[str, str] = {}
    for line_number, line in enumerate(
        checksums_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ProtocolViolationError(
                f"{checksums_path}:{line_number}: malformed ledger line: {line!r}"
            )
        ledger[parts[1].strip()] = parts[0]
    return ledger


def verify_dataset_checksums(
    dataset_path: Path,
    *,
    data_dir: Path,
    checksums_path: Path = CHECKSUMS_FILE,
) -> list[str]:
    """Verify every file the benchmark reads against the frozen ledger.

    Ledger keys are paths relative to ``data_dir``. Returns the verified
    relative paths; raises ``ProtocolViolationError`` on a missing ledger,
    a file absent from the ledger, or a hash mismatch.
    """
    ledger = load_checksums(checksums_path)
    verified: list[str] = []
    for file_path in dataset_files(dataset_path):
        relative = file_path.relative_to(data_dir).as_posix()
        expected = ledger.get(relative)
        if expected is None:
            raise ProtocolViolationError(
                f"{relative} is not in the checksum ledger {checksums_path}. "
                "Re-run freeze_protocol.py only if this dataset change is "
                "recorded as a PROTOCOL.md amendment."
            )
        actual = sha256_file(file_path)
        if actual != expected:
            raise ProtocolViolationError(
                f"checksum mismatch for {relative}: ledger has {expected}, "
                f"file hashes to {actual}. Refusing to run (PROTOCOL.md 1.2)."
            )
        verified.append(relative)
    return verified


def load_case_list(path: Path) -> list[str]:
    """Read a frozen case list: one task ID per line, ``#`` comments allowed."""
    if not path.exists():
        raise ProtocolViolationError(f"case list not found: {path}")
    ids = [
        stripped
        for line in path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]
    if not ids:
        raise ProtocolViolationError(f"case list is empty: {path}")
    duplicates = {task_id for task_id in ids if ids.count(task_id) > 1}
    if duplicates:
        raise ProtocolViolationError(
            f"case list {path} contains duplicate IDs: {sorted(duplicates)[:5]}"
        )
    return ids


def case_list_sha256(path: Path) -> str:
    return sha256_file(path)


def apply_case_list(cases: list, task_ids: list[str]) -> list:
    """Restrict loaded cases to exactly the frozen IDs, keeping dataset order.

    Raises if any frozen ID is absent from the dataset — a silent subset
    would invalidate the pre-registered sample.
    """
    wanted = set(task_ids)
    selected = [case for case in cases if case.task_id in wanted]
    found = {case.task_id for case in selected}
    missing = sorted(wanted - found)
    if missing:
        raise ProtocolViolationError(
            f"{len(missing)} frozen case IDs are missing from the loaded "
            f"dataset (first few: {missing[:5]}). Dataset and case list are "
            "out of sync."
        )
    return selected
