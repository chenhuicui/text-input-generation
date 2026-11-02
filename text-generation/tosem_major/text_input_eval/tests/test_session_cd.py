from __future__ import annotations

import base64
import copy
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from text_input_eval.services.feedback_based import FeedbackBuilder, LLMClient as FeedbackLLMClient, PromptGenerator
from text_input_eval.services.one_shot import PromptContext
from text_input_eval.supplementary.context import Case, ROOT, digest
from text_input_eval.supplementary.protocol import empty_evaluation
from text_input_eval.supplementary.session_cli import (
    build_session_manifest, live_readiness_issues, main, read_session_config, write_session_plan,
)
from text_input_eval.supplementary.session_feedback import next_feedback, structured_feedback, verified_ui_evidence
from text_input_eval.supplementary.session_report import export_session_report
from text_input_eval.supplementary.session_runner import (
    SessionCapacityGuard, SessionGateway, SessionRunner, normalize_usage, prepare_session,
)
from text_input_eval.supplementary.storage import Ledger


def make_case(task="valid_pptr", case_id="P001"):
    ctx = PromptContext(task, "app", case_id, "Generate inputs", "resource-id: app:id/a", "",
                        '{"app:id/a":"generated_value"}', "", '<hierarchy><node resource-id="app:id/a" /></hierarchy>',
                        "", ["app:id/a"])
    return Case(ctx, "app", digest(ctx.xml_text), [])


def failed(request_id="r"):
    ev = empty_evaluation("ok")
    ev.update(output_status="valid", candidate={"app:id/a": "fail"}, canonical_candidate={"app:id/a": "fail"},
              goal_verdict=False, behavior_verdict=False, rule_verdict=False)
    return {"request_id": request_id, "evaluation": ev, "chat": '{"app:id/a":"fail"}'}


class BehaviorDouble:
    endpoint = "behavior"
    oracle_cases = {}

    def evaluate(self, case, prepared, chat, request_id, artifact_dir):
        ev = failed(request_id)["evaluation"]
        if json.loads(chat)["app:id/a"] == "pass":
            ev.update(goal_verdict=True, behavior_verdict=True, rule_verdict=True)
        return ev


