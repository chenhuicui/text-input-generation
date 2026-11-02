from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from text_input_eval.supplementary.structured_android import (
    AndroidDriver,
    AppInputRejected,
    _adb_foreground_app,
    _adb_uiautomator_hierarchy,
    _binding_xpath,
    _run_contract_command,
    _selector_kwargs,
    app_input_limit_reached,
    accessibility_rendered_value,
    begin_attempt,
    binding_value_matches,
    binding_nodes,
    execute_turn,
    logical_binding_value,
    masked_phone_suffix_matches,
)
from unittest.mock import patch


class FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode
from text_input_eval.supplementary.structured_recovery import recover


FIELD = {"package": "example.app", "class": "android.widget.EditText", "resource-id": "field"}
SUBMIT = {"package": "example.app", "class": "android.widget.Button", "resource-id": "submit"}
MESSAGE = {"package": "example.app", "class": "android.widget.TextView", "resource-id": "message"}
VIRTUAL_FIELD = {
    "package": "example.app", "class": "android.widget.EditText", "resource-id": "virtual",
}


class FakeObject:
    def __init__(self, device: "FakeDevice", selector: dict[str, str]) -> None:
        self.device = device
        self.selector = selector

    @property
    def count(self) -> int:
        return 1 if self.device.has(self.selector) else 0

    def click(self) -> None:
        if self.selector.get("text") == "Blocker":
            self.device.blocker = False
            return
        if (
            self.selector == SUBMIT or self.selector.get("text") == "Submit"
        ) and not self.device.ime_visible:
            self.device.message = True

    def clear_text(self) -> None:
        self.device.text_write_count += 1
        self.device.text = ""

    def set_text(self, value: str) -> None:
        self.device.text_write_count += 1
        self.device.text = value


class FakeDevice:
    def __init__(self) -> None:
        self.text = ""
        self.hint = ""
        self.message = False
        self.ime_visible = False
        self.last_swipe = None
        self.last_click = None
        self.last_press = None
        self.blocker = False
        self.text_write_count = 0

    def has(self, selector: dict[str, str]) -> bool:
        if selector.get("package") == "example.app" and selector.get("text") == "Submit":
            return True
        if selector.get("package") == "example.app" and selector.get("text") == "Blocker":
            return self.blocker
        return selector in (FIELD, SUBMIT) or (selector == MESSAGE and self.message)

    def __call__(self, **kwargs):
        selector = {
            "package": kwargs["packageName"],
        }
        if "className" in kwargs:
            selector["class"] = kwargs["className"]
        if "resourceId" in kwargs:
            selector["resource-id"] = kwargs["resourceId"]
        if "text" in kwargs:
            selector["text"] = kwargs["text"]
        if "description" in kwargs:
            selector["content-desc"] = kwargs["description"]
        return FakeObject(self, selector)

    def dump_hierarchy(self, compressed: bool = False) -> str:
        message = (
            '<node package="example.app" class="android.widget.TextView" resource-id="message" '
            'text="accepted" hint="" content-desc="" />'
            if self.message else ""
        )
        ime = (
            '<node package="com.example.inputmethod" class="android.inputmethodservice.Keyboard" />'
            if self.ime_visible else ""
        )
        return (
            '<hierarchy><node package="example.app" class="android.widget.EditText" '
            f'resource-id="field" text="{self.text or self.hint}" hint="{self.hint}" content-desc="" />'
            '<node package="example.app" class="android.widget.Button" resource-id="submit" '
            'text="Submit" hint="" content-desc="" />'
            f"{message}{ime}</hierarchy>"
        )

    def app_current(self) -> dict:
        return {"package": "example.app", "activity": ".Main"}

    def press(self, key: str) -> None:
        self.last_press = key
        if key == "back":
            self.ime_visible = False
            if hasattr(self, "view_root_count"):
                self.view_root_count = 1

    def send_keys(self, value: str, clear: bool = False) -> None:
        self.text_write_count += 1
        if clear:
            self.text = ""
        self.text += value

    def window_size(self) -> tuple[int, int]:
        return 1080, 2400

    def click(self, x: int, y: int) -> None:
        self.last_click = (x, y)
        if (x, y) == (884, 200):
            self.message = True

    def swipe(self, start_x, start_y, end_x, end_y, duration) -> None:
        self.last_swipe = (start_x, start_y, end_x, end_y, duration)


