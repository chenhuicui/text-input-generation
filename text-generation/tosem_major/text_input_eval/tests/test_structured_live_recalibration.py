from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.structured_live_recalibration import (
    revised_observation_seconds,
    translate_and_execute,
)


class _Driver:
    def __init__(self) -> None:
        self.executed: list[list[dict]] = []

    def actions(self, actions: list[dict], *, stop_is_error: bool) -> bool:
        self.executed.append(actions)
        return True

    def hierarchy(self) -> str:
        return "<hierarchy/>"


class StructuredLiveRecalibrationTests(unittest.TestCase):
    def test_preserves_conditional_press(self) -> None:
        driver = _Driver()
        action = {
            "action": "press_if_present",
            "selector": {"package": "ime", "resource-id": "keyboard"},
            "key": "back",
        }
        with tempfile.TemporaryDirectory() as temporary:
            frozen, steps = translate_and_execute(
                driver, "example.app", [action], Path(temporary),
            )
        self.assertEqual(frozen, [action])
        self.assertEqual(driver.executed, [[action]])
        self.assertEqual(steps[0]["frozen"], action)

    def test_short_observation_requires_explicit_transient_contract(self) -> None:
        self.assertEqual(revised_observation_seconds({"observation_seconds": 0.25}), 5)
        self.assertEqual(revised_observation_seconds({
            "observation_seconds": 0.25,
            "time_sensitive_local_observation": True,
        }), 0.25)


if __name__ == "__main__":
    unittest.main()
