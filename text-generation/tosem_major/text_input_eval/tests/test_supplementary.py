from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from text_input_eval.services.one_shot import PromptContext, PromptGenerator
from text_input_eval.supplementary.android_oracle import execute, observe
from text_input_eval.supplementary.cli import export_report, read_config
from text_input_eval.supplementary.context import Case, ROOT, binding_issues, digest, load_cases, prepare, replace_ids
from text_input_eval.supplementary.protocol import Evaluator, empty_evaluation, feedback_prompt
from text_input_eval.supplementary.runner import Gateway, Runner
from text_input_eval.supplementary.storage import Ledger


def case(task="valid_pptr", case_id="P001"):
    ctx = PromptContext(task, "app", case_id, "Generate inputs.", "resource-id: app:id/a", "",
        'Response: ```json {"app:id/a": "generated_value"} ```', "",
        '<hierarchy><node resource-id="app:id/a" index="0" class="android.widget.EditText" package="app" /></hierarchy>',
        "", ["app:id/a"])
    return Case(ctx, "app", digest(ctx.xml_text), [])


def configuration():
    cfg = read_config(ROOT / "configs/supplementary/e1_e2.json")
    cfg.update(experiments=["E2"], replicates=1, endpoint="rule")
    cfg["models"] = [{"id": "fake", "max_prompt_bytes": 1000000, "gateway_verified": True}]
    return cfg


class FakeEvaluator:
    endpoint = "rule"
    oracle_cases = {}

    def evaluate(self, case, prepared, chat, request_id, artifact_dir):
        ev = empty_evaluation("ok")
        candidate = json.loads(chat)
        ev.update(output_status="valid", candidate=candidate, canonical_candidate=candidate,
                  goal_verdict=candidate["app:id/a"] == "pass", rule_verdict=candidate["app:id/a"] == "pass")
        return ev


