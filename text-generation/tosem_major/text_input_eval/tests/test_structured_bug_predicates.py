from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from text_input_eval.supplementary.structured_bug_predicates import control_pair
from text_input_eval.supplementary.structured_environment import StringPredicateEnvironment
from text_input_eval.supplementary.structured_preflight import REPO_ROOT, build_registry


class StructuredBugPredicateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config_path = REPO_ROOT / "configs" / "supplementary" / "e2_structure_v1.json"
        cls.registry, _ = build_registry(cls.config_path)
        cls.bug_cases = [row for row in cls.registry["cases"] if row["task"] == "invalid_bug"]
        manifest_path = REPO_ROOT / "output" / "e2_structure_v1_20260920" / "formal_oracles.json"
        cls.oracles = {
            row["case_id"]: row for row in json.loads(manifest_path.read_text(encoding="utf-8"))["cases"]
        }

    def test_all_37_predicates_have_true_and_false_controls(self) -> None:
        self.assertEqual(len(self.bug_cases), 37)
        for case in self.bug_cases:
            positive, negative = control_pair(case)
            oracle = self.oracles[case["case_id"]]
            self.assertEqual(oracle["oracle_kind"], "string_predicate")
            self.assertEqual(oracle["predicate"]["required_resource_ids"], case["required_resource_ids"])
            self.assertNotEqual(positive, negative)

    def test_all_formal_oracles_match_schema(self) -> None:
        schema_path = self.config_path.with_name("e2_structure_v1_oracle.schema.json")
        validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
        for oracle in self.oracles.values():
            self.assertEqual(list(validator.iter_errors(oracle)), [])

    def test_b028_literal_space_executes_without_android(self) -> None:
        case = next(row for row in self.bug_cases if row["case_id"] == "B028")
        oracle = self.oracles["B028"]
        resource_id = case["required_resource_ids"][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = StringPredicateEnvironment(REPO_ROOT)
            attempt = {
                "attempt_id": "attempt-b028",
                "oracle": oracle,
                "evidence_dir": str(root / "begin"),
            }
            begin = environment.begin_attempt(case, attempt)
            failed = environment.execute_turn({resource_id: ""}, {
                "attempt_id": "attempt-b028",
                "request_id": "request-1",
                "turn": 1,
                "case": case,
                "oracle": oracle,
                "evidence_dir": str(root / "request-1"),
            })
            recovered = environment.recover_turn({
                **failed,
                "turn": 1,
                "case": case,
                "oracle": oracle,
                "evidence_dir": str(root / "request-1"),
            })
            passed = environment.execute_turn({resource_id: " "}, {
                "attempt_id": "attempt-b028",
                "request_id": "request-2",
                "turn": 2,
                "case": case,
                "oracle": oracle,
                "evidence_dir": str(root / "request-2"),
            })
            self.assertTrue(begin["reset_verified"])
            self.assertEqual(failed["verdict"], "fail")
            self.assertEqual(recovered["state_before_hash"], failed["state_after_hash"])
            self.assertEqual(passed["state_before_hash"], recovered["state_hash"])
            self.assertEqual(passed["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
