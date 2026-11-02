from __future__ import annotations

import copy
import unittest

from text_input_eval.supplementary.structured_state import fresh_model_payload, validate_event_chain


def chain() -> list[dict]:
    return [
        {
            "event": "attempt_begin",
            "attempt_id": "P001-gpt-r1-A",
            "case_id": "P001",
            "arm": "A",
            "model": "gpt",
            "replicate": 1,
            "initial_state_hash": "state-0",
            "oracle_version": "oracle-v1",
            "reset_receipt": {
                "reset_verified": True,
                "target_page_verified": True,
                "remote_baseline": "not_applicable",
                "reset_id": "reset-v1",
                "state_hash": "state-0",
            },
        },
        {
            "event": "model_request",
            "turn": 1,
            "request_id": "request-1",
            "prompt_hash": "prompt-1",
            "visible_history_hash": "history-empty",
            "payload": fresh_model_payload("gpt", "prompt one"),
        },
        {
            "event": "execution",
            "turn": 1,
            "request_id": "request-1",
            "candidate_hash": "candidate-1",
            "oracle_version": "oracle-v1",
            "state_before_hash": "state-0",
            "state_after_hash": "state-1",
            "evidence_hash": "evidence-1",
            "verdict": "fail",
            "observation_complete": True,
        },
        {
            "event": "recovery",
            "turn": 1,
            "snapshot_loaded": False,
            "remote_reset": False,
            "renavigated_from_baseline": False,
            "natural_state_preserved": True,
            "state_before_hash": "state-1",
            "target_page_verified": True,
            "input_cleared": True,
            "state_hash": "state-recovered-1",
            "locator_refresh_hash": "locators-2",
        },
        {
            "event": "model_request",
            "turn": 2,
            "request_id": "request-2",
            "prompt_hash": "prompt-2",
            "visible_history_hash": "history-1",
            "payload": fresh_model_payload("gpt", "prompt two"),
        },
        {
            "event": "execution",
            "turn": 2,
            "request_id": "request-2",
            "candidate_hash": "candidate-2",
            "oracle_version": "oracle-v1",
            "state_before_hash": "state-recovered-1",
            "state_after_hash": "state-2",
            "evidence_hash": "evidence-2",
            "verdict": "pass",
            "observation_complete": True,
        },
        {
            "event": "trajectory_end",
            "turns_executed": 2,
            "status": "completed_success",
            "reason": "oracle_pass",
        },
    ]


class StructuredStateTests(unittest.TestCase):
    def test_fail_recover_pass_chain(self) -> None:
        result = validate_event_chain(chain(), budget=3)
        self.assertEqual(result["executions"], 2)
        self.assertEqual(result["recoveries"], 1)
        self.assertEqual(result["status"], "completed_success")

    def test_provider_cursor_is_forbidden(self) -> None:
        events = chain()
        events[1]["payload"]["id"] = "hidden-provider-session"
        with self.assertRaisesRegex(ValueError, "fresh explicit-history"):
            validate_event_chain(events, budget=3)

    def test_second_attempt_reset_is_forbidden(self) -> None:
        events = chain()
        events.insert(4, copy.deepcopy(events[0]))
        with self.assertRaisesRegex(ValueError, "reset exactly once"):
            validate_event_chain(events, budget=3)

    def test_recovery_cannot_load_snapshot_or_reset_remote_state(self) -> None:
        for field in ("snapshot_loaded", "remote_reset", "renavigated_from_baseline"):
            events = chain()
            events[3][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "must not reset"):
                validate_event_chain(events, budget=3)

    def test_unknown_is_incomplete_not_failure(self) -> None:
        events = chain()[:3]
        events[-1]["verdict"] = "unknown"
        events[-1]["observation_complete"] = False
        events.append({
            "event": "trajectory_end",
            "turns_executed": 1,
            "status": "incomplete",
            "reason": "observer_unavailable",
        })
        result = validate_event_chain(events, budget=3)
        self.assertEqual(result["final_verdict"], "unknown")
        self.assertEqual(result["status"], "incomplete")

    def test_next_request_requires_recovery(self) -> None:
        events = chain()
        del events[3]
        with self.assertRaisesRegex(ValueError, "expected recovery"):
            validate_event_chain(events, budget=3)

    def test_parse_error_consumes_turn_without_android_execution_or_recovery(self) -> None:
        events = chain()
        events[2:4] = [{
            "event": "output_check",
            "turn": 1,
            "request_id": "request-1",
            "output_status": "parse_error",
            "candidate_hash": None,
        }]
        events[4]["state_before_hash"] = "state-0"
        events[-1]["turns_executed"] = 2
        result = validate_event_chain(events, budget=3)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["executions"], 1)
        self.assertEqual(result["recoveries"], 0)

    def test_model_infrastructure_error_stays_incomplete(self) -> None:
        events = chain()[:2]
        events.extend([
            {
                "event": "output_check",
                "turn": 1,
                "request_id": "request-1",
                "output_status": "infrastructure_error",
                "candidate_hash": None,
            },
            {
                "event": "trajectory_end",
                "turns_executed": 1,
                "status": "incomplete",
                "reason": "model_infrastructure_error",
            },
        ])
        result = validate_event_chain(events, budget=3)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["executions"], 0)


if __name__ == "__main__":
    unittest.main()
