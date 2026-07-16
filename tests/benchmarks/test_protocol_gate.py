from pathlib import Path

from benchmarks.core import BenchmarkCase
from benchmarks.protocol_gate import (
    ProtocolViolationError,
    apply_case_list,
    dataset_files,
    load_case_list,
    load_checksums,
    sha256_file,
    verify_dataset_checksums,
)
from benchmarks.scripts.freeze_protocol import write_case_list, write_checksums
import pytest


def _case(task_id: str) -> BenchmarkCase:
    return BenchmarkCase(task_id=task_id, inputs={}, reference="x")


def _write_dataset(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "a.json").write_text('{"a": 1}', encoding="utf-8")
    nested = data_dir / "nested"
    nested.mkdir()
    (nested / "b.json").write_text('{"b": 2}', encoding="utf-8")
    return data_dir


def _write_ledger(tmp_path: Path, data_dir: Path) -> Path:
    ledger = tmp_path / "checksums.txt"
    lines = [
        f"{sha256_file(path)}  {path.relative_to(data_dir).as_posix()}"
        for path in dataset_files(data_dir)
    ]
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ledger


@pytest.mark.unit
def test_verify_passes_on_matching_checksums(tmp_path: Path) -> None:
    data_dir = _write_dataset(tmp_path)
    ledger = _write_ledger(tmp_path, data_dir)

    verified = verify_dataset_checksums(
        data_dir, data_dir=data_dir, checksums_path=ledger
    )

    assert verified == ["a.json", "nested/b.json"]


@pytest.mark.unit
def test_verify_passes_on_single_file_dataset(tmp_path: Path) -> None:
    data_dir = _write_dataset(tmp_path)
    ledger = _write_ledger(tmp_path, data_dir)

    verified = verify_dataset_checksums(
        data_dir / "a.json", data_dir=data_dir, checksums_path=ledger
    )

    assert verified == ["a.json"]


@pytest.mark.unit
def test_verify_refuses_on_mismatch(tmp_path: Path) -> None:
    data_dir = _write_dataset(tmp_path)
    ledger = _write_ledger(tmp_path, data_dir)
    (data_dir / "a.json").write_text('{"a": "tampered"}', encoding="utf-8")

    with pytest.raises(ProtocolViolationError, match="checksum mismatch"):
        verify_dataset_checksums(data_dir, data_dir=data_dir, checksums_path=ledger)


@pytest.mark.unit
def test_verify_refuses_on_unlisted_file(tmp_path: Path) -> None:
    data_dir = _write_dataset(tmp_path)
    ledger = _write_ledger(tmp_path, data_dir)
    (data_dir / "new.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProtocolViolationError, match="not in the checksum ledger"):
        verify_dataset_checksums(data_dir, data_dir=data_dir, checksums_path=ledger)


@pytest.mark.unit
def test_verify_refuses_when_ledger_missing(tmp_path: Path) -> None:
    data_dir = _write_dataset(tmp_path)

    with pytest.raises(ProtocolViolationError, match="ledger not found"):
        verify_dataset_checksums(
            data_dir,
            data_dir=data_dir,
            checksums_path=tmp_path / "absent.txt",
        )


@pytest.mark.unit
def test_load_checksums_rejects_malformed_line(tmp_path: Path) -> None:
    ledger = tmp_path / "checksums.txt"
    ledger.write_text("not-a-hash data/a.json\n", encoding="utf-8")

    with pytest.raises(ProtocolViolationError, match="malformed"):
        load_checksums(ledger)


@pytest.mark.unit
def test_case_list_roundtrip_skips_comments(tmp_path: Path) -> None:
    listing = tmp_path / "locomo.txt"
    listing.write_text(
        "# benchmark: locomo\ncase-1\n\ncase-2\n# trailing comment\ncase-3\n",
        encoding="utf-8",
    )

    assert load_case_list(listing) == ["case-1", "case-2", "case-3"]


@pytest.mark.unit
def test_case_list_rejects_duplicates(tmp_path: Path) -> None:
    listing = tmp_path / "dup.txt"
    listing.write_text("case-1\ncase-1\n", encoding="utf-8")

    with pytest.raises(ProtocolViolationError, match="duplicate"):
        load_case_list(listing)


@pytest.mark.unit
def test_apply_case_list_preserves_dataset_order() -> None:
    cases = [_case("c"), _case("a"), _case("b"), _case("d")]

    selected = apply_case_list(cases, ["a", "c"])

    assert [case.task_id for case in selected] == ["c", "a"]


@pytest.mark.unit
def test_apply_case_list_refuses_missing_ids() -> None:
    cases = [_case("a")]

    with pytest.raises(ProtocolViolationError, match="missing from the loaded"):
        apply_case_list(cases, ["a", "ghost"])


@pytest.mark.unit
def test_write_checksums_output_is_verifiable(tmp_path: Path) -> None:
    data_dir = _write_dataset(tmp_path)
    ledger = tmp_path / "protocol" / "checksums.txt"

    count = write_checksums(data_dir, ledger)

    assert count == 2
    verified = verify_dataset_checksums(
        data_dir, data_dir=data_dir, checksums_path=ledger
    )
    assert len(verified) == 2


@pytest.mark.unit
def test_write_case_list_is_deterministic_and_seeded(tmp_path: Path, monkeypatch) -> None:
    fixture = Path("benchmarks/locomo/fixtures/sample.json")
    from benchmarks.locomo import load_locomo, locomo_qa_scorer

    monkeypatch.setattr(
        "benchmarks.scripts.freeze_protocol._benchmark_config",
        lambda name, data_dir: (fixture, load_locomo, locomo_qa_scorer),
    )

    first = write_case_list("locomo", tmp_path, tmp_path / "lists-a")
    second = write_case_list("locomo", tmp_path, tmp_path / "lists-b")

    ids_first = load_case_list(first)
    ids_second = load_case_list(second)
    assert ids_first == ids_second
    assert first.read_text(encoding="utf-8").splitlines()[2] == "# sample_seed: 20260710"
    # The frozen IDs must round-trip through the loader used by live runs.
    selected = apply_case_list(load_locomo(fixture), ids_first)
    assert [case.task_id for case in selected] == ids_first
