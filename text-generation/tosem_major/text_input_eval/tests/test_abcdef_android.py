from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from text_input_eval.supplementary.abcdef_android import (
    _enforce_trigger_reached,
    _is_observational_input_rejection,
    _replace_delayed_frames_from_video,
    _split_observation_trigger,
    _valid_png,
    begin_attempt,
    execute_turn,
    recover_turn,
)
from text_input_eval.supplementary.abcdef_android_repaired import (
    _B017_BINDING,
    _B017_FROZEN_INITIAL_XML_SHA256,
    _P001_CLONE_AVD,
    _P001_CLONE_MANIFEST,
    _P001_ORACLE_VERSION,
    _P001_RESET_ID,
    _P001_SOURCE_MANIFEST,
    _P006_ORACLE_VERSION,
    _P006_RESET_ID,
    _P006_RESET_WRAPPER,
    _P006_SOURCE_AVD,
    _P006_SOURCE_MANIFEST,
    _P006_SOURCE_SERIAL,
    _P033_DATE_BINDING,
    _P093_PHONE_BINDING,
    _P094_LOGIN_GUARD_ACTIONS,
    _P094_MAX_TRANSIENT_VIEW_ROOTS,
    _P094_PRE_ENTER_GUARD_ACTIONS,
    _P094_REPAIRED_SELECTOR,
    _repair_b017_payload,
    _repair_p001_payload,
    _repair_p006_payload,
    _repair_p033_payload,
    _repair_p093_payload,
    _repair_p094_payload,
    _repaired_adb_uiautomator_hierarchy,
    _repaired_hierarchy,
    _repaired_ime_visible,
)
from text_input_eval.supplementary.structured_android import AndroidDriver


