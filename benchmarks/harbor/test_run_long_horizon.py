import unittest
from pathlib import Path

from benchmarks.harbor.run_long_horizon import job_config, provider_infrastructure_failure


class RunLongHorizonTest(unittest.TestCase):
    def test_task_identity_does_not_change_agent_configuration(self) -> None:
        manifest = {
            "model": "test-model", "timeout_seconds": 60,
            "max_model_steps": 20, "max_output_tokens": 100,
            "checkpoint_batches": 2, "compaction_effort": 1,
            "continuation_probability_bps": 10000,
            "cached_input_cost_bps": 10000, "thinking_enabled": False,
        }
        for controller in ("advisory_v18", "typed_completion_auto_v2"):
            configurations = []
            for task in ("build-cython-ext", "db-wal-recovery", "unseen-task"):
                trial = {"arm": "B0", "trial_id": "trial", "task": task,
                         "terminal_controller_policy": controller}
                config = job_config(manifest, trial, Path("jobs"),
                                    "http://localhost", "TEST_TOKEN", "open_ai_chat_completions")
                configurations.append(config["agents"][0]["kwargs"])
            self.assertEqual(configurations[0], configurations[1])
            self.assertEqual(configurations[0], configurations[2])
            self.assertNotIn("public_validation_profile", configurations[0])

    def test_provider_call_failure_is_infrastructure(self) -> None:
        self.assertTrue(
            provider_infrastructure_failure(
                {"provider_calls": [{"succeeded": True}, {"succeeded": False}]}
            )
        )

    def test_canonical_provider_failure_is_infrastructure(self) -> None:
        self.assertTrue(
            provider_infrastructure_failure(
                {
                    "events": [
                        {
                            "event": {
                                "type": "run.failed",
                                "payload": {
                                    "message": "model provider failed: network unavailable"
                                },
                            }
                        }
                    ]
                }
            )
        )

    def test_task_failure_is_not_infrastructure(self) -> None:
        self.assertFalse(
            provider_infrastructure_failure(
                {
                    "provider_calls": [{"succeeded": True}],
                    "events": [
                        {
                            "event": {
                                "type": "run.failed",
                                "payload": {"message": "tool_loop_detected"},
                            }
                        }
                    ],
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
