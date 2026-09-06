import unittest

from benchmarks.harbor.run_long_horizon import provider_infrastructure_failure


class RunLongHorizonTest(unittest.TestCase):
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
