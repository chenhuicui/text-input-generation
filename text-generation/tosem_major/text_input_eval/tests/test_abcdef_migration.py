from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.abcdef_migration import build_migration_plan
from text_input_eval.supplementary.structured_storage import StructuredLedger


class ABCDEFMigrationTests(unittest.TestCase):
    def _write_trajectory(
        self, root: Path, *, trajectory_id: str, arm: str, replicate: int,
        status: str, outcome: str, started: str, device_time: bool = True,
    ) -> None:
        path = root / "ledger.sqlite3"
        ledger = StructuredLedger(path, {"legacy": True})
        dimensions = {
            "protocol_id": "E2-abcdef-v1", "case_id": "P001", "task": "valid_pptr",
            "model": "model", "arm": arm, "replicate": replicate, "representation": "extracted",
        }
        call_id = f"call-{trajectory_id}"
        ledger.reserve_call(call_id, "subject_model_generation", dimensions, {"message": "FULL PROMPT"})
        ledger.finish_call(call_id, {
            "run_status": "ok", "raw_response": "FULL ANSWER", "provider_response": {"ok": True},
            "usage": {"total_tokens": 1},
        })
        ledger.begin_trajectory(trajectory_id, dimensions)
        receipt = {"reset_verified": True}
        if device_time:
            receipt["device_time"] = {"verified": True}
        ledger.append_event(trajectory_id, {"event": "attempt_begin", "reset_receipt": receipt})
        ledger.append_event(trajectory_id, {
            "event": "model_request", "turn": 1, "physical_request_id": call_id,
            "request_id": f"{trajectory_id}-turn-01", "prompt_audit": {},
        })
        request_root = root / "evidence" / trajectory_id / f"{trajectory_id}-turn-01"
        request_root.mkdir(parents=True, exist_ok=True)
        (request_root / "frame.png").write_bytes(b"png")
        ledger.append_event(trajectory_id, {
            "event": "execution", "turn": 1, "verdict": outcome.lower(),
            "observation_complete": True, "evidence_hash": "hash",
        })
        records = [{
            "turn": 1, "request_id": f"{trajectory_id}-turn-01", "raw_response": "FULL ANSWER",
            "candidate": {"field": "value"}, "outcome": outcome, "run_status": "ok",
        }]
        reason = "oracle_pass" if status == "completed_success" else "budget_exhausted_oracle_fail"
        budget = 3
        model_calls = 1
        if status == "completed_failure":
            for turn in (2, 3):
                call = f"call-{trajectory_id}-{turn}"
                ledger.reserve_call(call, "subject_model_generation", {**dimensions, "turn": turn}, {"message": "FULL PROMPT"})
                ledger.finish_call(call, {
                    "run_status": "ok", "raw_response": "FULL ANSWER",
                    "provider_response": {"ok": True}, "usage": {"total_tokens": 1},
                })
                request_id = f"{trajectory_id}-turn-{turn:02d}"
                (root / "evidence" / trajectory_id / request_id).mkdir(parents=True, exist_ok=True)
                (root / "evidence" / trajectory_id / request_id / "frame.png").write_bytes(b"png")
                ledger.append_event(trajectory_id, {
                    "event": "model_request", "turn": turn, "physical_request_id": call,
                    "request_id": request_id, "prompt_audit": {},
                })
                ledger.append_event(trajectory_id, {
                    "event": "execution", "turn": turn, "verdict": "fail",
                    "observation_complete": True, "evidence_hash": "hash",
                })
                records.append({
                    "turn": turn, "request_id": request_id, "raw_response": "FULL ANSWER",
                    "candidate": {"field": "value"}, "outcome": "FAIL", "run_status": "ok",
                })
            model_calls = 3
        ledger.finish_trajectory(trajectory_id, {
            "protocol_id": "E2-abcdef-v1", "status": status, "reason": reason,
            "budget": budget, "model_calls": model_calls, "records": records,
            "started_at_utc": started,
        })
        ledger.close()

    def test_selects_earliest_valid_even_when_it_is_semantic_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            self._write_trajectory(
                first, trajectory_id="failure", arm="A", replicate=1,
                status="completed_failure", outcome="FAIL", started="2026-01-01T00:00:00Z",
            )
            self._write_trajectory(
                second, trajectory_id="success", arm="A", replicate=2,
                status="completed_success", outcome="PASS", started="2026-01-02T00:00:00Z",
            )
            registry = base / "registry.json"
            registry.write_text(json.dumps({"cases": [{
                "case_id": "P001", "task": "valid_pptr",
                "formal_applicability": {"in_scope": True},
            }]}), encoding="utf-8")
            clock = base / "clock.json"
            clock.write_text(json.dumps({"cases": [{
                "case_id": "P001", "reset_network_mode": "online_capable_or_remote",
            }]}), encoding="utf-8")

            report = build_migration_plan(
                ledger_paths=[first / "ledger.sqlite3", second / "ledger.sqlite3"],
                registry_path=registry, clock_audit_path=clock, model="model",
            )

            arm_a = next(row for row in report["selections"] if row["arm"] == "A")
            self.assertEqual(arm_a["selected"]["trajectory_id"], "failure")
            self.assertEqual(arm_a["selected"]["semantic_status"], "completed_failure")
            self.assertEqual(report["target_trajectories"], 6)
            self.assertEqual(report["selected_legacy_trajectories"], 1)
            self.assertEqual(report["fresh_trajectory_cells"], 5)

    def test_unverified_online_clock_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            self._write_trajectory(
                source, trajectory_id="stale", arm="A", replicate=1,
                status="completed_success", outcome="PASS", started="2026-01-01T00:00:00Z",
                device_time=False,
            )
            registry = base / "registry.json"
            registry.write_text(json.dumps({"cases": [{
                "case_id": "P001", "task": "valid_pptr",
                "formal_applicability": {"in_scope": True},
            }]}), encoding="utf-8")
            clock = base / "clock.json"
            clock.write_text(json.dumps({"cases": [{
                "case_id": "P001", "reset_network_mode": "online_capable_or_remote",
            }]}), encoding="utf-8")
            report = build_migration_plan(
                ledger_paths=[source / "ledger.sqlite3"], registry_path=registry,
                clock_audit_path=clock, model="model",
            )
            arm_a = next(row for row in report["selections"] if row["arm"] == "A")
            self.assertIsNone(arm_a["selected"])
            self.assertEqual(report["audit_exclusion_reason_counts"]["unverified_clock"], 1)


if __name__ == "__main__":
    unittest.main()