class SessionDouble:
    def __init__(self):
        self.payloads = []
        self.initials = 0

    def __call__(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        cursor_id = payload["id"]
        if not cursor_id:
            self.initials += 1
            response_id = f"cursor-{self.initials}-1"
            session_id = f"session-{self.initials}"
        else:
            response_id = f"cursor-{self.initials}-{len(self.payloads)}"
            session_id = f"session-{self.initials}"
        return {"success": True, "data": {"id": response_id, "sessionId": session_id,
                                             "chat": '{"app:id/a":"fail"}'}}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        # Any unintended network call fails the test. The app adapter is doubled.
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("Offline tests only"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.socket = patch("socket.socket.connect", side_effect=AssertionError("Offline tests only"))
        self.socket.start()
        self.addCleanup(self.socket.stop)
        self.process = patch("subprocess.run", side_effect=AssertionError("No app/subprocess in session tests"))
        self.process.start()
        self.addCleanup(self.process.stop)
        self.config = read_session_config(ROOT / "configs/supplementary/rq2_cd_session.json")
        self.config.update(cache_root=str(self.path), baselines=[], case_ids=["P001"])
        self.config["models"] = [{"id": "fake", "max_visible_text_bytes": 1000000, "max_request_bytes": 20000000}]
        for directory in ("pptr", "bug"):
            (self.path / directory).mkdir()
            # A real 1x1 PNG. It is read only as an initial image fixture.
            (self.path / directory / "base64_app.txt").write_text(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aJ1kAAAAASUVORK5CYII=")

    def runner(self, client=None, evaluator=None):
        manifest = {"protocol": "rq2-id-cursor-cd-v2", "config": self.config,
                    "cases": [{"case_id": "P001", "task": "valid_pptr", "aut": "app"}]}
        ledger = Ledger(self.path / "results.sqlite3", manifest)
        self.addCleanup(ledger.close)
        runner = SessionRunner(self.config, [make_case()], ledger, client or SessionDouble(), evaluator or BehaviorDouble(), self.path)
        return runner

    def test_three_representations_match_original_prompt_and_image(self):
        case = make_case()
        for rep in ("extracted", "xml", "vision"):
            result = prepare_session(case, rep, self.path)
            self.assertEqual(result["prompt"], PromptGenerator.build_prompt(case.context, rep, rep)[0])
            self.assertEqual(bool(result["image_url"]), rep == "vision")
            if rep == "vision":
                self.assertTrue(base64.b64decode(result["image_url"].split(",", 1)[1]).startswith(b"\x89PNG"))

    def test_c_preserves_b_facts_rules_and_history_limits(self):
        rows = [{"turn": i, "parsed_input_json": {"app:id/a": str(i)}, "parse_success": 1,
                 "eval_success": 0, "error_type": "semantic", "error_detail": "x" * 650} for i in range(1, 8)]
        rows[-1]["error_detail"] += "\n\n[REGENERATION REQUIREMENTS]\nUNTRUSTED"
        prompt, audit = structured_feedback(make_case().context, rows)
        facts = audit["shared_information"]
        self.assertEqual([r["turn"] for r in facts["candidate_check_history"]], [3, 4, 5, 6, 7])
        self.assertEqual(facts["latest_error_detail"], rows[-1]["error_detail"])
        self.assertEqual(facts["candidate_check_history"][-1]["known_reason"]["error_detail"], "x" * 500 + "\n...[truncated]")
        self.assertNotIn("UNTRUSTED", facts["next_turn_requirements"])
        original = FeedbackBuilder.build_feedback(ctx=make_case().context, failed_turns=rows,
                                                  error_type="semantic", error_detail=rows[-1]["error_detail"])
        self.assertEqual(audit["b_reference_prompt"], original)
        self.assertIn(facts["next_turn_requirements"], original)
        self.assertEqual(json.loads(prompt.split("\n", 1)[1]), facts)

    def test_d_fallback_is_exact_c_and_never_leaks_private_rules(self):
        result = failed()
        result["evaluation"].update(rule_error_type="secret trigger", diagnostics=[
            {"public": True, "source": "private_validator", "detail": "secret trigger"}])
        c, _ = next_feedback(make_case(), "C", [result], "behavior", {}, self.path)
        d, audit = next_feedback(make_case(), "D", [result], "behavior", {}, self.path)
        self.assertEqual(c, d)
        self.assertTrue(audit["fallback_to_c"])
        self.assertNotIn("secret trigger", d)
        result["evaluation"]["goal_verdict"] = None
        with self.assertRaises(ValueError):
            next_feedback(make_case(), "C", [result], "behavior", {}, self.path)

    def evidence_fixture(self):
        directory = self.path / "r"
        directory.mkdir()
        raw = b'<hierarchy><node resource-id="app:id/error" text="At least 8 characters" /></hierarchy>'
        (directory / "after.xml").write_bytes(raw)
        oracle = {"version": "v1", "public_diagnostic_targets": [{"id": "error", "selector": {"resource-id": "app:id/error"}}]}
        result = failed()
        result["evaluation"]["observation"] = {"run_status": "ok", "behavior_verdict": False, "request_id": "r",
            "candidate_hash": digest({"app:id/a": "fail"}), "oracle_version": "v1", "reset_verified": True,
            "evidence": [{"path": "after.xml", "sha256": hashlib.sha256(raw).hexdigest()}]}
        result["evaluation"]["diagnostics"] = [{"type": "observed_ui_message", "detail": "At least 8 characters",
            "source": "ui_observation", "public": True, "checker_id": "error", "evidence_ref": "request:r:after.xml"}]
        return result, oracle

    def test_d_accepts_current_text_and_rejects_fabricated_or_stale_text(self):
        result, oracle = self.evidence_fixture()
        evidence, _ = verified_ui_evidence(result, oracle, self.path)
        self.assertEqual(len(evidence), 1)
        c, _ = next_feedback(make_case(), "C", [result], "behavior", oracle, self.path)
        d, audit = next_feedback(make_case(), "D", [result], "behavior", oracle, self.path)
        self.assertTrue(d.startswith(c))
        self.assertIn("At least 8 characters", d)
        self.assertTrue(audit["diagnostic_available"])
        for mutation in ({"detail": "Password must be exactly secret"}, {"evidence_ref": "request:old:after.xml"},
                         {"checker_id": "private"}, {"source": "public_spec_check"}):
            bad = copy.deepcopy(result)
            bad["evaluation"]["diagnostics"][0].update(mutation)
            self.assertEqual(verified_ui_evidence(bad, oracle, self.path)[0], [])
        (self.path / "r" / "after.xml").write_text("<hierarchy />")
        self.assertEqual(verified_ui_evidence(result, oracle, self.path)[0], [])

    def test_id_chains_are_isolated_and_vision_continues_without_resending(self):
        runner = self.runner()
        self.assertEqual(runner.run(), "finished")
        calls = runner.client.payloads
        self.assertEqual(len(calls), 18)  # 3 representations x 2 arms x 3 total calls.
        self.assertEqual(sum(not p["id"] for p in calls), 6)
        self.assertEqual(sum("imageUrl" in p for p in calls), 2)
        for start in range(0, 18, 3):
            block = calls[start:start + 3]
            self.assertEqual(block[0]["id"], "")
            self.assertTrue(block[1]["id"])
            self.assertNotEqual(block[1]["id"], block[2]["id"])
            self.assertTrue(all("imageUrl" not in p for p in block[1:]))
        self.assertEqual(len({calls[i]["id"] for i in range(1, 18, 3)}), 6)
        rows = [json.loads(r[0]) for r in runner.ledger.db.execute("SELECT result FROM requests")]
        self.assertTrue(any(r["capacity"]["conversation_contains_image"] and r["request_cursor_id"] for r in rows))
        report = export_session_report(self.path)
        self.assertEqual(report["new_submitted_requests"], 18)
        self.assertEqual(report["planned_trajectories"], 6)

    def test_pause_and_resume_never_replays_an_api_request(self):
        runner = self.runner()
        self.assertEqual(runner.run(1), "paused_at_call_limit")
        self.assertEqual(export_session_report(self.path)["new_submitted_requests"], 1)
        restarted = SessionRunner(self.config, [make_case()], runner.ledger, runner.client, BehaviorDouble(), self.path)
        self.assertEqual(restarted.run(), "finished")
        self.assertEqual(len(runner.client.payloads), 18)
        self.assertEqual(restarted.run(), "finished")
        self.assertEqual(len(runner.client.payloads), 18)

    def test_recorded_response_resumes_without_repeating_generation(self):
        self.config.update(arms=["C"], representations=["vision"])
        runner = self.runner()
        with patch.object(runner.ledger, "evaluating", side_effect=RuntimeError("simulated process interruption")):
            with self.assertRaises(RuntimeError):
                runner.run()
        self.assertEqual(len(runner.client.payloads), 1)
        runner.run()
        self.assertEqual(len(runner.client.payloads), 3)

    def test_uncertain_execution_is_not_replayed(self):
        runner = self.runner()
        runner.ledger.reserve("uncertain", {}, {})
        runner.ledger.evaluating("uncertain")
        with self.assertRaises(RuntimeError):
            runner.run()
        self.assertEqual(runner.client.payloads, [])

    def test_missing_cursor_stops_before_text_only_fresh_retry(self):
        self.config.update(arms=["C"], representations=["vision"])
        client = Mock(return_value={"data": {"chat": '{"app:id/a":"fail"}'}})
        runner = self.runner(client)
        runner.run()
        self.assertEqual(client.call_count, 1)
        record = json.loads(runner.ledger.db.execute("SELECT result FROM trajectories").fetchone()[0])
        self.assertEqual(record["reason"], "missing_cursor_id_for_continuation")
        self.assertEqual(record["status"], "incomplete")

    def test_cross_trajectory_cursor_collision_stops_the_run(self):
        client = Mock(return_value={"data": {"id": "same-session", "chat": '{"app:id/a":"fail"}'}})
        runner = self.runner(client)
        with self.assertRaisesRegex(RuntimeError, "reused a response cursor"):
            runner.run()
        self.assertEqual(client.call_count, 4)
        with self.assertRaisesRegex(RuntimeError, "Prior cross-trajectory"):
            runner.run()
        self.assertEqual(client.call_count, 4)

    def test_capacity_failure_is_incomplete_not_task_failure(self):
        self.config.update(arms=["C"], representations=["vision"])
        client = Mock(return_value={"success": False, "msg": "maximum context length exceeded"})
        runner = self.runner(client)
        runner.run()
        report = export_session_report(self.path)
        self.assertEqual(report["run_status_counts"], {"capacity_blocked": 1})
        self.assertEqual(client.call_count, 1)
        result = json.loads(runner.ledger.db.execute("SELECT result FROM trajectories").fetchone()[0])
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["first_success_turn"])

    def test_expired_session_does_not_create_a_new_session(self):
        self.config.update(arms=["C"], representations=["vision"])
        client = Mock(side_effect=[{"data": {"id": "s", "chat": '{"app:id/a":"fail"}'}},
                                   {"success": False, "msg": "session expired"}])
        runner = self.runner(client)
        runner.run()
        self.assertEqual(client.call_count, 2)
        self.assertEqual(client.call_args_list[1].args[0]["id"], "s")
        self.assertNotIn("imageUrl", client.call_args_list[1].args[0])
        record = json.loads(runner.ledger.db.execute("SELECT result FROM trajectories").fetchone()[0])
        self.assertEqual(record["status"], "incomplete")
        self.assertEqual(record["reason"], "provider_cursor_unavailable")

    def test_bug_budget_is_thirty_calls_in_one_session_per_arm(self):
        self.config.update(representations=["extracted"])
        ledger = Ledger(self.path / "bug.sqlite3", {"config": self.config})
        self.addCleanup(ledger.close)
        client = SessionDouble()
        runner = SessionRunner(self.config, [make_case("invalid_bug", "B001")], ledger, client, BehaviorDouble(), self.path)
        runner.run()
        self.assertEqual(len(client.payloads), 60)
        self.assertEqual(client.initials, 2)
        for record in ledger.db.execute("SELECT result FROM trajectories"):
            row = json.loads(record[0])
            self.assertEqual(row["budget"], 30)
            self.assertEqual(len(row["request_ids"]), 30)

    def test_success_metrics_and_unknown_cost_remain_distinct(self):
        self.config.update(arms=["C"], representations=["xml"])
        client = Mock(side_effect=[{"data": {"id": "s", "chat": '{"app:id/a":"fail"}'}},
                                   {"data": {"id": "s", "chat": '{"app:id/a":"pass"}'}}])
        runner = self.runner(client)
        runner.run()
        export_session_report(self.path)
        with (self.path / "cd_summary.csv").open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["completed"], "1")
        self.assertEqual(row["success_rate_completed"], "1.0")
        self.assertEqual(row["rescue_rate_within_arm"], "1.0")
        self.assertEqual(row["mean_calls_to_success"], "2.0")
        self.assertEqual(row["physical_submitted_requests"], "2")
        self.assertEqual(row["total_tokens_sum_known"], "")
        self.assertEqual(row["monetary_cost"], "")

    def test_refusal_stops_and_does_not_execute_app(self):
        self.config.update(arms=["C"], representations=["xml"], refusal_exact_matches=["REFUSAL_FIXTURE"])
        evaluator = BehaviorDouble()
        evaluator.evaluate = Mock(side_effect=AssertionError("Refusal must not reach app"))
        client = Mock(return_value={"data": {"id": "s", "chat": "REFUSAL_FIXTURE"}})
        runner = self.runner(client, evaluator)
        runner.run()
        self.assertEqual(client.call_count, 1)
        evaluator.evaluate.assert_not_called()
        record = json.loads(runner.ledger.db.execute("SELECT result FROM trajectories").fetchone()[0])
        self.assertEqual(record["status"], "refusal")

    def test_cumulative_capacity_counts_previous_replies_and_feedback(self):
        model = {"id": "m", "max_visible_text_bytes": 20, "max_request_bytes": 1000}
        guard = SessionCapacityGuard([model])
        payload = {"modelType": "m", "id": "s", "message": "current"}
        self.assertTrue(guard.check(payload, [], True)["allowed"])
        checked = guard.check(payload, ["previous prompt", "previous reply"], True)
        self.assertFalse(checked["allowed"])
        self.assertFalse(checked["full_context_fit_verified"])

    def test_gateway_sends_exact_original_contract(self):
        with patch("text_input_eval.supplementary.session_runner.LLMClient") as cls:
            gateway = SessionGateway([{"id": "m"}], "test-only", "http://localhost", 10)
            payload = {"modelType": "m", "id": "cursor", "message": "next"}
            gateway(payload)
            cls.return_value.post_payload.assert_called_once_with(payload)

    def test_feedback_client_emits_id_cursor_field(self):
        client = FeedbackLLMClient("test-only", "m", "http://localhost", 10)
        payload = client.build_payload("next", cursor_id="previous-response-id")
        self.assertEqual(payload, {"modelType": "m", "id": "previous-response-id", "message": "next"})

    def test_revised_gateway_chains_response_ids(self):
        self.config.update(arms=["C"], representations=["xml"])
        responses = [
            {"data": {"id": "session-1", "sessionId": "session-1", "chat": '{"app:id/a":"fail"}'}},
            {"data": {"id": "message-2", "sessionId": "session-1", "chat": '{"app:id/a":"fail"}'}},
            {"data": {"id": "message-3", "sessionId": "session-1", "chat": '{"app:id/a":"fail"}'}}
        ]
        client = Mock(side_effect=responses)
        runner = self.runner(client)
        runner.run()
        payloads = [call.args[0] for call in client.call_args_list]
        self.assertEqual([p["id"] for p in payloads], ["", "session-1", "message-2"])
        records = {json.loads(row[0])["turn"]: json.loads(row[1]) for row in runner.ledger.db.execute(
            "SELECT dimensions, result FROM requests")}
        self.assertEqual(records[3]["response_cursor_id"], "message-3")
        self.assertEqual(records[3]["response_session_id"], "session-1")

    def test_provider_usage_is_normalized_without_zero_filling(self):
        self.assertEqual(normalize_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 4,
                                                     "total_tokens": 14}}),
                         {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
                          "source": "response.usage"})
        self.assertEqual(normalize_usage({"data": {"usageMetadata": {"promptTokenCount": 7,
            "candidatesTokenCount": 3, "totalTokenCount": 10}}}),
            {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10,
             "source": "data.usageMetadata"})
        self.assertEqual(normalize_usage({"data": {"inputTokens": 8, "outputTokens": 2}}),
            {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10, "source": "data"})
        self.assertEqual(normalize_usage({"data": {"tokens": {"inputToken": 5, "outputToken": 1,
                                                                 "totalToken": 6}}}),
            {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6,
             "source": "data.tokens"})
        self.assertIsNone(normalize_usage({"data": {"chat": "ok"}}))

    def test_d_rejects_rule_only_and_run_gate_precedes_gateway(self):
        cfg = read_session_config(ROOT / "configs/supplementary/rq2_cd_session.json")
        self.assertTrue(live_readiness_issues(cfg, [make_case()], {}))
        cfg["endpoint"] = "rule"
        with self.assertRaises(ValueError):
            SessionRunner(cfg, [], None, None, type("E", (), {"endpoint": "rule"})(), self.path)
        with patch("text_input_eval.supplementary.session_cli.build_session_manifest", return_value=([], {
            "config": self.config, "protocol": "rq2-id-cursor-cd-v2", "cases": [], "historical_audit": {
                "available": {"A": 0, "B": 0}, "missing_case_cells": {"A": 0, "B": 0}},
            "local_readiness_issues": ["unverified"]}, [])), patch("text_input_eval.supplementary.session_cli.SessionGateway") as gateway:
            with self.assertRaises(SystemExit):
                main(["run", "--config", str(ROOT / "configs/supplementary/rq2_cd_session.json"), "--output", str(self.path)])
            gateway.assert_not_called()

    def test_full_local_plan_and_ab_coverage_without_backend_calls(self):
        config = read_session_config(ROOT / "configs/supplementary/rq2_cd_session.json")
        cases, manifest, historical = build_session_manifest(config)
        summary = write_session_plan(self.path, manifest)
        self.assertEqual(len(cases), 152)
        self.assertEqual(summary["cohort"], {"valid_pptr": 115, "invalid_bug": 37})
        self.assertEqual(summary["historical_ab_available"], {"A": 4104, "B": 4104})
        self.assertEqual(summary["historical_ab_missing_case_cells"], {"A": 0, "B": 0})
        self.assertEqual(summary["planned_new_trajectories"], 8208)
        self.assertEqual(summary["max_new_model_calls_before_success_stopping"], 78570)
        self.assertTrue(all(r["replicate_id"] is None and not r["comparison_ready"] for r in historical))
        self.assertTrue(summary["local_readiness_issues"])
        self.assertFalse((self.path / "results.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
