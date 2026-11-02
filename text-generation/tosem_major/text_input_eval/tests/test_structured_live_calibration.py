from __future__ import annotations

import unittest
import subprocess
from unittest.mock import patch
from xml.etree import ElementTree as ET

from text_input_eval.supplementary.structured_live_calibration import (
    binding_candidates,
    case_contract_sha256,
    goal_candidate,
    stop_conflicting_avd,
)


def root(*nodes: str) -> ET.Element:
    return ET.fromstring("<hierarchy>" + "".join(nodes) + "</hierarchy>")


def edit(**attributes: str) -> str:
    rendered = " ".join(f'{key}="{value}"' for key, value in attributes.items())
    return f"<node {rendered} />"


class StructuredLiveCalibrationTests(unittest.TestCase):
    def test_stops_only_an_exact_avd_match_on_another_serial(self) -> None:
        def fake_run(argv, timeout=180, check=True):
            command = " ".join(argv)
            if argv == ["adb", "devices"]:
                stdout = (
                    "List of devices attached\n"
                    "emulator-5554\tdevice\n"
                    "emulator-5556\tdevice\n"
                    "emulator-5558\tdevice\n"
                )
                return subprocess.CompletedProcess(argv, 0, stdout, "")
            if command.endswith("emulator-5554 emu avd name"):
                return subprocess.CompletedProcess(argv, 0, "target-avd\nOK\n", "")
            if command.endswith("emulator-5558 emu avd name"):
                return subprocess.CompletedProcess(argv, 0, "other-avd\nOK\n", "")
            if command.endswith("emulator-5554 get-state"):
                return subprocess.CompletedProcess(argv, 1, "", "not found")
            return subprocess.CompletedProcess(argv, 0, "OK\n", "")

        with patch(
            "text_input_eval.supplementary.structured_live_calibration.run",
            side_effect=fake_run,
        ) as mocked_run:
            stopped = stop_conflicting_avd("emulator-5556", "target-avd")

        self.assertEqual(stopped, ["emulator-5554"])
        commands = [call.args[0] for call in mocked_run.call_args_list]
        self.assertIn(["adb", "-s", "emulator-5554", "emu", "kill"], commands)
        self.assertNotIn(["adb", "-s", "emulator-5558", "emu", "kill"], commands)

    def test_goal_candidate_rejects_empty_and_contradictory_targets(self) -> None:
        goal, issues = goal_candidate("example.app", {
            "verify_appear": {"targets": ["", "Search"], "by": "text"},
            "verify_disappear": {"targets": ["Search"], "by": "text"},
        })

        self.assertEqual(len(goal["targets"]), 2)
        self.assertIn("empty text goal target", issues)
        self.assertIn(
            "contradictory existence requirements for the same goal selector", issues,
        )

    def test_case_contract_ignores_evolving_readiness_and_formal_oracle(self) -> None:
        case = {
            "case_id": "P001",
            "task": "valid_pptr",
            "required_resource_ids": ["field"],
            "source_files": {"validator_sha256": "a" * 64},
            "mirror": {
                "package": "example.app", "version_code": "1",
                "source_sha256": "b" * 64, "snapshot": "baseline",
                "snapshot_path": "/snapshots/baseline", "manifest_sha256": "c" * 64,
            },
            "formal_oracle": {"available": False},
            "protocol_readiness": {"formal_run_ready": False},
        }
        before = case_contract_sha256(case)
        case["formal_oracle"] = {"available": True, "version": "v1"}
        case["protocol_readiness"] = {"formal_run_ready": True}
        self.assertEqual(before, case_contract_sha256(case))

    def test_uses_expected_runtime_field_and_normalized_id_migration(self) -> None:
        tree = root(edit(
            package="example.app", **{
                "class": "android.widget.EditText", "resource-id": "example.app:id/edit_text",
                "text": "City", "enabled": "true",
            },
        ))
        bindings, issues = binding_candidates(
            tree, "example.app", ["example.app:id/editText"], {"expected_fields": []},
        )
        self.assertEqual(issues, [])
        self.assertEqual(bindings["example.app:id/editText"]["resource-id"], "example.app:id/edit_text")

        bindings, issues = binding_candidates(
            tree, "example.app", ["old-id"], {"expected_fields": ["example.app:id/edit_text"]},
        )
        self.assertEqual(issues, [])
        self.assertEqual(bindings["old-id"]["resource-id"], "example.app:id/edit_text")

    def test_single_editable_node_gets_a_coordinate_free_unique_binding(self) -> None:
        tree = root(edit(
            package="example.app", **{
                "class": "android.widget.EditText", "resource-id": "", "text": "",
                "enabled": "true",
            },
        ))
        bindings, issues = binding_candidates(tree, "example.app", ["legacy-id"], {})
        self.assertEqual(issues, [])
        self.assertEqual(bindings["legacy-id"], {
            "package": "example.app",
            "class": "android.widget.EditText",
            "enabled": True,
            "unique": True,
        })

    def test_descendant_text_semantic_contract_is_preserved(self) -> None:
        tree = ET.fromstring(
            '<hierarchy><node package="example.app" class="android.widget.EditText" '
            'resource-id="" text="" enabled="true">'
            '<node package="example.app" class="android.widget.TextView" text="Tracking number" />'
            '</node></hierarchy>'
        )
        mirror = {"semantic_fields": [{
            "class": "android.widget.EditText", "resource_id": "",
            "descendant_text": "Tracking number",
        }]}
        bindings, issues = binding_candidates(tree, "example.app", ["TRACKING"], mirror)
        self.assertEqual(issues, [])
        self.assertEqual(bindings["TRACKING"]["descendant-text"], "Tracking number")

    def test_bounds_encoded_no_id_fields_map_to_runtime_instances(self) -> None:
        tree = root(
            edit(package="example.app", **{
                "class": "android.widget.EditText", "resource-id": "", "text": "",
                "enabled": "true", "bounds": "[10,100][20,150]",
            }),
            edit(package="example.app", **{
                "class": "android.widget.EditText", "resource-id": "", "text": "",
                "enabled": "true", "bounds": "[10,200][20,250]",
            }),
        )
        required = ["no_id_EditText_20_300_40_350", "no_id_EditText_20_600_40_650"]
        bindings, issues = binding_candidates(tree, "example.app", required, {})
        self.assertEqual(issues, [])
        self.assertEqual(bindings[required[0]]["instance"], 0)
        self.assertEqual(bindings[required[1]]["instance"], 1)

    def test_bounds_order_excludes_an_unrelated_later_field(self) -> None:
        tree = root(
            edit(package="example.app", **{
                "class": "android.widget.EditText", "resource-id": "input", "text": "A",
                "enabled": "true", "bounds": "[10,100][100,140]",
            }),
            edit(package="example.app", **{
                "class": "android.widget.EditText", "resource-id": "input", "text": "B",
                "enabled": "true", "bounds": "[10,400][100,440]",
            }),
            edit(package="example.app", **{
                "class": "android.widget.EditText", "resource-id": "input", "text": "C",
                "enabled": "true", "bounds": "[10,500][100,540]",
            }),
            edit(package="example.app", **{
                "class": "android.widget.EditText", "resource-id": "input", "text": "D",
                "enabled": "true", "bounds": "[10,900][100,940]",
            }),
        )
        required = [
            "input__bounds_20_200_200_280",
            "input__bounds_20_800_200_880",
            "input__bounds_20_1000_200_1080",
        ]
        bindings, issues = binding_candidates(tree, "example.app", required, {})
        self.assertEqual(issues, [])
        self.assertEqual([bindings[item]["text"] for item in required], ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