class ABCDEFAndroidTests(unittest.TestCase):
    def _p001_payload(self) -> dict:
        return {
            "operation": "begin_attempt",
            "case": {"case_id": "P001"},
            "attempt": {"oracle": {
                "case_id": "P001",
                "version": _P001_ORACLE_VERSION,
                "begin_attempt": {
                    "reset_id": _P001_RESET_ID,
                    "reset_command": [
                        "/opt/anaconda3/bin/python3",
                        "-m",
                        "text_input_eval.supplementary.structured_snapshot_reset",
                        "--manifest",
                        _P001_SOURCE_MANIFEST,
                        "--device",
                        "emulator-5566",
                        "--avd",
                        _P001_CLONE_AVD,
                        "--reset-id",
                        _P001_RESET_ID,
                    ],
                },
            }},
        }

    def _b017_payload(self, initial_xml: Path) -> dict:
        return {
            "operation": "begin_attempt",
            "case": {"case_id": "B017"},
            "attempt": {"oracle": {
                "case_id": "B017",
                "version": "B017-app-observation-20260925-v2",
                "oracle_role": "feedback_observation_only",
                "verdict_oracle": "frozen_string_predicate",
                "binding": _B017_BINDING,
                "execute_turn": {"hierarchy_transport": "frozen_initial_xml"},
                "source_evidence": {
                    "initial_xml": str(initial_xml),
                    "initial_xml_sha256": _B017_FROZEN_INITIAL_XML_SHA256,
                },
            }},
        }

    def _p006_payload(self) -> dict:
        return {
            "operation": "begin_attempt",
            "case": {"case_id": "P006"},
            "attempt": {"oracle": {
                "case_id": "P006",
                "version": _P006_ORACLE_VERSION,
                "begin_attempt": {
                    "reset_id": _P006_RESET_ID,
                    "reset_command": [
                        "/opt/anaconda3/bin/python3",
                        "-m",
                        "text_input_eval.supplementary.structured_snapshot_reset",
                        "--manifest",
                        _P006_SOURCE_MANIFEST,
                        "--device",
                        _P006_SOURCE_SERIAL,
                        "--avd",
                        _P006_SOURCE_AVD,
                        "--reset-id",
                        _P006_RESET_ID,
                        "--offline",
                    ],
                },
            }},
        }

    def test_b017_missing_frozen_hierarchy_uses_live_uiautomator2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = self._b017_payload(Path(directory) / "missing.xml")
            repaired, applied = _repair_b017_payload(payload)
        self.assertTrue(applied)
        execution = repaired["attempt"]["oracle"]["execute_turn"]
        self.assertEqual(execution["hierarchy_transport"], "uiautomator2")
        self.assertEqual(
            execution["hierarchy_transport_repair"],
            "B017-missing-frozen-xml-live-uiautomator2-v1",
        )
        self.assertEqual(
            payload["attempt"]["oracle"]["execute_turn"]["hierarchy_transport"],
            "frozen_initial_xml",
        )

    def test_b017_restored_frozen_hierarchy_must_match_declared_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "initial.xml"
            path.write_text("<hierarchy />", encoding="utf-8")
            payload = self._b017_payload(path)
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                _repair_b017_payload(payload)

    def test_b017_repair_rejects_contract_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = self._b017_payload(Path(directory) / "missing.xml")
            payload["attempt"]["oracle"]["binding"] = {}
            with self.assertRaisesRegex(ValueError, "frozen reviewed contract"):
                _repair_b017_payload(payload)

    def test_p001_payload_routes_only_manifest_to_5566_clone(self) -> None:
        payload = self._p001_payload()
        repaired, applied = _repair_p001_payload(payload)
        self.assertTrue(applied)
        command = repaired["attempt"]["oracle"]["begin_attempt"]["reset_command"]
        self.assertEqual(command[command.index("--manifest") + 1], _P001_CLONE_MANIFEST)
        self.assertEqual(command[command.index("--avd") + 1], _P001_CLONE_AVD)
        original = payload["attempt"]["oracle"]["begin_attempt"]["reset_command"]
        self.assertEqual(
            original[original.index("--manifest") + 1],
            _P001_SOURCE_MANIFEST,
        )

    def test_p001_payload_repair_fails_closed_on_contract_drift(self) -> None:
        payload = self._p001_payload()
        payload["attempt"]["oracle"]["version"] = "unknown"
        with self.assertRaisesRegex(ValueError, "frozen reviewed contract"):
            _repair_p001_payload(payload)

        payload = self._p001_payload()
        command = payload["attempt"]["oracle"]["begin_attempt"]["reset_command"]
        command[command.index("--manifest") + 1] = "/tmp/other.json"
        with self.assertRaisesRegex(ValueError, "reset command drifted"):
            _repair_p001_payload(payload)

    def test_p001_payload_repair_does_not_touch_other_cases(self) -> None:
        payload = self._p001_payload()
        payload["case"]["case_id"] = "P002"
        repaired, applied = _repair_p001_payload(payload)
        self.assertFalse(applied)
        self.assertIs(repaired, payload)

    def test_p006_payload_routes_reset_through_qualified_activity_wrapper(self) -> None:
        payload = self._p006_payload()
        repaired, applied = _repair_p006_payload(payload)
        self.assertTrue(applied)
        command = repaired["attempt"]["oracle"]["begin_attempt"]["reset_command"]
        self.assertEqual(command[:2], ["/opt/anaconda3/bin/python3", _P006_RESET_WRAPPER])
        self.assertNotIn("-m", command)
        original = payload["attempt"]["oracle"]["begin_attempt"]["reset_command"]
        self.assertEqual(
            original[:3],
            [
                "/opt/anaconda3/bin/python3",
                "-m",
                "text_input_eval.supplementary.structured_snapshot_reset",
            ],
        )

    def test_p006_payload_repair_fails_closed_off_source_emulator(self) -> None:
        payload = self._p006_payload()
        command = payload["attempt"]["oracle"]["begin_attempt"]["reset_command"]
        command[command.index("--device") + 1] = "emulator-5566"
        with self.assertRaisesRegex(ValueError, "reset command drifted"):
            _repair_p006_payload(payload)

    def test_valid_png_rejects_truncated_capture(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.png"
            truncated = Path(directory) / "truncated.png"
            Image.new("RGB", (8, 8), "white").save(valid)
            payload = valid.read_bytes()
            truncated.write_bytes(payload[: len(payload) // 2])
            self.assertTrue(_valid_png(valid))
            self.assertFalse(_valid_png(truncated))

    @patch("text_input_eval.supplementary.abcdef_android.subprocess.run")
    def test_truncated_video_frame_falls_back_to_live_capture(self, run) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "feedback_window.mp4"
            video.write_bytes(b"video")
            original = [
                {
                    "label": "pre_action",
                    "scheduled_seconds": -0.1,
                    "actual_seconds": -0.1,
                    "state_hash": "before",
                    "xml_path": str(root / "before.xml"),
                    "png_path": str(root / "before.png"),
                    "foreground": {},
                    "evidence_ref": "request:req:pre_action.png",
                },
                {
                    "label": "frame_000",
                    "scheduled_seconds": 0.0,
                    "actual_seconds": 1.0,
                    "state_hash": "after",
                    "xml_path": str(root / "after.xml"),
                    "png_path": str(root / "after.png"),
                    "foreground": {},
                    "evidence_ref": "request:req:frame_000.png",
                },
            ]
            recorder = MagicMock()
            recorder.video_zero_monotonic = 0.0
            recorder.local = video
            recorder.video_duration_seconds = 2.0

            def completed(args, **kwargs):
                if args[0] == "ffprobe":
                    return CompletedProcess(
                        args=args,
                        returncode=0,
                        stdout='{"frames":[{"best_effort_timestamp_time":"1.0"}]}',
                        stderr="",
                    )
                Path(args[-1]).write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
                return CompletedProcess(args=args, returncode=0, stdout="", stderr=b"")

            run.side_effect = completed
            frames, evidence = _replace_delayed_frames_from_video(
                recorder=recorder,
                directory=root,
                frames=original,
                offsets=[0.0],
                action_started=0.0,
                request_id="req",
            )
            self.assertIs(frames, original)
            self.assertEqual(evidence, [])

    def test_p033_payload_routes_date_binding_through_material_picker(self) -> None:
        payload = {
            "operation": "execute_turn",
            "request": {
                "case": {"case_id": "P033"},
                "oracle": {
                    "binding": {
                        _P033_DATE_BINDING: {"descendant-text": "Date of birth"},
                    },
                    "execute_turn": {},
                },
            },
        }
        repaired, applied = _repair_p033_payload(payload)
        self.assertTrue(applied)
        self.assertEqual(
            repaired["request"]["oracle"]["execute_turn"]["input_strategies"][_P033_DATE_BINDING],
            "material_date_picker",
        )
        self.assertNotIn("input_strategies", payload["request"]["oracle"]["execute_turn"])

    def test_p093_payload_uses_only_calibrated_masked_phone_strategy(self) -> None:
        payload = {
            "operation": "execute_turn",
            "request": {
                "case": {"case_id": "P093"},
                "oracle": {
                    "binding": {
                        _P093_PHONE_BINDING: {
                            "package": "com.tickpickllc.ceobrien.tickpick",
                            "class": "android.widget.EditText",
                            "unique": True,
                        },
                    },
                    "execute_turn": {},
                },
            },
        }
        repaired, applied = _repair_p093_payload(payload)
        self.assertTrue(applied)
        self.assertEqual(
            repaired["request"]["oracle"]["execute_turn"]["input_strategies"][_P093_PHONE_BINDING],
            "masked_phone_suffix",
        )
        self.assertNotIn("input_strategies", payload["request"]["oracle"]["execute_turn"])

    def test_p094_payload_replaces_only_unsafe_center_tap(self) -> None:
        payload = {
            "operation": "execute_turn",
            "request": {
                "case": {"case_id": "P094"},
                "oracle": {"execute_turn": {"verify_action": [
                    {"action": "press", "key": "enter"},
                    {
                        "action": "click",
                        "selector": {
                            "package": "com.touchtunes.android",
                            "class": "androidx.compose.ui.platform.k1",
                            "enabled": True,
                            "unique": True,
                        },
                        "if_missing": "stop",
                    },
                ]}},
            },
        }
        repaired, applied = _repair_p094_payload(payload)
        self.assertTrue(applied)
        actions = repaired["request"]["oracle"]["execute_turn"]["verify_action"]
        enter_index = actions.index({"action": "press", "key": "enter"})
        self.assertEqual(
            actions[enter_index - len(_P094_PRE_ENTER_GUARD_ACTIONS):enter_index],
            _P094_PRE_ENTER_GUARD_ACTIONS,
        )
        self.assertEqual(
            actions[enter_index + 1:enter_index + 1 + len(_P094_LOGIN_GUARD_ACTIONS)],
            _P094_LOGIN_GUARD_ACTIONS,
        )
        self.assertEqual(
            actions[-1]["action"],
            "click_from_observed_hierarchy_unless_view_roots_exceed",
        )
        self.assertEqual(
            actions[-1]["max_view_roots"],
            _P094_MAX_TRANSIENT_VIEW_ROOTS,
        )
        self.assertEqual(
            [action["action"] for action in _P094_LOGIN_GUARD_ACTIONS],
            ["stop_if_view_roots_exceed", "stop_if_present", "stop_if_present"],
        )
        self.assertEqual(actions[-1]["selector"], _P094_REPAIRED_SELECTOR)
        self.assertEqual(
            payload["request"]["oracle"]["execute_turn"]["verify_action"][1]["selector"]["class"],
            "androidx.compose.ui.platform.k1",
        )

    def test_p094_payload_repair_is_idempotent(self) -> None:
        payload = {
            "operation": "execute_turn",
            "request": {
                "case": {"case_id": "P094"},
                "oracle": {"execute_turn": {"verify_action": [
                    {"action": "press", "key": "enter"},
                    {
                        "action": "click",
                        "selector": {
                            "package": "com.touchtunes.android",
                            "class": "androidx.compose.ui.platform.k1",
                            "enabled": True,
                            "unique": True,
                        },
                        "if_missing": "stop",
                    },
                ]}},
            },
        }
        repaired, _ = _repair_p094_payload(payload)
        repaired_again, applied = _repair_p094_payload(repaired)
        self.assertTrue(applied)
        self.assertEqual(repaired_again, repaired)

    def test_p094_payload_repair_does_not_touch_other_cases(self) -> None:
        payload = {
            "operation": "execute_turn",
            "request": {"case": {"case_id": "P095"}},
        }
        repaired, applied = _repair_p094_payload(payload)
        self.assertFalse(applied)
        self.assertIs(repaired, payload)

    @patch("text_input_eval.supplementary.abcdef_android_repaired.time.sleep")
    @patch("text_input_eval.supplementary.abcdef_android_repaired.subprocess.run")
    def test_repaired_native_hierarchy_recovers_from_exit_137(self, run, sleep) -> None:
        run.side_effect = [
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=137, stdout="Killed", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="UI hierchary dumped", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="<hierarchy />", stderr=""),
        ]
        self.assertEqual(
            _repaired_adb_uiautomator_hierarchy("emulator-5554"),
            "<hierarchy />",
        )
        sleep.assert_called_once_with(2.0)

    def test_repaired_hierarchy_accepts_system_crash_dialog(self) -> None:
        hierarchy = """<hierarchy><node package="android" class="android.widget.TextView"
            text="App Manager keeps stopping" resource-id="android:id/alertTitle" />
            <node package="android" class="android.widget.Button" text="Close app"
            resource-id="android:id/aerr_close" /></hierarchy>"""
        device = type("Device", (), {
            "dump_hierarchy": lambda self, compressed=False: hierarchy,
            "app_current": lambda self: {
                "package": "io.github.muntashirakon.AppManager",
                "activity": ".crypto.AuthenticationActivity",
                "pid": 123,
            },
        })()
        driver = AndroidDriver("emulator-5560", device=device)
        self.assertEqual(_repaired_hierarchy(driver), hierarchy)
        self.assertEqual(
            driver.hierarchy_source,
            "live_uiautomator2_system_crash_dialog",
        )

    @patch("text_input_eval.supplementary.abcdef_android_repaired.subprocess.run")
    def test_repaired_ime_visibility_uses_system_state(self, run) -> None:
        driver = type("Driver", (), {"serial": "emulator-5554"})()
        run.return_value = CompletedProcess(
            args=[], returncode=0,
            stdout="mInputShown=false mIsInputViewShown=false mShowRequested=false",
            stderr="",
        )
        self.assertFalse(_repaired_ime_visible(driver, "example.app"))
        run.return_value = CompletedProcess(
            args=[], returncode=0,
            stdout="mInputShown=false mIsInputViewShown=true mShowRequested=true",
            stderr="",
        )
        self.assertFalse(_repaired_ime_visible(driver, "example.app"))
        run.return_value = CompletedProcess(
            args=[], returncode=0,
            stdout="mInputShown=true mIsInputViewShown=true mShowRequested=true",
            stderr="",
        )
        self.assertTrue(_repaired_ime_visible(driver, "example.app"))

    def test_bug_observation_treats_app_value_normalization_as_rejection(self) -> None:
        error = RuntimeError(
            "binding post-write value mismatch: expected='a\\nb', observed='a b'"
        )
        self.assertTrue(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "verdict"}, error,
        ))

    def test_bug_observation_treats_unrepresentable_surrogate_as_rejection(self) -> None:
        error = RuntimeError(
            "field: 'utf-8' codec can't encode character '\\ud800' in position 2: "
            "surrogates not allowed"
        )
        self.assertTrue(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "verdict"}, error,
        ))

    def test_bug_observation_treats_unrepresentable_percent_as_rejection(self) -> None:
        error = RuntimeError(
            "field: adb_shell_input does not support percent characters"
        )
        self.assertTrue(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "verdict"}, error,
        ))

    def test_bug_observation_treats_non_ascii_shell_input_as_rejection(self) -> None:
        error = RuntimeError(
            "field: adb_shell_input supports printable ASCII input only"
        )
        self.assertTrue(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "verdict"}, error,
        ))

    def test_bug_observation_treats_shell_text_failure_as_rejection(self) -> None:
        error = RuntimeError("adb_shell_input text command failed")
        self.assertTrue(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "verdict"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"},
            RuntimeError("adb_shell_input command failed"),
        ))

    def test_bug_observation_treats_disappeared_binding_as_rejection(self) -> None:
        error = RuntimeError(
            "it.niedermann.owncloud.notes:id/editContent: {'code': -32002, "
            "'data': \"Selector [packageName='it.niedermann.owncloud.notes', "
            "resourceId='it.niedermann.owncloud.notes:id/editContent']\", "
            "'method': 'wait'}"
        )
        self.assertTrue(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "verdict"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"},
            RuntimeError("unrelated selector transport failure"),
        ))

    def test_bug_observation_treats_direct_uiobject_not_found_as_rejection(self) -> None:
        error = RuntimeError(
            "it.niedermann.owncloud.notes:id/editContent: (-32001, "
            "'androidx.test.uiautomator.UiObjectNotFoundException', "
            "({'resourceId': 'it.niedermann.owncloud.notes:id/editContent'}, 'value'))"
        )
        self.assertTrue(_is_observational_input_rejection(
            {"oracle_role": "feedback_observation_only"}, error,
        ))
        self.assertFalse(_is_observational_input_rejection(
            {"oracle_role": "verdict"}, error,
        ))

    def test_final_non_wait_action_is_the_observation_trigger(self) -> None:
        actions = [
            {"action": "press", "key": "back"},
            {"action": "wait", "seconds": 2},
            {"action": "click", "selector": {"text": "Submit"}},
            {"action": "wait", "seconds": 5},
        ]
        prefix, trigger, trailing = _split_observation_trigger(actions)
        self.assertEqual([row["action"] for row in prefix], ["press", "wait"])
        self.assertEqual(trigger["action"], "click")
        self.assertEqual(trailing, [{"action": "wait", "seconds": 5}])

    def test_wait_only_plan_observes_automatic_validation_immediately(self) -> None:
        actions = [{"action": "wait", "seconds": 2}]
        prefix, trigger, trailing = _split_observation_trigger(actions)
        self.assertEqual(prefix, [])
        self.assertIsNone(trigger)
        self.assertEqual(trailing, actions)

    def test_missing_final_trigger_cannot_pass_a_coarse_page_goal(self) -> None:
        self.assertEqual(
            _enforce_trigger_reached("pass", True, False, None),
            ("fail", True),
        )
        self.assertEqual(
            _enforce_trigger_reached(
                "unknown", True, False, "feedback_observation_only",
            ),
            ("unknown", True),
        )

    @patch("text_input_eval.supplementary.abcdef_android.ScreenRecorder")
    @patch("text_input_eval.supplementary.abcdef_android.build_feedback_artifacts")
    @patch("text_input_eval.supplementary.abcdef_android._capture_conditional_stop_frame")
    @patch("text_input_eval.supplementary.abcdef_android.base.AndroidDriver")
    def test_prefix_guard_stop_never_reenters_hierarchy_capture(
        self, driver_factory, capture_stop, build_feedback, recorder,
    ) -> None:
        driver = MagicMock()
        driver.actions.return_value = False
        driver.last_action_stop = {
            "reason": "view_root_pressure",
            "view_root_count": 3,
            "max_view_roots": 2,
        }
        driver_factory.return_value = driver
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_dir = root / "turn"
            request_dir.mkdir()
            xml = request_dir / "pre_action.xml"
            png = request_dir / "pre_action.png"
            composite = request_dir / "feedback_frames.png"
            observations = request_dir / "ui_observations.json"
            xml.write_text('<hierarchy source="conditional_prefix_stop_receipt" />')
            png.write_bytes(b"png")
            composite.write_bytes(b"composite")
            observations.write_text("{}")
            frame = {
                "label": "pre_action",
                "scheduled_seconds": -0.1,
                "actual_seconds": -0.1,
                "state_hash": "guarded",
                "xml_path": str(xml),
                "png_path": str(png),
                "foreground": {"package": "example.app", "activity": ".Main"},
                "evidence_ref": "request:req-1:pre_action.png",
                "hierarchy_source": "conditional_prefix_stop_receipt",
            }
            capture_stop.return_value = (frame, [])
            build_feedback.return_value = {
                "composite_path": "turn/feedback_frames.png",
                "observation_path": "turn/ui_observations.json",
            }
            result = execute_turn({
                "candidate": {"field": "value"},
                "request": {
                    "request_id": "req-1",
                    "case": {"case_id": "P094"},
                    "trajectory_evidence_root": str(root),
                    "evidence_dir": str(request_dir),
                    "capture_protocol": {
                        "burst_seconds": [0.0, 0.25, 0.5, 1.0, 2.0],
                        "tail_interval_seconds": 1.0,
                        "pre_roll_seconds": 0.5,
                        "record_video": True,
                    },
                    "oracle": {
                        "version": "oracle-v1",
                        "device_serial": "fake",
                        "binding": {"field": {"resource-id": "field"}},
                        "execute_turn": {
                            "observation_seconds": 15,
                            "verify_action": [
                                {"action": "stop_if_view_roots_exceed"},
                                {"action": "click", "selector": {"text": "Submit"}},
                            ],
                        },
                    },
                },
            })
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(result["observation_complete"])
        self.assertFalse(result["observation_trigger"]["trigger_reached"])
        self.assertEqual(result["diagnostics"][0]["receipt"]["view_root_count"], 3)
        recorder.assert_not_called()

    @patch("text_input_eval.supplementary.abcdef_android.base.begin_attempt")
    @patch("text_input_eval.supplementary.abcdef_android.base.recover_turn")
    def test_recovery_loads_frozen_snapshot_only_after_natural_failure(
        self, natural, begin,
    ) -> None:
        natural.side_effect = RuntimeError("target page not recovered")
        begin.return_value = {
            "target_page_verified": True,
            "state_hash": "baseline",
            "evidence": [{"kind": "xml", "path": "attempt_begin.xml"}],
            "foreground": {"package": "example.app", "activity": ".Main"},
        }
        receipt = recover_turn({"execution": {
            "case": {"case_id": "P001"},
            "oracle": {"binding": {"field": {"resource-id": "field"}}},
            "evidence_dir": "/tmp/request",
            "state_after_hash": "after",
            "command_timeout_seconds": 30,
        }})
        self.assertEqual(receipt["recovery_mode"], "snapshot_fallback")
        self.assertTrue(receipt["snapshot_loaded"])
        self.assertTrue(receipt["target_page_verified"])
        begin.assert_called_once()

    @patch("text_input_eval.supplementary.abcdef_android.time.sleep")
    @patch("text_input_eval.supplementary.abcdef_android.base.begin_attempt")
    def test_begin_attempt_retries_transient_uiautomation_startup(
        self, begin, sleep,
    ) -> None:
        begin.side_effect = [RuntimeError("UiAutomation not connected"), {"reset_verified": True}]
        receipt = begin_attempt({"case": {}, "attempt": {}})
        self.assertTrue(receipt["reset_verified"])
        self.assertEqual(receipt["begin_attempt_retry_count"], 1)
        sleep.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
