from __future__ import annotations

import tempfile
import unittest
import json
from unittest.mock import patch
from pathlib import Path

from benchmarks.harbor.campaign_control import (
    append_trial,
    authorize_stage,
    create_campaign,
    record_spend,
    resume_sequence,
    stop_stage,
)
from benchmarks.harbor.run_long_horizon import job_config


class CampaignControlTests(unittest.TestCase):
    def test_freeze_resume_and_budget_stop_are_deterministic(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            manifest = base / "manifest.json"
            manifest.write_text('{"trials":[]}\n')
            root = base / "campaign"
            freeze = create_campaign(root, repository, [manifest], [], ["MISSING_TEST_KEY"], 100)
            self.assertFalse(freeze["credential_environment"]["MISSING_TEST_KEY"])
            self.assertEqual(authorize_stage(root, "phase-0", 60)["status"], "authorized")
            record_spend(root, "phase-0", 20)
            self.assertEqual(
                authorize_stage(root, "phase-2", 90)["status"], "budget_stopped"
            )
            append_trial(root, {"sequence": 1, "trial_id": "trial-1"})
            self.assertEqual(resume_sequence(root), 2)
            stopped = stop_stage(root, "phase-0", "terminal mechanism failed")
            self.assertEqual(stopped["status"], "mechanism_stopped")
            self.assertEqual(stopped["completed_trials"], 1)
            self.assertEqual(
                json.loads((root / "budget.json").read_text())["campaign_status"],
                "mechanism_stopped",
            )
            stopped = stop_stage(
                root,
                "phase-0",
                "three provider failures",
                "infrastructure_stopped",
            )
            self.assertEqual(stopped["status"], "infrastructure_stopped")
            with self.assertRaises(ValueError):
                append_trial(root, {"sequence": 3, "trial_id": "trial-3"})

    def test_campaign_directory_is_non_overwritable(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            create_campaign(root, repository, [], [], [], 100)
            with self.assertRaises(FileExistsError):
                create_campaign(root, repository, [], [], [], 100)

    def test_freeze_records_presence_without_serializing_secret(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"REDACTION_TEST_KEY": "do-not-serialize-this-value"}
        ):
            root = Path(temporary) / "campaign"
            freeze = create_campaign(
                root, repository, [], [], ["REDACTION_TEST_KEY"], 100
            )
            serialized = (root / "freeze.json").read_text()
            self.assertTrue(freeze["credential_environment"]["REDACTION_TEST_KEY"])
            self.assertNotIn("do-not-serialize-this-value", serialized)

    def test_harbor_config_references_credential_environment_only(self) -> None:
        manifest = {
            "model": "glm-5.3-flash",
            "timeout_seconds": 900,
            "max_model_steps": 128,
            "max_output_tokens": 8192,
            "checkpoint_batches": 8,
            "compaction_effort": 1,
            "continuation_probability_bps": 7500,
            "cached_input_cost_bps": 0,
            "thinking_enabled": True,
        }
        trial = {"trial_id": "trial-1", "task": "task-1", "arm": "b0"}
        config = job_config(
            manifest,
            trial,
            Path("jobs"),
            "https://provider.example/v4",
            "GLM_APIKEY",
            "open_ai_chat_completions",
        )
        serialized = str(config)
        self.assertIn("GLM_APIKEY", serialized)
        self.assertNotIn("do-not-serialize-this-value", serialized)
        kwargs = config["agents"][0]["kwargs"]
        self.assertNotIn("provider_client_token", kwargs)
        self.assertEqual(kwargs["provider_client_token_env"], "GLM_APIKEY")


if __name__ == "__main__":
    unittest.main()
