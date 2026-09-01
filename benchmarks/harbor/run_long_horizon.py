"""Execute a frozen Structure long-horizon manifest through Harbor.

This runner owns only orchestration and ledger extraction. Provider calls,
agent behavior, tools, and memory policy remain in the Rust Harbor agent.
The real provider credential stays in a host-side proxy. Harbor receives only
a non-secret local client token and the proxy URL.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--harbor-program", default="harbor")
    parser.add_argument("--dataset", default="terminal-bench@2.0")
    parser.add_argument("--provider-base-url", required=True)
    parser.add_argument(
        "--provider-preflight-base-url",
        help="host-reachable proxy URL; defaults to --provider-base-url",
    )
    parser.add_argument("--provider-client-token", required=True)
    parser.add_argument(
        "--provider-api-type", default="open_ai_chat_completions"
    )
    parser.add_argument("--start-sequence", type=int, default=1)
    parser.add_argument("--stop-sequence", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--max-consecutive-infrastructure-failures", type=int, default=1
    )
    return parser.parse_args()


def job_config(
    manifest: dict[str, Any],
    trial: dict[str, Any],
    jobs_root: Path,
    provider_base_url: str,
    provider_client_token: str,
    provider_api_type: str,
) -> dict[str, Any]:
    strategy = str(trial["arm"]).upper()
    return {
        "job_name": trial["trial_id"],
        "jobs_dir": str(jobs_root),
        "n_concurrent_trials": 1,
        "n_attempts": 1,
        "max_retries": 0,
        "agents": [
            {
                "name": "benchmarks.harbor.structure_agent:StructureAgent",
                "model_name": f"glm/{manifest['model']}",
                "override_timeout_sec": manifest["timeout_seconds"],
                "kwargs": {
                    "strategy": strategy,
                    "max_steps": manifest["max_model_steps"],
                    "max_tokens": manifest["max_output_tokens"],
                    "checkpoint_batches": manifest["checkpoint_batches"],
                    "pgc_effort": manifest["compaction_effort"],
                    "pgc_continuation_probability_bps": manifest[
                        "continuation_probability_bps"
                    ],
                    "pgc_cached_input_cost_bps": manifest[
                        "cached_input_cost_bps"
                    ],
                    "thinking": manifest["thinking_enabled"],
                    "provider_base_url": provider_base_url,
                    "provider_client_token": provider_client_token,
                    "provider_api_type": provider_api_type,
                },
            }
        ],
        "datasets": [
            {
                "name": dataset_name(manifest.get("dataset", "terminal-bench@2.0")),
                "version": dataset_version(
                    manifest.get("dataset", "terminal-bench@2.0")
                ),
                "task_names": [trial["task"]],
            }
        ],
    }


def dataset_name(value: str) -> str:
    return value.rsplit("@", maxsplit=1)[0]


def dataset_version(value: str) -> str:
    if "@" not in value:
        raise ValueError("dataset must use name@version")
    return value.rsplit("@", maxsplit=1)[1]


def trial_directory(job_root: Path) -> Path:
    candidates = sorted(
        path
        for path in job_root.iterdir()
        if path.is_dir() and (path / "result.json").is_file()
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one Harbor trial under {job_root}, found {len(candidates)}"
        )
    return candidates[0]


def length_truncated(report: dict[str, Any]) -> bool:
    for envelope in report.get("events", []):
        event = envelope.get("event", {})
        if event.get("type") != "run.failed":
            continue
        payload = event.get("payload", {})
        message = str(payload.get("message", event.get("message", ""))).lower()
        if "length" in message or "max model steps" in message:
            return True
    return False


def ledger_entry(trial: dict[str, Any], job_root: Path) -> dict[str, Any]:
    try:
        harbor_trial = trial_directory(job_root)
        harbor_result = json.loads((harbor_trial / "result.json").read_text())
        report_path = harbor_trial / "agent" / "structure-report.json"
        if not report_path.is_file():
            return {
                "trial_id": trial["trial_id"],
                "verifier_passed": False,
                "external_verifier_passed": False,
                "agent_terminal_success": False,
                "infrastructure_failure": True,
                "length_truncated": False,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "uncached_input_tokens": 0,
                "official_half_price_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "gc_quality_gate_passed": None,
            }
        report = json.loads(report_path.read_text())
        reward = (
            harbor_result.get("verifier_result", {})
            .get("rewards", {})
            .get("reward")
        )
        infrastructure_failure = harbor_result.get("exception_info") is not None
        external_verifier_passed = reward == 1.0 and not infrastructure_failure
        agent_terminal_success = bool(report.get("terminal_success"))
        verifier_passed = external_verifier_passed and agent_terminal_success
        input_tokens = int(report.get("input_tokens", 0))
        cached_input_tokens = min(
            input_tokens, int(report.get("cached_input_tokens", 0))
        )
        uncached_input_tokens = input_tokens - cached_input_tokens
        gate = report.get("gc_quality_gate") or {}
        gate_passed = (
            bool(gate.get("passed")) if str(trial["arm"]).upper() == "FBGC" else None
        )
        return {
            "trial_id": trial["trial_id"],
            "verifier_passed": verifier_passed,
            "external_verifier_passed": external_verifier_passed,
            "agent_terminal_success": agent_terminal_success,
            "infrastructure_failure": infrastructure_failure,
            "length_truncated": length_truncated(report),
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "uncached_input_tokens": uncached_input_tokens,
            "official_half_price_input_tokens": uncached_input_tokens
            + (cached_input_tokens + 1) // 2,
            "cache_creation_input_tokens": int(
                report.get("cache_creation_input_tokens", 0)
            ),
            "gc_quality_gate_passed": gate_passed,
        }
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {
            "trial_id": trial["trial_id"],
            "verifier_passed": False,
            "external_verifier_passed": False,
            "agent_terminal_success": False,
            "infrastructure_failure": True,
            "length_truncated": False,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "uncached_input_tokens": 0,
            "official_half_price_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "gc_quality_gate_passed": None,
        }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def provider_preflight(base_url: str, client_token: str) -> None:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/health",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"provider preflight failed: {error}") from error
    if response.status != 200 or payload.get("status") != "ready":
        raise RuntimeError(f"provider preflight returned an invalid response: {payload}")


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    output_root = args.output_root.resolve()
    binary = args.binary.resolve()
    manifest = json.loads(manifest_path.read_text())
    manifest["dataset"] = args.dataset
    if not binary.is_file():
        raise FileNotFoundError(f"Harbor agent binary not found: {binary}")
    if args.start_sequence < 1:
        raise ValueError("start sequence must be positive")
    if args.max_consecutive_infrastructure_failures < 1:
        raise ValueError("maximum consecutive infrastructure failures must be positive")
    if not args.dry_run:
        provider_preflight(
            args.provider_preflight_base_url or args.provider_base_url,
            args.provider_client_token,
        )

    jobs_root = output_root / "jobs"
    configs_root = output_root / "job-configs"
    ledger_path = output_root / "ledger.json"
    output_root.mkdir(parents=True, exist_ok=True)
    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{repository_root}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else str(repository_root)
    )
    environment["STRUCTURE_HARBOR_AGENT_BIN"] = str(binary)

    selected = []
    for sequence, trial in enumerate(manifest["trials"], start=1):
        if sequence < args.start_sequence:
            continue
        if args.stop_sequence is not None and sequence > args.stop_sequence:
            continue
        selected.append((sequence, trial))

    consecutive_infrastructure_failures = 0
    for sequence, trial in selected:
        config_path = configs_root / f"{sequence:03d}-{trial['trial_id']}.json"
        config = job_config(
            manifest,
            trial,
            jobs_root,
            args.provider_base_url,
            args.provider_client_token,
            args.provider_api_type,
        )
        write_json(config_path, config)
        if args.dry_run:
            print(json.dumps({"sequence": sequence, "trial_id": trial["trial_id"], "config": str(config_path)}))
            continue
        job_root = jobs_root / trial["trial_id"]
        if job_root.exists():
            entry = ledger_entry(trial, job_root)
        else:
            completed = subprocess.run(
                [args.harbor_program, "run", "--config", str(config_path), "--yes", "--quiet"],
                cwd=repository_root,
                env=environment,
                check=False,
            )
            entry = ledger_entry(trial, job_root)
            if completed.returncode != 0:
                entry["infrastructure_failure"] = True
                entry["verifier_passed"] = False
        write_json(output_root / trial["report_path"], entry)
        print(json.dumps({"sequence": sequence, **entry}), flush=True)

        if entry["infrastructure_failure"]:
            consecutive_infrastructure_failures += 1
        else:
            consecutive_infrastructure_failures = 0

        ledger = []
        for scheduled in manifest["trials"]:
            report_path = output_root / scheduled["report_path"]
            if report_path.is_file():
                ledger.append(json.loads(report_path.read_text()))
        write_json(ledger_path, ledger)
        if (
            consecutive_infrastructure_failures
            >= args.max_consecutive_infrastructure_failures
        ):
            raise RuntimeError(
                "stopping after consecutive infrastructure failures: "
                f"{consecutive_infrastructure_failures}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
