import json
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.structured_recovery_preflight import (
    render_protocol,
    verify_oracle_partition,
)


class StructuredRecoveryPreflightTests(unittest.TestCase):
    def _config(self, root: Path) -> dict:
        return {
            "historical_protocol": "E2-structure-v1",
            "extension_protocol": "E2-abcdef-v1",
            "historical_artifacts": {
                "unavailable_originals": {
                    "case_registry_sha256": "a" * 64,
                    "formal_oracles_sha256": "b" * 64,
                }
            },
            "reconstructed_artifacts": {
                "formal_oracles": "oracles.json",
                "formal_exclusions": "exclusions.json",
            },
            "expected": {
                "registry_cases": 4,
                "applicable_cases": 3,
                "valid_pptr_oracles": 2,
                "invalid_bug_oracles": 1,
                "not_applicable_cases": ["P003"],
            },
        }

    def test_partition_keeps_bug_predicate_and_explicit_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "recovery.json"
            config = self._config(root)
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / "oracles.json").write_text(json.dumps({"cases": [
                {"case_id": "P001", "task": "valid_pptr", "oracle_kind": "app_behavior"},
                {"case_id": "P002", "task": "valid_pptr", "oracle_kind": "app_behavior"},
                {
                    "case_id": "B001", "task": "invalid_bug",
                    "oracle_kind": "string_predicate",
                    "predicate": {"true_means": "bug_revealed"},
                },
            ]}), encoding="utf-8")
            (root / "exclusions.json").write_text(json.dumps({
                "cases": [{"case_id": "P003", "disposition": "not_applicable"}]
            }), encoding="utf-8")
            gates = []
            detail = verify_oracle_partition(config, config_path, gates)
            self.assertTrue(gates[-1]["passed"])
            self.assertTrue(detail["bug_string_predicates_valid"])
            self.assertEqual(detail["covered_cases"], 4)

    def test_protocol_preserves_history_and_unknown_semantics(self):
        config = self._config(Path("."))
        protocol = render_protocol(config, {"formal_oracles": 3})
        self.assertIn("A-D", protocol)
        self.assertIn("A-F", protocol)
        self.assertIn("unknown", protocol)
        self.assertIn("infrastructure_error", protocol)
        self.assertIn("不调用付费模型", protocol)
        self.assertIn("formal_run_enabled", protocol)


if __name__ == "__main__":
    unittest.main()
