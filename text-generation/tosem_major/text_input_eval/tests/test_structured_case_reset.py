from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from text_input_eval.supplementary.structured_case_reset import (
    ISHARING_RESET_ID,
    _isharing_phone_row_center,
    _repair_isharing_remote_baseline,
    reset,
)


class StructuredCaseResetTests(unittest.TestCase):
    def test_locates_unique_isharing_phone_row_by_accessibility_contract(self) -> None:
        hierarchy = """<hierarchy>
          <node clickable="false" content-desc="" bounds="[0,0][1080,2400]" />
          <node clickable="true" content-desc="+1 6505551234" bounds="[45,1317][1035,1380]" />
        </hierarchy>"""
        self.assertEqual(_isharing_phone_row_center(hierarchy), (540, 1348))

    def test_rejects_ambiguous_isharing_phone_rows(self) -> None:
        hierarchy = """<hierarchy>
          <node clickable="true" content-desc="+1 6505551234" bounds="[0,0][10,10]" />
          <node clickable="true" content-desc="+1 6505555678" bounds="[0,10][10,20]" />
        </hierarchy>"""
        with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
            _isharing_phone_row_center(hierarchy)

    def test_isharing_repair_uses_selected_fresh_client_serial(self) -> None:
        field = MagicMock()
        field.wait.return_value = True
        save = MagicMock()
        save.wait.return_value = True
        device = MagicMock()
        device.app_current.return_value = {"package": "com.isharing.isharing"}
        device.dump_hierarchy.return_value = (
            '<hierarchy><node clickable="true" content-desc="+1 6505551234" '
            'bounds="[45,1317][1035,1380]" /></hierarchy>'
        )
        device.side_effect = lambda **selector: field if selector.get("resourceId") else save
        connect = MagicMock(return_value=device)
        keychain = SimpleNamespace(stdout="6505551234\n")

        with (
            patch("text_input_eval.supplementary.structured_case_reset.run", return_value=keychain),
            patch.dict("sys.modules", {"uiautomator2": SimpleNamespace(connect=connect)}),
            patch("text_input_eval.supplementary.structured_case_reset.time.sleep"),
        ):
            _repair_isharing_remote_baseline("emulator-5564")

        connect.assert_called_once_with("emulator-5564")
        field.set_text.assert_called_once_with("6505551234")
        save.click.assert_called_once_with()

    def test_isharing_repair_rejects_selected_serial_without_session(self) -> None:
        device = MagicMock()
        device.app_current.return_value = {"package": "com.example.unrelated"}
        connect = MagicMock(return_value=device)
        keychain = SimpleNamespace(stdout="6505551234\n")

        with (
            patch("text_input_eval.supplementary.structured_case_reset.run", return_value=keychain),
            patch.dict("sys.modules", {"uiautomator2": SimpleNamespace(connect=connect)}),
        ):
            with self.assertRaisesRegex(RuntimeError, "session is unavailable"):
                _repair_isharing_remote_baseline("emulator-5560")

        connect.assert_called_once_with("emulator-5560")

    @patch("text_input_eval.supplementary.structured_case_reset.current_avd")
    @patch("text_input_eval.supplementary.structured_case_reset._running_argv")
    @patch("text_input_eval.supplementary.structured_case_reset._run_case_command")
    @patch("text_input_eval.supplementary.structured_case_reset.verify_device_time")
    @patch("text_input_eval.supplementary.structured_case_reset.synchronize_device_time")
    def test_isharing_reset_forwards_target_and_fresh_serials(
        self,
        synchronize_time: MagicMock,
        verify_time: MagicMock,
        run_command: MagicMock,
        running_argv: MagicMock,
        current_avd: MagicMock,
    ) -> None:
        manifest = {
            "case_id": "P043",
            "status": "ready",
            "attempt_reset_id": ISHARING_RESET_ID,
            "attempt_prepare_command": ["python3", "/tmp/isharing_reset.py"],
            "target_hold_snapshot": "P043_google_host_v2",
            "required_emulator_args": [],
        }
        receipt = {
            "case_id": "P043",
            "reset_id": ISHARING_RESET_ID,
            "reset_verified": True,
            "remote_baseline_verified": True,
        }
        completed = SimpleNamespace(returncode=0, stdout=__import__("json").dumps(receipt), stderr="")
        run_command.return_value = completed
        running_argv.return_value = ["qemu-system", "-avd", "cd_blocked_google_api33"]
        current_avd.return_value = "cd_blocked_google_api33"
        synchronize_time.return_value = {"verified": True, "mutation_performed": True}
        verify_time.return_value = {"verified": True, "mutation_performed": False}

        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
            with patch.dict("os.environ", {"E2_P043_FRESH_CLIENT_SERIAL": "emulator-5568"}):
                result = reset(
                    manifest_path,
                    "emulator-5556",
                    "cd_blocked_google_api33",
                    ISHARING_RESET_ID,
                    {},
                )

        environment = run_command.call_args.kwargs["environment"]
        self.assertEqual(environment["P043_TARGET_SERIAL"], "emulator-5556")
        self.assertEqual(environment["P043_FRESH_SERIAL"], "emulator-5568")
        synchronize_time.assert_called_once_with("emulator-5556")
        verify_time.assert_called_once_with("emulator-5568")
        self.assertTrue(result["device_time"]["verified"])
        self.assertFalse(result["fresh_client_device_time"]["mutation_performed"])


if __name__ == "__main__":
    unittest.main()