class BoundedForegroundTests(unittest.TestCase):
    @patch("text_input_eval.supplementary.structured_android.time.sleep")
    def test_binding_write_retries_bounded_local_android_transients(
        self, _sleep,
    ) -> None:
        for error_name in ("InputIMEError", "AdbTimeout"):
            with self.subTest(error_name=error_name):
                transient = type(error_name, (Exception,), {})
                device = FakeDevice()
                calls = 0

                def flaky_set_text(obj, value: str) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise transient(error_name)
                    obj.device.text_write_count += 1
                    obj.device.text = value

                with patch.object(FakeObject, "set_text", new=flaky_set_text):
                    AndroidDriver("fake", device=device).set_binding_text(
                        FIELD, "hello"
                    )

                self.assertEqual(calls, 2)
                self.assertEqual(device.text, "hello")

    @patch(
        "text_input_eval.supplementary.structured_snapshot_reset.synchronize_device_time"
    )
    @patch("text_input_eval.supplementary.structured_android._run_contract_command")
    def test_begin_attempt_verifies_final_clock_after_reset_command(
        self, contract_command, synchronize_time,
    ) -> None:
        command_clock = {
            "requested_epoch_millis": 1,
            "observed_epoch_millis": 2,
            "verified_against_host_millis": 3,
            "absolute_skew_millis": 1,
            "verified": True,
        }
        final_clock = {
            "requested_epoch_millis": 4,
            "observed_epoch_millis": 5,
            "verified_against_host_millis": 6,
            "absolute_skew_millis": 1,
            "verified": True,
        }
        synchronize_time.return_value = final_clock
        contract_command.return_value = {
            "reset_verified": True,
            "reset_id": "reset-v1",
            "remote_baseline": "not_applicable",
            "device_time": command_clock,
            "snapshot_load_strategy": "live",
            "network_isolation": {"mode": "offline"},
            "avd_before_reset": "fixture",
            "avd_switch_required": False,
        }
        oracle = {
            "device_serial": "fake",
            "begin_attempt": {
                "reset_command": ["reset-fixture"],
                "reset_id": "reset-v1",
                "remote_baseline": "not_applicable",
                "initial_assertion": {
                    "mode": "all",
                    "targets": [{"selector": FIELD, "exists": True}],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            receipt = begin_attempt({
                "case": {"case_id": "P000"},
                "attempt": {
                    "oracle": oracle,
                    "evidence_dir": directory,
                },
            }, device=FakeDevice())
        self.assertEqual(receipt["device_time"], final_clock)
        self.assertEqual(receipt["reset_command_device_time"], command_clock)
        synchronize_time.assert_called_once_with("fake")
        self.assertEqual(receipt["snapshot_load_strategy"], "live")
        self.assertEqual(receipt["network_isolation"], {"mode": "offline"})

    @patch("text_input_eval.supplementary.structured_android.subprocess.run")
    def test_contract_command_accepts_final_json_after_helper_log(self, mocked_run) -> None:
        mocked_run.return_value = FakeCompletedProcess(
            "install helper ...\nsuccess\n{\"reset_verified\": true}\n"
        )

        self.assertEqual(
            _run_contract_command(["reset-fixture"], {}, 10),
            {"reset_verified": True},
        )

    @patch("text_input_eval.supplementary.structured_android.subprocess.run")
    def test_adb_foreground_app_uses_bounded_system_state(self, mocked_run) -> None:
        mocked_run.side_effect = [
            FakeCompletedProcess(
                "mCurrentFocus=Window{abc u0 example.app/.MainActivity}\n"
            ),
            FakeCompletedProcess("1234\n"),
        ]

        self.assertEqual(
            _adb_foreground_app("emulator-5554"),
            {"package": "example.app", "activity": ".MainActivity", "pid": 1234},
        )
        self.assertEqual(mocked_run.call_args_list[0].kwargs["timeout"], 15)
        self.assertEqual(mocked_run.call_args_list[1].kwargs["timeout"], 10)

    def test_supplementary_unicode_matches_android_accessibility_rendering(self) -> None:
        self.assertEqual(accessibility_rendered_value("📸 memory"), ".. memory")
        self.assertTrue(binding_value_matches("📸 memory", ".. memory", ""))

    @patch("text_input_eval.supplementary.structured_android.time.sleep")
    @patch("text_input_eval.supplementary.structured_android.subprocess.run")
    def test_adb_foreground_app_retries_transient_missing_focus(
        self, mocked_run, mocked_sleep,
    ) -> None:
        mocked_run.side_effect = [
            FakeCompletedProcess(""),
            FakeCompletedProcess(""),
            FakeCompletedProcess(
                "mCurrentFocus=Window{abc u0 example.app/.MainActivity}\n"
            ),
            FakeCompletedProcess("1234\n"),
        ]

        self.assertEqual(_adb_foreground_app("emulator-5554")["package"], "example.app")
        mocked_sleep.assert_called_once_with(0.5)


class FakeScrollAction:
    def __init__(self, device: "FakeVirtualizedDevice") -> None:
        self.device = device

    def to(self, **kwargs) -> bool:
        if kwargs == {"resourceId": "virtual"}:
            self.device.virtual_visible = True
            return True
        return False


class FakeScrollableObject:
    def __init__(self, device: "FakeVirtualizedDevice") -> None:
        self.count = 1
        self.scroll = FakeScrollAction(device)


class FakeVirtualizedDevice(FakeDevice):
    def __init__(self) -> None:
        super().__init__()
        self.virtual_visible = False

    def has(self, selector: dict[str, str]) -> bool:
        return super().has(selector) or (
            selector == VIRTUAL_FIELD and self.virtual_visible
        )

    def __call__(self, **kwargs):
        if kwargs.get("scrollable") is True:
            return FakeScrollableObject(self)
        return super().__call__(**kwargs)

    def dump_hierarchy(self, compressed: bool = False) -> str:
        value = super().dump_hierarchy(compressed=compressed)
        if not self.virtual_visible:
            return value
        virtual = (
            '<node package="example.app" class="android.widget.EditText" '
            f'resource-id="virtual" text="{self.text}" hint="" content-desc="" />'
        )
        return value.replace("</hierarchy>", virtual + "</hierarchy>")


class FakeTransientSemanticDevice(FakeDevice):
    def __init__(self) -> None:
        super().__init__()
        self.dumps = 0

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dumps += 1
        if self.dumps == 1:
            return '<hierarchy><node package="example.app" class="android.widget.FrameLayout" /></hierarchy>'
        return super().dump_hierarchy(compressed=compressed)


class FakeTransientDumpErrorDevice(FakeDevice):
    def __init__(self) -> None:
        super().__init__()
        self.dumps = 0
        self.resets = 0

    def dump_hierarchy(self, compressed: bool = False) -> str:
        self.dumps += 1
        if self.dumps == 1:
            raise RuntimeError("transient uiautomator instrumentation error")
        return super().dump_hierarchy(compressed=compressed)

    def reset_uiautomator(self) -> None:
        self.resets += 1


class StructuredAndroidTests(unittest.TestCase):
    def test_post_write_matching_allows_only_equivalent_phone_formatting(self) -> None:
        self.assertTrue(binding_value_matches("+14155551234", "+1 415-555-1234", "Phone"))
        self.assertTrue(binding_value_matches("+12025551234", "202-555-1234", "Phone number"))
        self.assertTrue(binding_value_matches("", "Phone", "Phone"))
        self.assertFalse(binding_value_matches("Creative Events Co.", "Creative Events Co. By by", ""))
        self.assertFalse(binding_value_matches("+14155551234", "+1 415-555-9999", "Phone"))
        self.assertFalse(binding_value_matches("+12025551234", "212-555-1234", "Phone number"))

    def test_masked_phone_suffix_requires_explicit_strategy(self) -> None:
        candidate = "138 0000 1111"
        observed = "(380) 000-1111"
        self.assertFalse(binding_value_matches(candidate, observed, "(555) 123-4567"))
        self.assertTrue(masked_phone_suffix_matches(
            candidate, observed, "(555) 123-4567",
        ))
        self.assertFalse(masked_phone_suffix_matches(
            candidate, "(212) 555-9999", "(555) 123-4567",
        ))

    def test_ui_assertion_normalizes_boolean_enabled_attribute(self) -> None:
        device = FakeDevice()
        device.dump_hierarchy = lambda compressed=False: (
            '<hierarchy><node package="example.app" class="android.view.ViewGroup" '
            'content-desc="Save Preferences" enabled="true" /></hierarchy>'
        )
        driver = AndroidDriver("fake", device=device)
        self.assertTrue(driver.assertion({
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "example.app",
                    "class": "android.view.ViewGroup",
                    "content-desc": "Save Preferences",
                    "enabled": True,
                },
                "exists": True,
            }],
        }))

    def test_visible_counter_proves_app_owned_input_truncation(self) -> None:
        root = ET.fromstring(
            '<hierarchy><node text="100/100" content-desc="" /></hierarchy>'
        )
        self.assertTrue(app_input_limit_reached("x" * 101, "x" * 100, "", root))
        self.assertFalse(app_input_limit_reached("x" * 101, "x" * 99, "", root))

    def test_conditional_guarded_tap_only_runs_when_blocker_is_visible(self) -> None:
        device = FakeDevice()
        driver = AndroidDriver("fake", device=device)
        action = {
            "action": "tap_if_present",
            "guard_selector": {"package": "example.app", "text": "Blocker"},
            "package": "example.app",
            "x": 877,
            "y": 763,
            "screen_width": 1080,
            "screen_height": 2400,
        }
        driver.actions([action])
        self.assertIsNone(device.last_click)
        device.blocker = True
        driver.actions([action])
        self.assertEqual(device.last_click, (877, 763))

    def test_conditional_click_only_runs_when_target_is_visible(self) -> None:
        device = FakeDevice()
        driver = AndroidDriver("fake", device=device)
        action = {
            "action": "click_if_present",
            "selector": {"package": "example.app", "text": "Blocker"},
        }
        driver.actions([action])
        self.assertFalse(device.blocker)
        device.blocker = True
        driver.actions([action])
        self.assertFalse(device.blocker)

    def test_conditional_press_only_runs_when_guard_is_visible(self) -> None:
        device = FakeDevice()
        driver = AndroidDriver("fake", device=device)
        action = {
            "action": "press_if_present",
            "selector": {"package": "example.app", "text": "Blocker"},
            "key": "back",
        }
        driver.actions([action])
        self.assertIsNone(device.last_press)
        device.blocker = True
        driver.actions([action])
        self.assertEqual(device.last_press, "back")

    def test_conditional_stop_returns_false_only_when_terminal_state_is_visible(self) -> None:
        device = FakeDevice()
        driver = AndroidDriver("fake", device=device)
        action = {
            "action": "stop_if_present",
            "selector": {"package": "example.app", "text": "Blocker"},
        }
        self.assertTrue(driver.actions([action]))
        device.blocker = True
        self.assertFalse(driver.actions([action]))
        self.assertEqual(
            driver.last_action_stop,
            {
                "reason": "terminal_selector_present",
                "selector": {"package": "example.app", "text": "Blocker"},
            },
        )
        with self.assertRaisesRegex(RuntimeError, "terminal App state"):
            driver.actions([action], stop_is_error=True)

    def test_view_root_pressure_stop_returns_false_without_pressing_back(self) -> None:
        device = FakeDevice()
        device.view_root_count = 3
        driver = AndroidDriver("fake", device=device)
        action = {
            "action": "stop_if_view_roots_exceed",
            "package": "example.app",
            "max_view_roots": 2,
            "timeout_seconds": 0.001,
            "poll_seconds": 0.001,
        }
        self.assertFalse(driver.actions([action]))
        self.assertIsNone(device.last_press)
        self.assertEqual(driver.last_action_stop["reason"], "view_root_pressure")
        self.assertEqual(driver.last_action_stop["view_root_count"], 3)
        with self.assertRaisesRegex(RuntimeError, "view-root pressure"):
            driver.actions([action], stop_is_error=True)

    def test_guarded_click_stops_before_selector_resolution_under_root_pressure(self) -> None:
        device = FakeDevice()
        device.view_root_count = 3
        driver = AndroidDriver("fake", device=device)
        action = {
            "action": "click_unless_view_roots_exceed",
            "selector": SUBMIT,
            "package": "example.app",
            "max_view_roots": 2,
            "if_missing": "stop",
        }
        self.assertFalse(driver.actions([action]))
        self.assertFalse(device.message)
        device.view_root_count = 1
        self.assertTrue(driver.actions([action]))
        self.assertTrue(device.message)

    def test_observed_hierarchy_click_uses_captured_unique_bounds(self) -> None:
        device = FakeDevice()
        device.view_root_count = 1
        driver = AndroidDriver("fake", device=device)
        action = {
            "action": "click_from_observed_hierarchy_unless_view_roots_exceed",
            "selector": SUBMIT,
            "package": "example.app",
            "max_view_roots": 2,
            "if_missing": "stop",
        }
        hierarchy = (
            '<hierarchy><node package="example.app" class="android.widget.Button" '
            'resource-id="submit" bounds="[800,100][968,300]" /></hierarchy>'
        )
        self.assertTrue(driver.actions([action], observed_hierarchy=hierarchy))
        self.assertEqual(device.last_click, (884, 200))
        self.assertTrue(device.message)

        missing = '<hierarchy><node package="example.app" class="android.view.View" /></hierarchy>'
        self.assertFalse(driver.actions([action], observed_hierarchy=missing))
        self.assertEqual(driver.last_action_stop["reason"], "conditional_target_missing")

    def test_view_root_pressure_guard_presses_back_without_hierarchy_polling(self) -> None:
        device = FakeDevice()
        device.view_root_count = 3
        driver = AndroidDriver("fake", device=device)

        driver.actions([{
            "action": "press_back_if_view_roots_exceed",
            "package": "example.app",
            "max_view_roots": 2,
            "timeout_seconds": 1,
            "poll_seconds": 0.01,
        }])

        self.assertEqual(device.last_press, "back")

    def test_android_accessibility_edit_text_wrapper_exposes_only_logical_value(self) -> None:
        suffix = "insertion at end of text.Postal Codeedit box.Edit box, double tap to edit text, double tap to long press."
        self.assertEqual(logical_binding_value(suffix, ""), "")
        self.assertEqual(logical_binding_value(f"10001, {suffix}", ""), "10001")
        self.assertTrue(binding_value_matches("10001", f"10001, {suffix}", ""))
        self.assertFalse(binding_value_matches("Morgan", "Morgan extra text", ""))

    def test_native_hierarchy_fallback_reads_dumped_xml(self) -> None:
        xml = '<hierarchy><node package="example.app" text="Unable to sign in" /></hierarchy>'
        completed = type("Completed", (), {"returncode": 0, "stdout": xml, "stderr": ""})
        with patch("text_input_eval.supplementary.structured_android.subprocess.run", return_value=completed) as run:
            self.assertEqual(_adb_uiautomator_hierarchy("emulator-5554"), xml)
        self.assertEqual(run.call_count, 2)

    def test_hierarchy_retries_transient_empty_activity_before_native_fallback(self) -> None:
        device = FakeTransientSemanticDevice()
        driver = AndroidDriver("fake", device=device)
        with patch("text_input_eval.supplementary.structured_android._adb_uiautomator_hierarchy") as fallback:
            hierarchy = driver.hierarchy()
        self.assertIn('resource-id="field"', hierarchy)
        self.assertEqual(device.dumps, 2)
        fallback.assert_not_called()

    @patch("text_input_eval.supplementary.structured_android.time.sleep")
    def test_hierarchy_restarts_uiautomator_after_transient_dump_error(
        self, mocked_sleep,
    ) -> None:
        device = FakeTransientDumpErrorDevice()
        driver = AndroidDriver("fake", device=device)

        hierarchy = driver.hierarchy()

        self.assertIn('resource-id="field"', hierarchy)
        self.assertEqual(device.dumps, 2)
        self.assertEqual(device.resets, 1)
        mocked_sleep.assert_called_once_with(1)

    def test_action_target_is_relocated_after_hiding_ime(self) -> None:
        device = FakeDevice()
        device.ime_visible = True
        driver = AndroidDriver("fake", device=device)
        self.assertEqual(driver.serial, "fake")
        driver.actions([{"action": "click", "selector": SUBMIT}])
        self.assertFalse(device.ime_visible)
        self.assertTrue(device.message)

    def test_virtualized_binding_is_relocated_by_stable_selector(self) -> None:
        device = FakeVirtualizedDevice()
        driver = AndroidDriver("fake", device=device)
        obj = driver.binding_object(VIRTUAL_FIELD)
        self.assertIsNotNone(obj)
        self.assertTrue(device.virtual_visible)
        self.assertTrue(driver.binding_assertion({
            "mode": "all",
            "targets": [{
                "selector": VIRTUAL_FIELD,
                "exists": True,
                "attribute": "text",
                "equals": "",
            }],
        }))

    def test_dynamic_fill_action_goal_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            oracle = {
                "device_serial": "fake",
                "binding": {"field": FIELD},
                "execute_turn": {
                    "verify_action": [{"action": "click", "selector": SUBMIT}],
                    "observation_seconds": 0.01,
                    "poll_seconds": 0.001,
                },
                "goal_assertion": {"mode": "all", "targets": [{"selector": MESSAGE, "exists": True}]},
                "diagnostic_targets": [{"id": "message", "selector": MESSAGE, "attribute": "text"}],
            }
            payload = {
                "candidate": {"field": "hello"},
                "request": {
                    "request_id": "request-1",
                    "case": {"mirror": {"device": {"serial": "fake"}}},
                    "oracle": oracle,
                    "evidence_dir": directory,
                },
            }
            result = execute_turn(payload, device=FakeDevice())
            self.assertEqual(result["verdict"], "pass")
            self.assertTrue(result["observation_complete"])
            self.assertTrue((Path(directory) / "before.xml").is_file())
            self.assertTrue((Path(directory) / "after.xml").is_file())
            self.assertEqual(result["diagnostics"][0]["detail"], "accepted")

    def test_if_missing_stop_returns_complete_fail_without_goal_polling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            oracle = {
                "device_serial": "fake",
                "binding": {"field": FIELD},
                "execute_turn": {
                    "verify_action": [{
                        "action": "click",
                        "selector": {
                            "package": "example.app",
                            "text": "Missing result",
                        },
                        "if_missing": "stop",
                    }],
                    "observation_seconds": 30,
                    "poll_seconds": 1,
                },
                "goal_assertion": {
                    "mode": "all",
                    "targets": [{"selector": MESSAGE, "exists": True}],
                },
                "diagnostic_targets": [],
            }
            with patch.object(AndroidDriver, "assertion") as assertion:
                result = execute_turn({
                    "candidate": {"field": "hello"},
                    "request": {
                        "request_id": "request-action-stopped",
                        "case": {"mirror": {"device": {"serial": "fake"}}},
                        "oracle": oracle,
                        "evidence_dir": directory,
                    },
                }, device=FakeDevice())

            assertion.assert_not_called()
            self.assertEqual(result["verdict"], "fail")
            self.assertTrue(result["observation_complete"])
            self.assertEqual(
                result["diagnostics"][-1]["id"],
                "conditional_action_target_missing",
            )
            self.assertTrue((Path(directory) / "action_stopped.xml").is_file())

    def test_candidate_binding_goal_rejects_a_different_loaded_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            oracle = {
                "device_serial": "fake",
                "binding": {"field": FIELD},
                "execute_turn": {
                    "verify_action": [{"action": "click", "selector": SUBMIT}],
                    "candidate_binding_goal": True,
                    "observation_seconds": 0.01,
                    "poll_seconds": 0.001,
                },
                "goal_assertion": {
                    "mode": "all",
                    "targets": [{
                        "selector": FIELD,
                        "exists": True,
                        "attribute": "text_non_empty",
                        "equals": True,
                    }],
                },
                "diagnostic_targets": [],
            }
            device = FakeDevice()
            with patch.object(AndroidDriver, "candidate_bindings_match", return_value=False):
                result = execute_turn({
                    "candidate": {"field": "hello"},
                    "request": {
                        "request_id": "request-candidate-binding",
                        "case": {"mirror": {"device": {"serial": "fake"}}},
                        "oracle": oracle,
                        "evidence_dir": directory,
                    },
                }, device=device)
            self.assertEqual(result["verdict"], "fail")
            self.assertTrue(result["observation_complete"])

    def test_candidate_text_goal_requires_exact_app_owned_text(self) -> None:
        device = FakeDevice()
        device.text = "Jamie_AudifyFan"
        driver = AndroidDriver("fake", device=device)
        self.assertTrue(driver.candidate_texts_visible(
            "example.app", {"field": "Jamie_AudifyFan"},
        ))
        self.assertFalse(driver.candidate_texts_visible(
            "example.app", {"field": "Jamie"},
        ))

    def test_calibrated_app_input_rejection_is_a_complete_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            oracle = {
                "device_serial": "fake",
                "binding": {"field": FIELD},
                "execute_turn": {
                    "verify_action": [{"action": "click", "selector": SUBMIT}],
                    "input_strategies": {"field": "adb_key_events"},
                    "input_rejection_verdict": "fail",
                    "observation_seconds": 0.01,
                    "poll_seconds": 0.001,
                },
                "goal_assertion": {
                    "mode": "all",
                    "targets": [{"selector": MESSAGE, "exists": True}],
                },
                "diagnostic_targets": [],
            }
            payload = {
                "candidate": {"field": "abc"},
                "request": {
                    "request_id": "request-rejected",
                    "case": {"mirror": {"device": {"serial": "fake"}}},
                    "oracle": oracle,
                    "evidence_dir": directory,
                },
            }
            with patch.object(
                AndroidDriver,
                "set_binding_text_adb_key_events",
                side_effect=AppInputRejected("App refused the value"),
            ):
                result = execute_turn(payload, device=FakeDevice())

            self.assertEqual(result["verdict"], "fail")
            self.assertTrue(result["observation_complete"])
            self.assertEqual(result["diagnostics"][0]["id"], "app_input_rejected")

    def test_calibrated_input_rejection_overrides_a_coarse_success_indicator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            oracle = {
                "device_serial": "fake",
                "binding": {"field": FIELD},
                "execute_turn": {
                    "verify_action": [{"action": "click", "selector": SUBMIT}],
                    "input_rejection_verdict": "fail",
                    "observation_seconds": 0.01,
                    "poll_seconds": 0.001,
                },
                "goal_assertion": {
                    "mode": "all",
                    "targets": [{"selector": MESSAGE, "exists": True}],
                },
                "diagnostic_targets": [],
            }
            device = FakeDevice()
            device.message = True
            with patch.object(
                AndroidDriver,
                "set_binding_text",
                side_effect=AppInputRejected("read-only field refused the value"),
            ):
                result = execute_turn({
                    "candidate": {"field": "abc"},
                    "request": {
                        "request_id": "request-coarse-goal",
                        "case": {"mirror": {"device": {"serial": "fake"}}},
                        "oracle": oracle,
                        "evidence_dir": directory,
                    },
                }, device=device)
            self.assertEqual(result["verdict"], "fail")
            self.assertTrue(result["observation_complete"])

    def test_coordinate_or_unknown_action_is_rejected_as_infrastructure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            oracle = {
                "device_serial": "fake",
                "binding": {"field": FIELD},
                "execute_turn": {
                    "verify_action": [{"action": "tap", "x": 10, "y": 20}],
                    "observation_seconds": 0.01,
                    "poll_seconds": 0.001,
                },
                "goal_assertion": {"mode": "all", "targets": [{"selector": MESSAGE, "exists": True}]},
                "diagnostic_targets": [],
            }
            result = execute_turn({
                "candidate": {"field": "hello"},
                "request": {
                    "request_id": "request-1",
                    "case": {"mirror": {"device": {"serial": "fake"}}},
                    "oracle": oracle,
                    "evidence_dir": directory,
                },
            }, device=FakeDevice())
            self.assertEqual(result["verdict"], "infrastructure_error")
            self.assertFalse(result["observation_complete"])

    def test_guarded_webview_tap_requires_frozen_package_and_screen(self) -> None:
        device = FakeDevice()
        driver = AndroidDriver("fake", device=device)
        driver.actions([{
            "action": "tap",
            "package": "example.app",
            "x": 884,
            "y": 200,
            "screen_width": 1080,
            "screen_height": 2400,
        }])
        self.assertTrue(device.message)
        with self.assertRaisesRegex(RuntimeError, "screen dimensions"):
            driver.actions([{
                "action": "tap",
                "package": "example.app",
                "x": 884,
                "y": 200,
                "screen_width": 720,
                "screen_height": 1280,
            }])

    def test_prepare_set_text_uses_verified_binding_write(self) -> None:
        device = FakeDevice()
        device.text = "frozen baseline"
        driver = AndroidDriver("fake", device=device)

        driver.actions([{
            "action": "set_text",
            "selector": FIELD,
            "value": "",
        }], stop_is_error=True)

        self.assertEqual(device.text, "")
        with self.assertRaisesRegex(ValueError, "string value"):
            driver.actions([{
                "action": "set_text",
                "selector": FIELD,
                "value": 7,
            }])

    def test_adb_key_event_input_strategy_is_explicit_and_binding_scoped(self) -> None:
        device = FakeDevice()
        driver = AndroidDriver("fake", device=device)

        driver.fill(
            {"field": FIELD}, {"field": "16502530000"},
            {"field": "adb_key_events"},
        )

        self.assertEqual(device.text, "16502530000")
        with self.assertRaisesRegex(ValueError, "frozen bindings"):
            driver.fill(
                {"field": FIELD}, {"field": "123"},
                {"other": "adb_key_events"},
            )
        with self.assertRaisesRegex(ValueError, "unsupported input strategy"):
            driver.fill(
                {"field": FIELD}, {"field": "123"},
                {"field": "clipboard"},
            )

    def test_guarded_swipe_requires_frozen_package_and_screen(self) -> None:
        device = FakeDevice()
        driver = AndroidDriver("fake", device=device)
        driver.actions([{
            "action": "swipe",
            "package": "example.app",
            "start_x": 350,
            "start_y": 900,
            "end_x": 700,
            "end_y": 1400,
            "duration_seconds": 0.8,
            "screen_width": 1080,
            "screen_height": 2400,
        }])
        self.assertEqual(device.last_swipe, (350, 900, 700, 1400, 0.8))
        with self.assertRaisesRegex(RuntimeError, "foreground package"):
            driver.actions([{
                "action": "swipe",
                "package": "wrong.app",
                "start_x": 350,
                "start_y": 900,
                "end_x": 700,
                "end_y": 1400,
                "duration_seconds": 0.8,
                "screen_width": 1080,
                "screen_height": 2400,
            }])

    def test_exact_text_action_and_assertion_do_not_require_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            oracle = {
                "device_serial": "fake",
                "binding": {"field": FIELD},
                "execute_turn": {
                    "verify_action": [{"action": "click", "selector": {"package": "example.app", "text": "Submit"}}],
                    "observation_seconds": 0.01,
                    "poll_seconds": 0.001,
                },
                "goal_assertion": {
                    "mode": "all",
                    "targets": [{"selector": {"package": "example.app", "text": "accepted"}, "exists": True}],
                },
                "diagnostic_targets": [],
            }
            result = execute_turn({
                "candidate": {"field": "hello"},
                "request": {
                    "request_id": "request-text",
                    "case": {"mirror": {"device": {"serial": "fake"}}},
                    "oracle": oracle,
                    "evidence_dir": directory,
                },
            }, device=FakeDevice())
            self.assertEqual(result["verdict"], "pass")

    def test_generic_recovery_clears_inputs_without_reset(self) -> None:
        device = FakeDevice()
        device.text = "failed candidate"
        oracle = {
            "device_serial": "fake",
            "binding": {"field": FIELD},
            "recover_turn": {
                "target_page_assertion": {
                    "mode": "all", "targets": [{"selector": FIELD, "exists": True}],
                },
                "input_clear_assertion": {
                    "mode": "all",
                    "targets": [{
                        "selector": FIELD, "exists": True, "attribute": "text", "equals": "",
                    }],
                },
            },
        }
        result = recover({"execution": {
            "case": {"mirror": {"device": {"serial": "fake"}}},
            "oracle": oracle,
        }}, 3, device=device)
        self.assertEqual(device.text, "")
        self.assertEqual(result["back_presses"], 0)
        self.assertFalse(result["snapshot_loaded"])
        self.assertFalse(result["remote_reset"])

    def test_generic_recovery_uses_explicit_restore_values(self) -> None:
        device = FakeDevice()
        device.text = "failed candidate"
        oracle = {
            "device_serial": "fake",
            "binding": {"field": FIELD},
            "recover_turn": {
                "target_page_assertion": {
                    "mode": "all", "targets": [{"selector": FIELD, "exists": True}],
                },
                "input_clear_assertion": {
                    "mode": "all",
                    "targets": [{
                        "selector": FIELD, "exists": True,
                        "attribute": "text", "equals": "restored",
                    }],
                },
            },
        }
        result = recover({"execution": {
            "case": {"mirror": {"device": {"serial": "fake"}}},
            "oracle": oracle,
        }}, 3, recovery_values={"field": "restored"}, device=device)
        self.assertEqual(device.text, "restored")
        self.assertEqual(result["input_restore_values"], {"field": "restored"})

    def test_generic_recovery_does_not_rewrite_satisfied_baseline(self) -> None:
        device = FakeDevice()
        oracle = {
            "device_serial": "fake",
            "binding": {"field": FIELD},
            "recover_turn": {
                "target_page_assertion": {
                    "mode": "all", "targets": [{"selector": FIELD, "exists": True}],
                },
                "input_clear_assertion": {
                    "mode": "all",
                    "targets": [{
                        "selector": FIELD, "exists": True, "attribute": "text", "equals": "",
                    }],
                },
            },
        }

        recover({"execution": {
            "case": {"mirror": {"device": {"serial": "fake"}}},
            "oracle": oracle,
        }}, 3, device=device)

        self.assertEqual(device.text_write_count, 0)

    def test_empty_input_may_be_exposed_as_its_hint(self) -> None:
        device = FakeDevice()
        device.hint = "Enter a value"
        driver = AndroidDriver("fake", device=device)
        self.assertTrue(driver.assertion({
            "mode": "all",
            "targets": [{
                "selector": FIELD,
                "exists": True,
                "attribute": "text_equals_hint",
                "equals": True,
            }],
        }))

    def test_explicit_value_equal_to_hint_is_a_successful_write(self) -> None:
        self.assertTrue(binding_value_matches("0", "0", "0"))

    def test_binding_selector_supports_enabled_instance_and_unique_contract(self) -> None:
        selector = {
            "package": "example.app",
            "class": "android.widget.EditText",
            "enabled": True,
            "instance": 1,
            "unique": True,
        }
        self.assertEqual(_selector_kwargs(selector), {
            "packageName": "example.app",
            "className": "android.widget.EditText",
            "enabled": True,
            "instance": 1,
        })
        root = ET.fromstring(
            '<hierarchy>'
            '<node package="example.app" class="android.widget.EditText" enabled="true" />'
            '<node package="example.app" class="android.widget.EditText" enabled="true" />'
            '</hierarchy>'
        )
        self.assertEqual(len(binding_nodes(root, selector)), 1)
        self.assertIs(binding_nodes(root, selector)[0], list(root)[1])

    def test_binding_xpath_supports_ancestor_and_descendant_contracts(self) -> None:
        ancestor = {
            "package": "example.app",
            "class": "android.widget.EditText",
            "resource-id": "input",
            "ancestor-resource-id": "first_name",
        }
        descendant = {
            "package": "example.app",
            "class": "android.widget.EditText",
            "descendant-text": "Tracking number",
        }
        xml = ET.fromstring(
            '<hierarchy>'
            '<node package="example.app" class="android.widget.LinearLayout" resource-id="first_name">'
            '<node package="example.app" class="android.widget.EditText" resource-id="input" enabled="true" />'
            '</node>'
            '<node package="example.app" class="android.widget.EditText" enabled="true">'
            '<node package="example.app" class="android.widget.TextView" text="Tracking number" />'
            '</node>'
            '</hierarchy>'
        )
        self.assertIn("ancestor::*[@resource-id='first_name']", _binding_xpath(ancestor))
        self.assertIn(".//*[@text='Tracking number']", _binding_xpath(descendant))
        self.assertEqual(len(binding_nodes(xml, ancestor)), 1)
        self.assertEqual(len(binding_nodes(xml, descendant)), 1)

    def test_binding_selector_supports_resource_id_prefix(self) -> None:
        selector = {
            "package": "com.touchtunes.android",
            "class": "android.view.View",
            "resource-id-prefix": "search_list_item_",
            "enabled": True,
            "instance": 0,
        }
        root = ET.fromstring(
            '<hierarchy>'
            '<node package="com.touchtunes.android" class="androidx.compose.ui.platform.k1" '
            'enabled="true" bounds="[0,0][1080,2400]" />'
            '<node package="com.touchtunes.android" class="android.view.View" '
            'resource-id="search_list_item_1136" enabled="true" />'
            '<node package="com.touchtunes.android" class="android.view.View" '
            'resource-id="search_list_item_80271" enabled="true" />'
            '</hierarchy>'
        )
        self.assertEqual(_selector_kwargs(selector), {
            "packageName": "com.touchtunes.android",
            "className": "android.view.View",
            "enabled": True,
            "instance": 0,
        })
        self.assertIn(
            "starts-with(@resource-id, 'search_list_item_')",
            _binding_xpath(selector),
        )
        nodes = binding_nodes(root, selector)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].get("resource-id"), "search_list_item_1136")

    def test_resource_id_prefix_does_not_match_full_screen_compose_root(self) -> None:
        selector = {
            "package": "com.touchtunes.android",
            "class": "android.view.View",
            "resource-id-prefix": "search_list_item_",
            "enabled": True,
            "instance": 0,
        }
        root = ET.fromstring(
            '<hierarchy><node package="com.touchtunes.android" '
            'class="androidx.compose.ui.platform.k1" enabled="true" '
            'bounds="[0,0][1080,2400]" /></hierarchy>'
        )
        self.assertEqual(binding_nodes(root, selector), [])


if __name__ == "__main__":
    unittest.main()
