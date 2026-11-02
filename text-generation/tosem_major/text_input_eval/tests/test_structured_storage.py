from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.structured_storage import StructuredLedger, UncertainCallError


class StructuredStorageTests(unittest.TestCase):
    def test_reserved_external_call_is_never_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            first = StructuredLedger(path, {"protocol": "test"})
            first.reserve_call("call-1", "model_generation", {"turn": 1}, {"id": ""})
            first.close()
            second = StructuredLedger(path, {"protocol": "test"})
            with self.assertRaisesRegex(UncertainCallError, "reserved_calls"):
                second.ensure_resumable()
            with self.assertRaisesRegex(UncertainCallError, "cannot be replayed"):
                second.reserve_call("call-1", "model_generation", {"turn": 1}, {"id": ""})
            second.close()

    def test_completed_call_is_reused_only_for_identical_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = StructuredLedger(Path(directory) / "ledger.sqlite3", {"protocol": "test"})
            self.assertIsNone(ledger.reserve_call("call-1", "model_generation", {"turn": 1}, {"id": ""}))
            ledger.finish_call("call-1", {"run_status": "ok"})
            self.assertEqual(
                ledger.reserve_call("call-1", "model_generation", {"turn": 1}, {"id": ""}),
                {"run_status": "ok"},
            )
            with self.assertRaisesRegex(ValueError, "identity drift"):
                ledger.reserve_call("call-1", "model_generation", {"turn": 2}, {"id": ""})
            ledger.close()


if __name__ == "__main__":
    unittest.main()
