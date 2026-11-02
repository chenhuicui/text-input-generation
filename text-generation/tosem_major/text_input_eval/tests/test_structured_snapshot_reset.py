from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from text_input_eval.supplementary.structured_snapshot_reset import (
    push_prepare_files, restore, run_adb_prepare, synchronize_device_time,
    verify_device_time,
)


class StructuredSnapshotResetTests(unittest.TestCase):
    def test_read_only_device_time_verification_preserves_device(self) -> None:
        with patch(
            "text_input_eval.supplementary.structured_snapshot_reset.run",
            return_value=subprocess.CompletedProcess([], 0, "1791081572618\n", ""),
        ), patch(
            "text_input_eval.supplementary.structured_snapshot_reset.time.time",
            side_effect=[1791081572.612, 1791081572.620],
        ):
            receipt = verify_device_time("emulator-5564")

        self.assertTrue(receipt["verified"])
        self.assertFalse(receipt["mutation_performed"])
        self.assertEqual(receipt["absolute_skew_millis"], 2)

    def test_read_only_device_time_verification_rejects_stale_clock(self) -> None:
        with patch(
            "text_input_eval.supplementary.structured_snapshot_reset.run",
            return_value=subprocess.CompletedProcess([], 0, "1789630090000\n", ""),
        ), patch(
            "text_input_eval.supplementary.structured_snapshot_reset.time.time",
            side_effect=[1791081572.612, 1791081572.620],
        ):
            with self.assertRaisesRegex(RuntimeError, "is not current"):
                verify_device_time("emulator-5564")

    def test_snapshot_restore_synchronizes_and_verifies_device_time(self) -> None:
        outputs = iter(["", "1791081572618\n"])

        def fake_run(argv, timeout=180):
            return subprocess.CompletedProcess(argv, 0, next(outputs), "")

        with patch(
            "text_input_eval.supplementary.structured_snapshot_reset.run",
            side_effect=fake_run,
        ) as mocked_run, patch(
            "text_input_eval.supplementary.structured_snapshot_reset.time.time",
            side_effect=[1791081572.612, 1791081572.620],
        ):
            receipt = synchronize_device_time("emulator-5556")

        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["absolute_skew_millis"], 2)
        self.assertEqual(mocked_run.call_args_list[0].args[0], [
            "adb", "-s", "emulator-5556", "shell", "cmd", "alarm",
            "set-time", "1791081572612",
        ])
        self.assertEqual(mocked_run.call_args_list[1].args[0][-2:], [
            "date", "+%s%3N",
        ])

    def test_snapshot_restore_rejects_unverified_device_time(self) -> None:
        with patch(
            "text_input_eval.supplementary.structured_snapshot_reset.run",
            side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "1789630090000\n", ""),
            ],
        ), patch(
            "text_input_eval.supplementary.structured_snapshot_reset.time.time",
            side_effect=[1791081572.612, 1791081572.620],
        ):
            with self.assertRaisesRegex(RuntimeError, "remained stale"):
                synchronize_device_time("emulator-5556")

    def test_adb_prepare_is_package_geometry_guarded_and_asserted(self) -> None:
        xml = (
            '<hierarchy><node package="example.app" text="Selected" '
            'resource-id="country" /></hierarchy>'
        )

        def fake_run(argv, timeout=180):
            command = " ".join(argv)
            if "dumpsys window" in command:
                stdout = "mCurrentFocus=Window{1 u0 example.app/.MainActivity}\n"
            elif "wm size" in command:
                stdout = "Physical size: 1080x2400\n"
            elif " shell cat " in command:
                stdout = xml
            else:
                stdout = "OK\n"
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        actions = [
            {
                "action": "tap", "package": "example.app", "x": 10, "y": 20,
                "screen_width": 1080, "screen_height": 2400,
            },
            {"action": "input_text", "package": "example.app", "value": "United States"},
        ]
        contract = {
            "mode": "all",
            "targets": [{
                "selector": {"package": "example.app", "text": "Selected"},
                "exists": True,
            }],
        }
        with patch(
            "text_input_eval.supplementary.structured_snapshot_reset.run",
            side_effect=fake_run,
        ) as mocked_run:
            receipt = run_adb_prepare("emulator-5556", actions, contract)

        self.assertTrue(receipt["verified"])
        self.assertEqual(len(receipt["actions_sha256"]), 64)
        commands = [call.args[0] for call in mocked_run.call_args_list]
        self.assertIn(
            ["adb", "-s", "emulator-5556", "shell", "input", "text", "United%sStates"],
            commands,
        )

    def test_adb_prepare_rejects_foreground_package_drift(self) -> None:
        with patch(
            "text_input_eval.supplementary.structured_snapshot_reset.run",
            return_value=subprocess.CompletedProcess(
                [], 0, "mCurrentFocus=Window{1 u0 other.app/.MainActivity}\n", "",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "foreground package changed"):
                run_adb_prepare(
                    "emulator-5556",
                    [{
                        "action": "tap", "package": "example.app", "x": 1, "y": 1,
                        "screen_width": 1080, "screen_height": 2400,
                    }],
                    {"mode": "all", "targets": []},
                )

    def test_prepare_file_is_hash_pinned_and_media_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.png"
            fixture.write_bytes(b"frozen fixture")
            import hashlib
            digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
            with patch(
                "text_input_eval.supplementary.structured_snapshot_reset.run",
                return_value=subprocess.CompletedProcess([], 0, "OK", ""),
            ) as mocked_run:
                receipt = push_prepare_files("emulator-5556", [{
                    "host_path": str(fixture),
                    "device_path": "/sdcard/Download/fixture.png",
                    "sha256": digest,
                    "media_scan": True,
                }])
            self.assertEqual(receipt[0]["sha256"], digest)
            self.assertEqual(mocked_run.call_count, 3)
            self.assertEqual(mocked_run.call_args_list[0].args[0][:4], [
                "adb", "-s", "emulator-5556", "push",
            ])

    def test_local_snapshot_receipt_is_explicitly_not_remote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshots" / "P001_baseline_v1"
            snapshot.mkdir(parents=True)
            (snapshot / "snapshot.pb").write_bytes(b"snapshot")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "case_id": "P001",
                "status": "ready",
                "readiness_scope": "legacy_ready_scope_unclassified",
                "snapshot": "P001_baseline_v1",
                "snapshot_path": str(snapshot),
                "serial": "emulator-5556",
                "avd": "test-avd",
            }))

            def fake_run(argv, timeout=180):
                stdout = "test-avd\nOK\n" if argv[-2:] == ["avd", "name"] else "OK\n"
                return subprocess.CompletedProcess(argv, 0, stdout, "")

            payload = {"case": {"case_id": "P001"}, "attempt": {"oracle": {"case_id": "P001"}}}
            with patch(
                "text_input_eval.supplementary.structured_snapshot_reset.run",
                side_effect=fake_run,
            ), patch(
                "text_input_eval.supplementary.structured_snapshot_reset.load_snapshot",
                return_value="cold_restart",
            ), patch(
                "text_input_eval.supplementary.structured_snapshot_reset.synchronize_device_time",
                return_value={"verified": True, "absolute_skew_millis": 1},
            ):
                receipt = restore(manifest, "emulator-5556", "reset-v1", payload)
            self.assertTrue(receipt["reset_verified"])
            self.assertEqual(receipt["remote_baseline"], "not_applicable")
            self.assertEqual(receipt["reset_id"], "reset-v1")
            self.assertEqual(receipt["snapshot_load_strategy"], "cold_restart")
            self.assertTrue(receipt["device_time"]["verified"])

    def test_remote_state_case_cannot_use_local_only_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshots" / "B019"
            snapshot.mkdir(parents=True)
            (snapshot / "snapshot.pb").write_bytes(b"snapshot")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "case_id": "B019",
                "status": "ready",
                "readiness_scope": "full_independent_attempt_with_remote_reset",
                "snapshot": "B019",
                "snapshot_path": str(snapshot),
            }))
            payload = {"case": {"case_id": "B019"}, "attempt": {"oracle": {"case_id": "B019"}}}
            with self.assertRaisesRegex(RuntimeError, "case-specific reset"):
                restore(manifest, "emulator-5556", "reset-v1", payload)

    def test_verified_remote_receipt_requires_online_prepare_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshots" / "P073"
            snapshot.mkdir(parents=True)
            (snapshot / "snapshot.pb").write_bytes(b"snapshot")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "case_id": "P073", "status": "ready", "snapshot": "P073",
                "snapshot_path": str(snapshot), "avd": "test-avd",
            }))
            payload = {"case": {"case_id": "P073"}, "attempt": {"oracle": {"case_id": "P073"}}}
            with self.assertRaisesRegex(ValueError, "online prepare action"):
                restore(
                    manifest, "emulator-5556", "reset-v1", payload,
                    remote_baseline="verified",
                )

    def test_explicit_avd_supports_legacy_manifest_without_device_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshots" / "P028_baseline_v1"
            snapshot.mkdir(parents=True)
            (snapshot / "snapshot.pb").write_bytes(b"snapshot")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "case_id": "P028",
                "status": "ready",
                "snapshot": "P028_baseline_v1",
                "snapshot_path": str(snapshot),
            }))
            payload = {"case": {"case_id": "P028"}, "attempt": {"oracle": {"case_id": "P028"}}}

            def fake_run(argv, timeout=180):
                stdout = "legacy-avd\nOK\n" if argv[-2:] == ["avd", "name"] else "OK\n"
                return subprocess.CompletedProcess(argv, 0, stdout, "")

            with patch(
                "text_input_eval.supplementary.structured_snapshot_reset.run",
                side_effect=fake_run,
            ), patch(
                "text_input_eval.supplementary.structured_snapshot_reset.load_snapshot",
                return_value="hot_load",
            ) as load, patch(
                "text_input_eval.supplementary.structured_snapshot_reset.synchronize_device_time",
                return_value={"verified": True, "absolute_skew_millis": 1},
            ):
                receipt = restore(
                    manifest, "emulator-5556", "P028-local-v1", payload, "legacy-avd",
                )
            self.assertTrue(receipt["reset_verified"])
            load.assert_called_once_with(
                "emulator-5556", "legacy-avd", snapshot,
                snapshot.parent / "snapshot_reset_emulator.log",
                force_cold=False,
            )

    def test_formal_reset_starts_exact_avd_when_no_emulator_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshots" / "P042"
            snapshot.mkdir(parents=True)
            (snapshot / "snapshot.pb").write_bytes(b"snapshot")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "case_id": "P042", "status": "ready", "snapshot": "P042",
                "snapshot_path": str(snapshot), "avd": "target-avd",
            }))
            payload = {
                "case": {"case_id": "P042"},
                "attempt": {"oracle": {"case_id": "P042"}},
            }

            with patch(
                "text_input_eval.supplementary.structured_snapshot_reset.run",
                side_effect=RuntimeError("no device"),
            ), patch(
                "text_input_eval.supplementary.structured_snapshot_reset.load_snapshot",
                return_value="cold_restart",
            ) as load, patch(
                "text_input_eval.supplementary.structured_snapshot_reset.synchronize_device_time",
                return_value={"verified": True, "absolute_skew_millis": 1},
            ):
                receipt = restore(
                    manifest, "emulator-5556", "P042-local-v1", payload,
                )

            load.assert_called_once_with(
                "emulator-5556", "target-avd", snapshot,
                snapshot.parent / "snapshot_reset_emulator.log",
                force_cold=True,
            )
            self.assertIsNone(receipt["avd_before_reset"])
            self.assertTrue(receipt["avd_switch_required"])

    def test_offline_reset_returns_verified_network_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshots" / "P011"
            snapshot.mkdir(parents=True)
            (snapshot / "snapshot.pb").write_bytes(b"snapshot")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "case_id": "P011", "status": "ready", "snapshot": "P011",
                "snapshot_path": str(snapshot), "avd": "test-avd",
            }))
            payload = {"case": {"case_id": "P011"}, "attempt": {"oracle": {"case_id": "P011"}}}
            with patch(
                "text_input_eval.supplementary.structured_snapshot_reset.run",
                return_value=subprocess.CompletedProcess([], 0, "test-avd\nOK\n", ""),
            ), patch(
                "text_input_eval.supplementary.structured_snapshot_reset.load_snapshot",
                return_value="hot_load",
            ), patch(
                "text_input_eval.supplementary.structured_snapshot_reset.synchronize_device_time",
                return_value={"verified": True, "absolute_skew_millis": 1},
            ), patch(
                "text_input_eval.supplementary.structured_snapshot_reset.isolate_external_network",
                return_value={"mode": "offline", "airplane_mode_verified": True},
            ):
                receipt = restore(manifest, "emulator-5556", "reset-v1", payload, offline=True)
            self.assertTrue(receipt["network_isolation"]["airplane_mode_verified"])


if __name__ == "__main__":
    unittest.main()
