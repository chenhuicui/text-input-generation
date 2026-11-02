from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from text_input_eval.supplementary.structured_live_controls import (
    baseline_restore_contract,
    wait_negative_observation,
)


class StructuredLiveControlsTests(unittest.TestCase):
    def test_wait_negative_observation_allows_async_negative_endpoint(self) -> None:
        goal = {"kind": "goal"}
        negative = {"kind": "negative"}

        class Driver:
            negative_values = iter([False, True])

            def assertion(self, value: dict[str, str]) -> bool:
                if value is goal:
                    return False
                return next(self.negative_values)

        with (
            patch(
                "text_input_eval.supplementary.structured_live_controls.time.monotonic",
                side_effect=[0.0, 0.1, 1.0],
            ),
            patch("text_input_eval.supplementary.structured_live_controls.time.sleep"),
        ):
            self.assertTrue(
                wait_negative_observation(Driver(), goal, negative, 0.5)
            )

    def test_wait_negative_observation_requires_endpoint_before_deadline(self) -> None:
        goal = {"kind": "goal"}
        negative = {"kind": "negative"}

        class Driver:
            def assertion(self, value: dict[str, str]) -> bool:
                return False

        with patch(
            "text_input_eval.supplementary.structured_live_controls.time.monotonic",
            side_effect=[0.0, 1.0],
        ):
            self.assertFalse(
                wait_negative_observation(Driver(), goal, negative, 0.5)
            )

    def test_baseline_restore_contract_preserves_prefilled_values_and_hints(self) -> None:
        bindings = {
            "title": {
                "package": "example.app", "resource-id": "title",
                "class": "android.widget.EditText",
            },
            "description": {
                "package": "example.app", "resource-id": "description",
                "class": "android.widget.EditText",
            },
        }
        xml = (
            '<hierarchy><node package="example.app" resource-id="title" '
            'class="android.widget.EditText" text="My playlist #7" hint="Title" />'
            '<node package="example.app" resource-id="description" '
            'class="android.widget.EditText" text="Description" hint="Description" />'
            '</hierarchy>'
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.xml"
            path.write_text(xml, encoding="utf-8")
            assertion, values = baseline_restore_contract(bindings, path)

        self.assertEqual(values, {"title": "My playlist #7", "description": ""})
        self.assertEqual(
            assertion["targets"][0],
            {
                "selector": bindings["title"],
                "exists": True,
                "attribute": "text",
                "equals": "My playlist #7",
            },
        )
        self.assertEqual(
            assertion["targets"][1],
            {
                "selector": bindings["description"],
                "exists": True,
                "attribute": "text",
                "equals": "",
            },
        )


if __name__ == "__main__":
    unittest.main()