class ProtocolTests(unittest.TestCase):
    def test_legacy_prompt_matches_and_id_inverse(self):
        c = case()
        original = prepare(c, "extracted", "original")
        self.assertEqual(original["prompt"], PromptGenerator.build_prompt(c.context, "extracted", "full")[0])
        renamed = prepare(c, "extracted", "id_rename")
        self.assertNotIn("app:id/a", renamed["prompt"])
        self.assertEqual([renamed["inverse_map"][k] for k in renamed["resource_ids"]], c.context.resource_ids)
        self.assertFalse(renamed["exclusion_reason"])

    def test_replacement_does_not_cascade_or_replace_prefix(self):
        self.assertEqual(replace_ids("app:id/a app:id/ab app:id/b", {"app:id/a": "app:id/b", "app:id/b": "Z"}),
                         "app:id/b app:id/ab Z")

    def test_noise_has_no_false_extracted_treatment(self):
        self.assertEqual(prepare(case(), "extracted", "irrelevant_nodes")["exclusion_reason"],
                         "identical_prompt_after_extraction")
        xml = prepare(case(), "xml", "irrelevant_nodes")
        self.assertTrue(xml["effective"])
        self.assertIsNone(xml["exclusion_reason"])

    def test_duplicate_binding_does_not_get_guessed(self):
        c = case()
        c.context.xml_text = c.context.xml_text.replace("</hierarchy>", '<node resource-id="app:id/a" /></hierarchy>')
        c.binding_issues = binding_issues(c.context)
        self.assertTrue(prepare(c, "xml", "id_rename")["exclusion_reason"])

    def test_four_visible_information_sets(self):
        ev = empty_evaluation("ok")
        ev.update(candidate={"a": "candidate-only"}, goal_verdict=False,
            diagnostics=[{"type": "business", "detail": "PRIVATE_TRIGGER_SECRET", "source": "private_oracle", "public": True,
                          "evidence_ref": "private", "checker_id": "hidden"},
                         {"type": "syntax", "detail": "PUBLIC_SYNTAX", "source": "output_parser", "public": True,
                          "evidence_ref": "r1", "checker_id": "parser"}])
        history = [{"evaluation": ev, "chat": "candidate-only"}]
        prompts = {arm: feedback_prompt("BASE", arm, history, 5, 8000, 1000)
                   for arm in ("retry", "history", "binary", "typed")}
        self.assertEqual(prompts["retry"], "BASE")
        self.assertNotIn("goal_passed", prompts["history"])
        self.assertIn('"goal_passed":false', prompts["binary"])
        self.assertNotIn("PUBLIC_SYNTAX", prompts["binary"])
        self.assertIn("PUBLIC_SYNTAX", prompts["typed"])
        self.assertTrue(all("PRIVATE_TRIGGER_SECRET" not in p for p in prompts.values()))

    def test_gateway_reuses_contract_but_never_response_session(self):
        with patch("text_input_eval.supplementary.runner.LLMClient") as client:
            client.return_value.chat_completion.return_value = {"data": {"id": "old-session"}}
            gateway = Gateway([{"id": "fake"}], "test-only", "http://localhost", 10)
            gateway("fake", "one")
            gateway("fake", "two")
            self.assertEqual([c.kwargs["cursor_id"] for c in client.return_value.chat_completion.call_args_list], ["", ""])

    def test_history_same_truncation_and_unknown_refused(self):
        ev = empty_evaluation("ok")
        ev.update(candidate={"a": "123456789"}, goal_verdict=False)
        for arm in ("history", "binary", "typed"):
            prompt = feedback_prompt("BASE", arm, [{"evaluation": ev}] * 6, 2, 5, 10)
            self.assertEqual(prompt.count('"candidate_truncated":true'), 2)
        ev["goal_verdict"] = None
        with self.assertRaises(ValueError):
            feedback_prompt("BASE", "binary", [{"evaluation": ev}], 5, 100, 100)

    def test_parse_schema_and_checker_errors_are_separate(self):
        evaluator, c = Evaluator("rule"), case()
        prepared = prepare(c, "extracted", "original")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for chat, expected in (("not json", "parse_error"), ('{"extra":"x"}', "schema_error"),
                                   ('{"app:id/a":12}', "schema_error")):
                result = evaluator.evaluate(c, prepared, chat, "r", directory)
                self.assertEqual(result["output_status"], expected)
                self.assertIs(result["goal_verdict"], False)
                self.assertIsNone(result["behavior_verdict"])
            with patch("text_input_eval.supplementary.protocol.get_validator", side_effect=RuntimeError):
                result = evaluator.evaluate(c, prepared, '{"app:id/a":"value"}', "r", directory)
                self.assertEqual(result["run_status"], "checker_error")
                self.assertIsNone(result["goal_verdict"])
                self.assertFalse(result["diagnostics"])

    def test_rule_false_has_no_invented_diagnosis_and_inverse_reaches_validator(self):
        c = case()
        prepared = prepare(c, "xml", "id_rename")
        validator = SimpleNamespace(validate=lambda inputs: inputs == {"app:id/a": "value"})
        with patch("text_input_eval.supplementary.protocol.get_validator", return_value=validator):
            result = Evaluator("rule").evaluate(c, prepared, json.dumps({prepared["resource_ids"][0]: "wrong"}), "r", Path("unused"))
            self.assertIs(result["rule_verdict"], False)
            self.assertIsNone(result["behavior_verdict"])
            self.assertEqual(result["canonical_candidate"], {"app:id/a": "wrong"})
            self.assertFalse(result["diagnostics"])

    def test_behavior_never_falls_back_to_rule(self):
        c = case()
        with patch("text_input_eval.supplementary.protocol.get_validator", return_value=SimpleNamespace(validate=lambda _: True)):
            result = Evaluator("behavior").evaluate(c, prepare(c, "extracted", "original"), '{"app:id/a":"x"}', "r", Path("unused"))
        self.assertIs(result["rule_verdict"], True)
        self.assertIsNone(result["goal_verdict"])
        self.assertEqual(result["run_status"], "oracle_unavailable")

    def test_behavior_requires_current_candidate_and_evidence(self):
        c = case()
        oracle = {"P001": {"status": "ready", "version": "v1"}}
        evaluator = Evaluator("behavior", oracle, ["fake-command"])
        obs = {"run_status": "ok", "request_id": "r", "candidate_hash": digest({"app:id/a": "x"}),
               "oracle_version": "v1", "reset_verified": True, "behavior_verdict": True,
               "evidence": [{"path": "after.xml"}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "after.xml").write_text("<hierarchy />")
            for mutation, passed in (({}, True), ({"candidate_hash": "wrong"}, False), ({"reset_verified": False}, False),
                                     ({"evidence": [{"path": "missing.png"}]}, False)):
                response = SimpleNamespace(stdout=json.dumps({**obs, **mutation}), stderr="")
                with patch("text_input_eval.supplementary.protocol.subprocess.run", return_value=response):
                    result = evaluator.evaluate(c, prepare(c, "extracted", "original"), '{"app:id/a":"x"}', "r", path)
                self.assertEqual(result["goal_verdict"], True if passed else None)

    def test_android_assertions_all_any_absence_and_invalid_observations(self):
        xml = '<hierarchy><node text="A" /></hierarchy>'
        targets = [{"selector": {"text": "A"}, "exists": True}, {"selector": {"text": "B"}, "exists": True}]
        self.assertFalse(observe(xml, {"mode": "all", "targets": targets})[0])
        self.assertTrue(observe(xml, {"mode": "any", "targets": targets})[0])
        self.assertTrue(observe(xml, {"mode": "all", "targets": [{"selector": {"text": "B"}, "exists": False}]})[0])
        for assertion in ({"mode": "all", "targets": []}, {"mode": "all", "targets": [{"selector": {}, "exists": False}]}):
            with self.assertRaises(ValueError):
                observe(xml, assertion)
        with self.assertRaises(Exception):
            observe("invalid XML", {"mode": "all", "targets": targets})

    def test_android_adapter_stages_with_fake_device(self):
        # No adb or device call escapes these mocks. Exercise the real adapter
        # state machine, including failures before submission and bound evidence.
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            apk = directory / "fixture.apk"
            apk.write_bytes(b"local test fixture, not an APK")
            apk_hash = hashlib.sha256(apk.read_bytes()).hexdigest()
            initial = '<hierarchy><node resource-id="app:id/a" text="Initial" index="0" /></hierarchy>'
            after = '<hierarchy><node text="Target" /></hierarchy>'
            for scenario in ("success", "already_at_goal", "fill_failure", "wrong_installed_apk"):
                with self.subTest(scenario=scenario):
                    start_xml = after if scenario == "already_at_goal" else initial
                    device = Mock()
                    device.dump_hierarchy.side_effect = [start_xml, initial, after]
                    device.screenshot.side_effect = lambda filename: Path(filename).write_bytes(b"fake image")
                    launcher = Mock(device=device)
                    launcher.launch_app.return_value = True
                    launcher.navigate_to_target_page.return_value = True
                    actions = Mock()
                    actions.fill_text_inputs.return_value = scenario != "fill_failure"
                    actions.execute_actions.return_value = True
                    oracle = {"version": "fixture-v1", "apk_path": str(apk), "apk_sha256": apk_hash,
                        "aut": "app", "device_id": "fake-device", "reset_id": "fixture-reset",
                        "reset_command": ["reset-fixture"], "verify_action": [{"action": "fixture"}],
                        "navigation_steps": [], "bindings": {"app:id/a": "app:id/a"},
                        "observation_seconds": 1, "poll_seconds": 0.01,
                        "initial_assertion": {"mode": "all", "targets": [{"selector": {"text": "Target" if scenario == "already_at_goal" else "Initial"}, "exists": True}]},
                        "goal_assertion": {"mode": "all", "targets": [{"selector": {"text": "Target"}, "exists": True}]}}
                    request = {"request_id": "fixture", "candidate": {"app:id/a": "x"},
                        "candidate_hash": digest({"app:id/a": "x"}), "oracle": oracle,
                        "artifact_dir": str(directory / scenario)}
                    def command(argv, **kwargs):
                        if argv[0] == "reset-fixture":
                            stdout = json.dumps({"reset_verified": True, "reset_id": "fixture-reset"})
                        elif "pm" in argv:
                            stdout = "package:/data/app/base.apk"
                        elif "sha256sum" in argv:
                            stdout = "wrong" if scenario == "wrong_installed_apk" else apk_hash
                        else:
                            stdout = "fixture log line"
                        return SimpleNamespace(stdout=stdout, stderr="")
                    modules = {"src.apk_management.launcher": SimpleNamespace(AppLauncher=Mock(return_value=launcher)),
                               "src.test_execution.action_executor": SimpleNamespace(ActionExecutor=Mock(return_value=actions))}
                    with patch.dict("sys.modules", modules), patch("text_input_eval.supplementary.android_oracle.subprocess.run", side_effect=command):
                        result = execute(request)
                    self.assertEqual(result["run_status"], "ok" if scenario == "success" else "execution_error")
                    self.assertEqual(result["behavior_verdict"], True if scenario == "success" else None)
                    if scenario in ("already_at_goal", "wrong_installed_apk"):
                        actions.fill_text_inputs.assert_not_called()
                    if scenario == "success":
                        self.assertTrue(result["evidence"])
                        self.assertEqual(result["candidate_hash"], request["candidate_hash"])


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.config = configuration()
        self.ledger = Ledger(self.directory / "results.sqlite3", {"test": "v1"})
        self.calls = []

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def client(self, model, prompt):
        self.calls.append((model, prompt))
        return {"success": True, "data": {"id": f"s{len(self.calls)}", "chat": '{"app:id/a":"fail"}'}}

    def runner(self, client=None, cases=None):
        return Runner(self.config, cases or [case()], self.ledger, client or self.client, FakeEvaluator(), self.directory)

    def trajectories(self):
        return [json.loads(r[0]) for r in self.ledger.db.execute("SELECT result FROM trajectories")]

    def test_shared_failure_four_arms_page_budget_and_resume(self):
        self.assertEqual(self.runner().run(), "finished")
        self.assertEqual(len(self.calls), 9)
        trajectories = self.trajectories()
        self.assertEqual(len(trajectories), 4)
        self.assertEqual(len({t["request_ids"][0] for t in trajectories}), 1)
        self.assertTrue(all(len(t["request_ids"]) == 3 for t in trajectories))
        self.runner().run()
        self.assertEqual(len(self.calls), 9)
        payloads = [json.loads(r[0]) for r in self.ledger.db.execute("SELECT payload FROM requests")]
        self.assertTrue(all(p["id"] == "" for p in payloads))
        self.assertTrue(all(p["modelType"] == "fake" for p in payloads))

    def test_common_success_calls_once_and_stops_every_arm(self):
        def success(model, prompt):
            self.calls.append(prompt)
            return {"data": {"chat": '{"app:id/a":"pass"}'}}
        self.runner(success).run()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([t["first_success_turn"] for t in self.trajectories()], [1] * 4)

    def test_bug_budget_includes_shared_first_and_repeats_are_distinct(self):
        self.config["replicates"] = 2
        self.runner(cases=[case("invalid_bug", "B001")]).run()
        self.assertEqual(len(self.calls), 2 * 117)
        self.assertEqual(len(self.trajectories()), 8)
        self.assertTrue(all(len(t["request_ids"]) == 30 for t in self.trajectories()))

    def test_call_limit_resume_does_not_repeat_shared_call(self):
        self.assertEqual(self.runner().run(max_calls=2), "paused_at_call_limit")
        self.assertEqual(len(self.calls), 2)
        self.runner().run()
        self.assertEqual(len(self.calls), 9)

    def test_capacity_blocks_before_api_and_is_not_failure(self):
        self.config["models"][0]["max_prompt_bytes"] = 1
        self.runner().run()
        self.assertFalse(self.calls)
        self.assertTrue(all(t["status"] == "incomplete" for t in self.trajectories()))
        result = json.loads(self.ledger.db.execute("SELECT result FROM requests").fetchone()[0])
        self.assertFalse(result["submitted"])
        self.assertIsNone(result["evaluation"]["goal_verdict"])

    def test_gateway_false_and_transport_errors_not_syntax_or_business(self):
        for response in ({"success": False, "msg": "context limit"}, {"data": {}}, None):
            self.ledger.db.execute("DELETE FROM requests")
            self.ledger.db.execute("DELETE FROM trajectories")
            self.ledger.db.commit()
            def failed(model, prompt):
                if response is None:
                    raise TimeoutError("do not log headers")
                return response
            self.runner(failed).run()
            result = json.loads(self.ledger.db.execute("SELECT result FROM requests").fetchone()[0])
            self.assertEqual(result["evaluation"]["run_status"], "api_error")
            self.assertIsNone(result["evaluation"]["goal_verdict"])
            self.assertTrue(all(t["status"] == "incomplete" for t in self.trajectories()))

    def test_interrupted_request_is_not_automatically_replayed(self):
        self.ledger.reserve("uncertain", {}, {})
        with self.assertRaises(RuntimeError):
            self.runner().run()
        self.assertFalse(self.calls)

    def test_responded_request_resumes_evaluation_without_api(self):
        self.runner().run()
        row = self.ledger.db.execute("SELECT id FROM requests LIMIT 1").fetchone()
        self.ledger.db.execute("UPDATE requests SET state='responded', result=NULL WHERE id=?", row)
        self.ledger.db.commit()
        self.runner().run()
        self.assertEqual(len(self.calls), 9)

    def test_resume_config_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            Ledger(self.directory / "results.sqlite3", {"test": "changed"})

    def test_e1_noop_excluded_no_model_cost(self):
        self.config["experiments"] = ["E1"]
        self.runner().run()
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(len(self.trajectories()), 6)
        self.assertEqual(sum(t["status"] == "excluded" for t in self.trajectories()), 1)

    def report_manifest(self):
        manifest = {"config": self.config, "cells": [{"experiment": "E2", "task": "valid_pptr",
            "case_id": "P001", "representation": "extracted", "condition": "original"}]}
        self.ledger.db.execute("UPDATE metadata SET value=? WHERE key='manifest'", (json.dumps(manifest),))
        self.ledger.db.commit()

    def test_report_counts_shared_requests_once_and_retains_pending(self):
        self.report_manifest()
        self.runner().run(max_calls=1)
        partial = export_report(self.directory)
        self.assertEqual(partial["physical_submitted_requests"], 1)
        self.assertEqual(partial["planned_trajectories"], 4)
        self.assertEqual(partial["saved_trajectories"], 0)
        self.runner().run()
        final = export_report(self.directory)
        self.assertEqual(final["physical_submitted_requests"], 9)
        self.assertEqual(final["requests_missing_usage"], 9)
        self.assertIsNone(final["token_totals"]["total_tokens"]["sum_known"])
        import csv
        with (self.directory / "summary.csv").open() as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(all(r["completed"] == "1" and r["pending"] == "0" for r in rows))

    def test_refusal_stop_is_shared_and_consumes_first_opportunity(self):
        self.config["refusal_exact_matches"] = ["I cannot provide this input."]
        self.runner(lambda m, p: {"data": {"chat": "I cannot provide this input."}}).run()
        self.assertTrue(all(t["status"] == "refusal" and len(t["request_ids"]) == 1 for t in self.trajectories()))

    def test_successful_continuation_stops_at_second_turn(self):
        count = 0
        def client(model, prompt):
            nonlocal count
            count += 1
            value = "fail" if count == 1 else "pass"
            return {"data": {"chat": json.dumps({"app:id/a": value})}}
        self.runner(client).run()
        self.assertEqual(count, 5)
        self.assertTrue(all(t["first_success_turn"] == 2 for t in self.trajectories()))


class CohortTests(unittest.TestCase):
    def test_full_real_cache_and_legacy_extracted_baseline(self):
        cases = load_cases(ROOT / "tosem_major/apk_info/cache")
        self.assertEqual(sum(c.task == "valid_pptr" for c in cases), 115)
        self.assertEqual(sum(c.task == "invalid_bug" for c in cases), 37)
        self.assertEqual(len({c.aut for c in cases if c.task == "invalid_bug"}), 31)
        for c in cases:
            self.assertEqual(prepare(c, "extracted", "original")["prompt"],
                             PromptGenerator.build_prompt(c.context, "extracted", "full")[0])
            if c.task == "valid_pptr":
                renamed = prepare(c, "xml", "id_rename")
                if not renamed["exclusion_reason"]:
                    self.assertEqual([renamed["inverse_map"][k] for k in renamed["resource_ids"]], c.context.resource_ids)


if __name__ == "__main__":
    unittest.main()
