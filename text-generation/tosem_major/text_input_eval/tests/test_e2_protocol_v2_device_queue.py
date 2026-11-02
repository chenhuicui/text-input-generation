from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from text_input_eval.supplementary.structured_storage import StructuredLedger


ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = (
    ROOT
    / "output/e2_abcdef_v1_20261002_formal_doubao_parallel_v1"
    / "run_protocol_v2_device_queue.py"
)
SPEC = importlib.util.spec_from_file_location("e2_protocol_v2_device_queue", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class DeviceAttentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_formal_root = RUNNER.FORMAL_ROOT
        RUNNER.FORMAL_ROOT = Path(self.temporary.name)

    def tearDown(self) -> None:
        RUNNER.FORMAL_ROOT = self.original_formal_root
        self.temporary.cleanup()

    @staticmethod
    def payload(serial: str, case_id: str, timestamp: str) -> dict[str, object]:
        return {
            "schema_version": "e2-abcdef-v2-attention-required-1",
            "created_at_utc": timestamp,
            "quota_exhausted": False,
            "model": "Doubao-Seed-2.0-pro",
            "case_id": case_id,
            "serial": serial,
            "claim": f"/claims/{serial}/{case_id}",
            "returncode": 1,
        }

    def test_concurrent_device_incidents_are_append_only_and_device_local(self) -> None:
        first = RUNNER.record_attention(self.payload(
            "emulator-5560", "B002", "2026-10-04T12:20:10.000001+00:00",
        ))
        first_bytes = first.read_bytes()
        second = RUNNER.record_attention(self.payload(
            "emulator-5556", "B023", "2026-10-04T13:10:47.000002+00:00",
        ))

        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertTrue(second.is_file())
        self.assertTrue(RUNNER.has_attention("emulator-5560"))
        self.assertTrue(RUNNER.has_attention("emulator-5556"))
        self.assertFalse(RUNNER.has_attention("emulator-5554"))
        global_summary = json.loads(
            (RUNNER.FORMAL_ROOT / "attention_required.json").read_text()
        )
        self.assertEqual(global_summary["serial"], "emulator-5556")

        RUNNER.active_attention_path("emulator-5560").unlink()
        self.assertFalse(RUNNER.has_attention("emulator-5560"))
        self.assertTrue(RUNNER.has_attention("emulator-5556"))

    def test_append_only_collision_fails_closed(self) -> None:
        payload = self.payload(
            "emulator-5560", "B002", "2026-10-04T12:20:10.000001+00:00",
        )
        RUNNER.record_attention(payload)
        with self.assertRaises(FileExistsError):
            RUNNER.record_attention(payload)

    def test_legacy_global_pointer_only_blocks_matching_device(self) -> None:
        RUNNER.atomic_json(
            RUNNER.FORMAL_ROOT / "attention_required.json",
            self.payload(
                "emulator-5556", "B023", "2026-10-04T13:10:47.000002+00:00",
            ),
        )
        self.assertTrue(RUNNER.has_attention("emulator-5556"))
        self.assertFalse(RUNNER.has_attention("emulator-5560"))

    def test_nonquota_infrastructure_pause_is_global(self) -> None:
        self.assertFalse(RUNNER.infrastructure_paused())
        RUNNER.atomic_json(
            RUNNER.FORMAL_ROOT / "infrastructure_pause.json",
            {"reason": "mysql_opening_tables_stall", "quota_exhausted": False},
        )
        self.assertTrue(RUNNER.infrastructure_paused())


class GlobalCaseClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_formal_root = RUNNER.FORMAL_ROOT
        RUNNER.FORMAL_ROOT = Path(self.temporary.name)
        self.schedule = RUNNER.FORMAL_ROOT / "schedule.json"
        self.schedule.write_text(
            json.dumps({"protocol_id": "E2-abcdef-v2"}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        RUNNER.FORMAL_ROOT = self.original_formal_root
        self.temporary.cleanup()

    def test_atomic_global_claim_allows_exactly_one_device(self) -> None:
        first = RUNNER.acquire_global_claim(
            "emulator-5554", "P098", RUNNER.FORMAL_ROOT / "first", self.schedule,
        )
        second = RUNNER.acquire_global_claim(
            "emulator-5564", "P098", RUNNER.FORMAL_ROOT / "second", self.schedule,
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        stored = json.loads(RUNNER.global_claim_path("P098").read_text())
        self.assertEqual(stored["serial"], "emulator-5554")
        self.assertEqual(stored["status"], "claimed")
        self.assertTrue(stored["result_blind"])
        self.assertNotIn("result", stored)

    def test_completed_cells_are_visible_to_every_queue(self) -> None:
        RUNNER.acquire_global_claim(
            "emulator-5564", "B037", RUNNER.FORMAL_ROOT / "claim", self.schedule,
        )
        RUNNER.update_global_claim("B037", status="completed_accepted")
        for arm in RUNNER.ARMS:
            RUNNER.atomic_json(
                RUNNER.completion_record_path("B037", arm),
                {"status": "completed_accepted", "case_id": "B037", "arm": arm},
            )
        self.assertTrue(RUNNER.completed_case("emulator-5554", "B037"))
        self.assertTrue(RUNNER.completed_case("emulator-5560", "B037"))

    def test_global_claim_alone_is_not_completion_authority(self) -> None:
        RUNNER.acquire_global_claim(
            "emulator-5564", "B037", RUNNER.FORMAL_ROOT / "claim", self.schedule,
        )
        RUNNER.update_global_claim("B037", status="completed_accepted")
        self.assertFalse(RUNNER.completed_case("emulator-5554", "B037"))

    def test_retry_required_claim_is_reclaimed_with_immutable_history(self) -> None:
        first = RUNNER.acquire_global_claim(
            "emulator-5554", "B004", RUNNER.FORMAL_ROOT / "first", self.schedule,
        )
        self.assertIsNotNone(first)
        RUNNER.update_global_claim(
            "B004", status="retry_required", completed_arms=["A", "B", "C"],
        )

        second = RUNNER.acquire_global_claim(
            "emulator-5560", "B004", RUNNER.FORMAL_ROOT / "second", self.schedule,
        )

        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second["serial"], "emulator-5560")
        self.assertEqual(second["reclaimed_from_status"], "retry_required")
        history = Path(str(second["previous_claim_history"]))
        self.assertTrue(history.is_file())
        saved = json.loads(history.read_text())
        self.assertEqual(saved["serial"], "emulator-5554")
        self.assertEqual(saved["completed_arms"], ["A", "B", "C"])

    def test_running_claim_cannot_be_stolen(self) -> None:
        RUNNER.acquire_global_claim(
            "emulator-5554", "B014", RUNNER.FORMAL_ROOT / "first", self.schedule,
        )
        RUNNER.update_global_claim("B014", status="running", pid=12345)
        self.assertIsNone(RUNNER.acquire_global_claim(
            "emulator-5560", "B014", RUNNER.FORMAL_ROOT / "second", self.schedule,
        ))

    def test_attention_claim_is_skipped_until_explicit_resolution(self) -> None:
        RUNNER.acquire_global_claim(
            "emulator-5554", "B017", RUNNER.FORMAL_ROOT / "first", self.schedule,
        )
        RUNNER.update_global_claim(
            "B017", status="attention_required", reason="missing_case_evidence",
        )

        self.assertIsNone(RUNNER.acquire_global_claim(
            "emulator-5568", "B017", RUNNER.FORMAL_ROOT / "second", self.schedule,
        ))
        stored = json.loads(RUNNER.global_claim_path("B017").read_text())
        self.assertEqual(stored["serial"], "emulator-5554")
        self.assertEqual(stored["status"], "attention_required")
        self.assertEqual(stored["reason"], "missing_case_evidence")


class CaseSerialAffinityTests(unittest.TestCase):
    def test_p001_is_restricted_to_qualified_clone_serial(self) -> None:
        self.assertTrue(RUNNER.case_serial_compatible("emulator-5566", "P001"))
        for serial in (
            "emulator-5554", "emulator-5556", "emulator-5560",
            "emulator-5564", "emulator-5568",
        ):
            self.assertFalse(RUNNER.case_serial_compatible(serial, "P001"))

    def test_other_cases_remain_work_stealable(self) -> None:
        for serial in (
            "emulator-5554", "emulator-5556", "emulator-5560",
            "emulator-5564", "emulator-5566", "emulator-5568",
        ):
            self.assertTrue(RUNNER.case_serial_compatible(serial, "P020"))

    def test_p006_is_restricted_to_qualified_source_serial(self) -> None:
        self.assertTrue(RUNNER.case_serial_compatible("emulator-5554", "P006"))
        for serial in (
            "emulator-5556", "emulator-5560", "emulator-5564",
            "emulator-5566", "emulator-5568",
        ):
            self.assertFalse(RUNNER.case_serial_compatible(serial, "P006"))


class FinalizedLedgerLocalAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temporary.name)
        self.original_formal_root = RUNNER.FORMAL_ROOT
        self.original_migration = RUNNER.MIGRATION
        self.original_clock_audit = RUNNER.CLOCK_AUDIT
        RUNNER.FORMAL_ROOT = self.run_dir / "formal"
        RUNNER.MIGRATION = self.run_dir / "migration.json"
        RUNNER.CLOCK_AUDIT = self.run_dir / "clock.json"
        RUNNER.atomic_json(RUNNER.MIGRATION, {"fresh_cells": []})
        RUNNER.atomic_json(RUNNER.CLOCK_AUDIT, {"cases": []})
        ledger = StructuredLedger(self.run_dir / "ledger.sqlite3", {"test": True})
        ledger.close()

    def tearDown(self) -> None:
        RUNNER.FORMAL_ROOT = self.original_formal_root
        RUNNER.MIGRATION = self.original_migration
        RUNNER.CLOCK_AUDIT = self.original_clock_audit
        self.temporary.cleanup()

    def test_local_audit_is_reused_without_remote_call(self) -> None:
        first = RUNNER.audit_finalized_ledger(self.run_dir)
        with mock.patch.object(RUNNER.subprocess, "run") as remote:
            actual = RUNNER.audit_finalized_ledger(self.run_dir)
        self.assertEqual(actual, first)
        self.assertEqual(actual["status"], "passed")
        self.assertEqual(actual["mysql_mirror_status"], "pending_model_completion")
        remote.assert_not_called()

    def test_local_audit_writes_deferred_mysql_outbox(self) -> None:
        actual = RUNNER.audit_finalized_ledger(self.run_dir)
        records = list((RUNNER.FORMAL_ROOT / "mysql_deferred_outbox").glob("*.json"))
        self.assertEqual(len(records), 1)
        outbox = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(outbox["status"], "pending_model_completion")
        self.assertEqual(outbox["source_ledger_sha256"], actual["source_ledger_sha256"])

    def test_sqlite_only_config_preserves_source_and_disables_live_mysql(self) -> None:
        source = self.run_dir / "device.json"
        original = {"storage": {"sync_after_formal_run": True}}
        source.write_text(json.dumps(original) + "\n", encoding="utf-8")
        before = source.read_bytes()

        target = RUNNER.sqlite_only_config(source)

        self.assertEqual(source.read_bytes(), before)
        value = json.loads(target.read_text(encoding="utf-8"))
        self.assertFalse(value["storage"]["sync_after_formal_run"])
        self.assertFalse(value["storage"]["mysql_mirror_required_for_formal_run"])
        self.assertEqual(
            value["storage"]["mysql_mirror_mode"],
            "deferred_until_model_completion",
        )


if __name__ == "__main__":
    unittest.main()
