from __future__ import annotations

import json
import unittest

from text_input_eval.supplementary.structured_synthetic_control_candidates import (
    synthesize_pair,
)


class SyntheticControlCandidateTests(unittest.TestCase):
    def test_completes_missing_arm_without_promoting_validator_to_oracle(self) -> None:
        field = "example:id/value"
        rows = [
            {"parsed_input_json": json.dumps({field: "accepted"})},
            {"parsed_input_json": json.dumps({field: "also accepted"})},
        ]
        positive = {
            "candidate": {field: "accepted"},
            "source": {"sha256": "a" * 64},
        }

        completed_positive, negative = synthesize_pair(
            "P999", rows, {field},
            lambda candidate: bool(candidate[field].strip()),
            positive, None, random_budget=10,
        )

        self.assertIs(completed_positive, positive)
        self.assertEqual(negative["candidate"], {field: " "})
        self.assertEqual(
            negative["source"]["kind"],
            "deterministic_validator_guided_recombination",
        )
        self.assertIn("App controls decide behavior", negative["source"]["meaning"])

    def test_constant_false_validator_stays_incomplete(self) -> None:
        field = "example:id/value"
        positive, negative = synthesize_pair(
            "P998", [], {field}, lambda candidate: False,
            None, None, random_budget=10,
        )

        self.assertIsNone(positive)
        self.assertIsNotNone(negative)


if __name__ == "__main__":
    unittest.main()
