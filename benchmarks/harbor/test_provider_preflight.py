import unittest

from benchmarks.harbor.provider_preflight import (
    decoded_arguments,
    valid_completion_arguments,
)


class ProviderPreflightTest(unittest.TestCase):
    def test_completion_arguments_accept_only_the_frozen_summary(self) -> None:
        self.assertTrue(valid_completion_arguments('{"summary":"preflight-ok"}'))
        self.assertTrue(valid_completion_arguments({"summary": "preflight-ok"}))
        self.assertFalse(valid_completion_arguments("{}"))
        self.assertFalse(valid_completion_arguments('{"summary":"placeholder"}'))
        self.assertFalse(valid_completion_arguments("not-json"))

    def test_decoded_arguments_rejects_non_object_json(self) -> None:
        self.assertEqual(decoded_arguments('{"summary":"preflight-ok"}'), {
            "summary": "preflight-ok"
        })
        self.assertIsNone(decoded_arguments("[]"))
        self.assertIsNone(decoded_arguments(None))


if __name__ == "__main__":
    unittest.main()
