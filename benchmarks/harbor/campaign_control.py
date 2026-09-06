"""Immutable campaign freeze, ordered ledger, resume, and EUR budget gates.

This module never reads credential values. It records only whether the frozen
environment names are present and injects no secrets into campaign artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CAMPAIGN_SCHEMA = "structure.context-agent-campaign/2026-09-v1"
DEFAULT_BUDGET_CENTS = 25_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def git_output(repository: Path, *arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def dirty_digest(repository: Path) -> str | None:
    status = git_output(repository, "status", "--porcelain=v1", "--untracked-files=all")
    if status is None or not status:
        return None
    diff = subprocess.run(
        ["git", "-C", str(repository), "diff", "--binary", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout
    digest = hashlib.sha256(status.encode())
    digest.update(diff)
    return f"sha256:{digest.hexdigest()}"


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def create_campaign(
    root: Path,
    repository: Path,
    manifests: Iterable[Path],
    binaries: Iterable[Path],
    credential_env_names: Iterable[str],
    budget_cents: int = DEFAULT_BUDGET_CENTS,
) -> dict[str, Any]:
    if budget_cents <= 0:
        raise ValueError("budget must be positive")
    root.mkdir(parents=True, exist_ok=False)
    manifest_records = []
    binary_records = []
    for source in manifests:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = root / "manifests" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        manifest_records.append(
            {"path": str(destination.relative_to(root)), "sha256": sha256_file(destination)}
        )
    for binary in binaries:
        binary = binary.resolve()
        if not binary.is_file():
            raise FileNotFoundError(binary)
        binary_records.append(
            {"path": str(binary), "sha256": sha256_file(binary)}
        )
    freeze = {
        "schema_version": CAMPAIGN_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": str(repository.resolve()),
        "git_revision": git_output(repository, "rev-parse", "HEAD"),
        "dirty_digest": dirty_digest(repository),
        "manifests": manifest_records,
        "binaries": binary_records,
        "credential_environment": {
            name: os.environ.get(name) is not None for name in credential_env_names
        },
        "budget_eur_cents": budget_cents,
    }
    atomic_json(root / "freeze.json", freeze)
    atomic_json(
        root / "budget.json",
        {
            "schema_version": CAMPAIGN_SCHEMA,
            "budget_eur_cents": budget_cents,
            "spent_eur_cents": 0,
            "stages": {},
            "campaign_status": "frozen",
        },
    )
    (root / "ordered-ledger.jsonl").touch(exist_ok=False)
    return freeze


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


def authorize_stage(root: Path, stage: str, worst_cost_cents: int) -> dict[str, Any]:
    if not stage or worst_cost_cents < 0:
        raise ValueError("stage and non-negative worst cost are required")
    state_path = root / "budget.json"
    state = read_json(state_path)
    remaining = int(state["budget_eur_cents"]) - int(state["spent_eur_cents"])
    status = "authorized" if worst_cost_cents <= remaining else "budget_stopped"
    state["stages"][stage] = {
        "worst_cost_eur_cents": worst_cost_cents,
        "remaining_before_eur_cents": remaining,
        "status": status,
    }
    if status == "budget_stopped":
        state["campaign_status"] = "budget_stopped"
    atomic_json(state_path, state)
    return state["stages"][stage]


def append_trial(root: Path, entry: dict[str, Any]) -> None:
    ledger = root / "ordered-ledger.jsonl"
    existing = load_ledger(root)
    expected_sequence = len(existing) + 1
    if int(entry.get("sequence", 0)) != expected_sequence:
        raise ValueError(
            f"ledger sequence must be {expected_sequence}, got {entry.get('sequence')}"
        )
    if any(item.get("trial_id") == entry.get("trial_id") for item in existing):
        raise ValueError(f"duplicate trial id {entry.get('trial_id')}")
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(canonical + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_ledger(root: Path) -> list[dict[str, Any]]:
    ledger = root / "ordered-ledger.jsonl"
    if not ledger.is_file():
        raise FileNotFoundError(ledger)
    entries = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    for sequence, entry in enumerate(entries, start=1):
        if entry.get("sequence") != sequence:
            raise ValueError("ordered ledger has a sequence gap or reorder")
    return entries


def resume_sequence(root: Path) -> int:
    return len(load_ledger(root)) + 1


def record_spend(root: Path, stage: str, amount_cents: int) -> dict[str, Any]:
    if amount_cents < 0:
        raise ValueError("spend must be non-negative")
    path = root / "budget.json"
    state = read_json(path)
    stage_state = state["stages"].get(stage)
    if not stage_state or stage_state.get("status") != "authorized":
        raise ValueError(f"stage {stage} is not authorized")
    new_spend = int(state["spent_eur_cents"]) + amount_cents
    if new_spend > int(state["budget_eur_cents"]):
        state["campaign_status"] = "budget_stopped"
        atomic_json(path, state)
        raise ValueError("recorded spend would exceed the campaign hard cap")
    state["spent_eur_cents"] = new_spend
    stage_state["recorded_spend_eur_cents"] = int(
        stage_state.get("recorded_spend_eur_cents", 0)
    ) + amount_cents
    atomic_json(path, state)
    return state


def stop_stage(
    root: Path, stage: str, reason: str, status: str = "mechanism_stopped"
) -> dict[str, Any]:
    if not stage or not reason.strip():
        raise ValueError("stage and non-empty stop reason are required")
    if status not in {"mechanism_stopped", "infrastructure_stopped"}:
        raise ValueError("stop status must be mechanism_stopped or infrastructure_stopped")
    path = root / "budget.json"
    state = read_json(path)
    stage_state = state["stages"].get(stage)
    if stage_state is None:
        raise ValueError(f"stage {stage} is not registered")
    stage_state["status"] = status
    stage_state["stop_reason"] = reason.strip()
    stage_state["completed_trials"] = len(load_ledger(root))
    state["campaign_status"] = status
    atomic_json(path, state)
    return stage_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--root", type=Path, required=True)
    freeze.add_argument("--repository", type=Path, required=True)
    freeze.add_argument("--manifest", type=Path, action="append", default=[])
    freeze.add_argument("--binary", type=Path, action="append", default=[])
    freeze.add_argument("--credential-env", action="append", default=[])
    freeze.add_argument("--budget-eur-cents", type=int, default=DEFAULT_BUDGET_CENTS)
    authorize = subparsers.add_parser("authorize-stage")
    authorize.add_argument("--root", type=Path, required=True)
    authorize.add_argument("--stage", required=True)
    authorize.add_argument("--worst-cost-eur-cents", type=int, required=True)
    resume = subparsers.add_parser("resume")
    resume.add_argument("--root", type=Path, required=True)
    stop = subparsers.add_parser("stop-stage")
    stop.add_argument("--root", type=Path, required=True)
    stop.add_argument("--stage", required=True)
    stop.add_argument("--reason", required=True)
    stop.add_argument(
        "--status",
        choices=["mechanism_stopped", "infrastructure_stopped"],
        default="mechanism_stopped",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "freeze":
        result = create_campaign(
            args.root,
            args.repository,
            args.manifest,
            args.binary,
            args.credential_env,
            args.budget_eur_cents,
        )
    elif args.command == "authorize-stage":
        result = authorize_stage(args.root, args.stage, args.worst_cost_eur_cents)
    elif args.command == "stop-stage":
        result = stop_stage(args.root, args.stage, args.reason, args.status)
    else:
        result = {"next_sequence": resume_sequence(args.root)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
