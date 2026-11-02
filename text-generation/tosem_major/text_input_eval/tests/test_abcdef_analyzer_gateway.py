from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from PIL import Image

from text_input_eval.supplementary.abcdef_analyzer import (
    MUSTGatewayAnalyzer,
    _request_json_bytes,
    analyzer_prompt,
)
from text_input_eval.supplementary.abcdef_gateway import (
    ABCDEFSubjectGateway,
    provider_failure_type,
)
from text_input_eval.supplementary.abcdef_preflight import qualification_config_sha256


def valid_diagnosis() -> dict:
    return {
        "execution_status": "FAIL",
        "diagnosis_category": "visible_rejection",
        "diagnosis": "The submitted value remained on screen with a visible error.",
        "support_level": "direct",
        "temporal_summary": "The error appeared after the action.",
        "evidence_refs": ["request:r1:frame.png"],
        "alternative_explanations": [],
        "uncertainty": "No server response body was available.",
        "recommendation": None,
    }


class AnalyzerGatewayTests(unittest.TestCase):
    def test_qualification_hash_excludes_only_formal_launch_switch(self) -> None:
        disabled = {
            "protocol_id": "E2-abcdef-v1", "formal_run_enabled": False,
            "analyzer": {"model": "gpt-5.6-sol"},
        }
        enabled = {**disabled, "formal_run_enabled": True}
        drifted = {**disabled, "analyzer": {"model": "different"}}
        storage_changed = {**disabled, "storage": {"mysql": "changed"}}
        self.assertEqual(
            qualification_config_sha256(disabled),
            qualification_config_sha256(enabled),
        )
        self.assertNotEqual(
            qualification_config_sha256(disabled),
            qualification_config_sha256(drifted),
        )
        self.assertEqual(
            qualification_config_sha256(disabled),
            qualification_config_sha256(storage_changed),
        )

    def test_analyzer_prompt_fails_closed_when_no_visual_refs_exist(self) -> None:
        prompt = analyzer_prompt({
            "execution_status": "FAIL", "frame_manifest": [],
            "ui_observations": {"evidence_available": False},
        })
        self.assertIn("Set evidence_refs to []", prompt)
        self.assertIn("set support_level to insufficient", prompt)

    def test_analyzer_prompt_requires_exact_outcome_echo(self) -> None:
        prompt = analyzer_prompt({"execution_status": "UNKNOWN"})
        self.assertIn(
            "execution_status output field must exactly copy the supplied execution_status",
            prompt,
        )
        self.assertIn("Never reinterpret or change it", prompt)

    def test_analyzer_request_losslessly_escapes_lone_surrogate(self) -> None:
        payload = {"message": json.loads('{"field":"\\ud800"}')["field"]}
        rendered = _request_json_bytes(payload)
        self.assertEqual(json.loads(rendered), payload)
        self.assertIn(b"\\ud800", rendered)

    def test_analyzer_contains_request_serialization_failure(self) -> None:
        analyzer = MUSTGatewayAnalyzer("test-key", api_url="https://example.invalid/api")
        error = UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")
        with mock.patch(
            "text_input_eval.supplementary.abcdef_analyzer._request_json_bytes",
            side_effect=error,
        ):
            result = analyzer.analyze({"execution_status": "FAIL"})
        self.assertEqual(result["run_status"], "infrastructure_error")
        self.assertEqual(result["error_type"], "UnicodeEncodeError")
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_analyzer_freezes_model_effort_schema_and_image(self) -> None:
        captured = []

        def transport(payload: dict) -> dict:
            captured.append(payload)
            return {
                "success": True,
                "data": {
                    "id": "response-1",
                    "sessionId": "response-1",
                    "model": "gpt-5.6-sol",
                    "chat": json.dumps(valid_diagnosis()),
                },
            }

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "evidence.png"
            Image.new("RGB", (4, 4), "white").save(image)
            analyzer = MUSTGatewayAnalyzer("test-key", transport=transport)
            result = analyzer.analyze({
                "case_id": "P001",
                "task_goal": "Complete the task.",
                "candidate": {"field": "bad"},
                "execution_status": "FAIL",
                "ui_observations": {},
                "frame_manifest": [{"evidence_ref": "request:r1:frame.png"}],
            }, image)
        self.assertEqual(result["run_status"], "ok")
        payload = captured[0]
        self.assertEqual(payload["modelType"], "gpt-5.6-sol")
        self.assertEqual(payload["reasoningEffort"], "medium")
        self.assertEqual(payload["sessionId"], "")
        self.assertIn('"recommendation": {"type": "null"}', payload["message"])
        self.assertTrue(payload["imageUrl"].startswith("data:image/png;base64,"))
        self.assertEqual(result["response_model"], "gpt-5.6-sol")

    def test_analyzer_rejects_recommendation(self) -> None:
        value = valid_diagnosis()
        value["recommendation"] = "Use another input"
        analyzer = MUSTGatewayAnalyzer(
            "test-key", transport=lambda _: {
                "success": True,
                "data": {"model": "gpt-5.6-sol", "chat": json.dumps(value)},
            }
        )
        result = analyzer.analyze({"execution_status": "FAIL"})
        self.assertEqual(result["run_status"], "analysis_invalid")
        self.assertIn("recommendations", result["validation_error"])

    def test_analyzer_rejects_invented_evidence_reference(self) -> None:
        analyzer = MUSTGatewayAnalyzer(
            "test-key", transport=lambda _: {
                "success": True,
                "data": {"model": "gpt-5.6-sol", "chat": json.dumps(valid_diagnosis())},
            }
        )
        result = analyzer.analyze({
            "execution_status": "FAIL", "frame_manifest": [], "ui_observations": {}
        })
        self.assertEqual(result["run_status"], "analysis_invalid")

    def test_analyzer_rejects_response_model_drift(self) -> None:
        analyzer = MUSTGatewayAnalyzer(
            "test-key", transport=lambda _: {
                "success": True,
                "data": {"model": "gpt-5.6", "chat": json.dumps(valid_diagnosis())},
            }
        )
        result = analyzer.analyze({"execution_status": "FAIL"})
        self.assertEqual(result, {
            "run_status": "infrastructure_error",
            "error_type": "response_model_mismatch",
        })

    def test_subject_gateway_uses_fresh_session_and_optional_image(self) -> None:
        captured = []
        gateway = ABCDEFSubjectGateway(
            "https://example.invalid/api", "token",
            transport=lambda payload: captured.append(payload) or {
                "success": True,
                "data": {
                    "id": "response-1", "sessionId": "session-1", "model": "subject",
                    "chat": "{}", "time": 1.25,
                    "originContent": json.dumps({"usage": {"total_tokens": 9}}),
                },
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "evidence.png"
            Image.new("RGB", (4, 4), "white").save(image)
            model = {
                "id": "subject",
                "capacity_verified": True,
                "max_prompt_bytes": 10000,
                "feedback_image_verified": True,
                "max_feedback_image_bytes": 10000,
            }
            result = gateway.generate(model, "prompt", [image])
        self.assertEqual(result["run_status"], "ok")
        self.assertEqual(captured[0]["id"], "")
        self.assertTrue(captured[0]["imageUrl"].startswith("data:image/png;base64,"))
        self.assertEqual(result["raw_response"], "{}")
        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(result["response_model"], "subject")
        self.assertEqual(result["usage"], {"total_tokens": 9})
        self.assertEqual(result["provider_elapsed_seconds"], 1.25)
        self.assertEqual(result["provider_response"]["data"]["chat"], "{}")
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_subject_gateway_fails_closed_without_image_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "evidence.png"
            Image.new("RGB", (4, 4), "white").save(image)
            gateway = ABCDEFSubjectGateway(
                "https://example.invalid/api", "token", transport=lambda _: {}
            )
            result = gateway.generate({
                "id": "subject", "capacity_verified": True, "max_prompt_bytes": 10000,
                "feedback_image_verified": False, "max_feedback_image_bytes": None,
            }, "prompt", [image])
        self.assertEqual(result["run_status"], "infrastructure_error")

    def test_subject_gateway_retries_transient_transport_and_records_attempts(self) -> None:
        calls = 0

        def transport(_: dict) -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise urllib.error.URLError("temporary DNS failure")
            return {
                "success": True,
                "data": {"model": "subject", "chat": "{}"},
            }

        gateway = ABCDEFSubjectGateway(
            "https://example.invalid/api", "token", transport=transport,
            max_transport_attempts=3, transport_retry_backoff_seconds=0,
        )
        result = gateway.generate({
            "id": "subject", "capacity_verified": True, "max_prompt_bytes": 10000,
            "feedback_image_verified": True, "max_feedback_image_bytes": 10000,
        }, "prompt")
        self.assertEqual(result["run_status"], "ok")
        self.assertEqual(calls, 2)
        self.assertEqual(
            [row["status"] for row in result["transport_attempts"]],
            ["transport_error", "response_received"],
        )

    def test_quota_classification_does_not_conflate_rate_limit(self) -> None:
        self.assertEqual(
            provider_failure_type({"message": "余额不足，请充值"}),
            "quota_exhausted",
        )
        self.assertEqual(
            provider_failure_type({"code": "INSUFFICIENT_BALANCE"}),
            "quota_exhausted",
        )
        self.assertEqual(
            provider_failure_type({
                "code": "-9",
                "msg": "HTTP 403: account has an overdue balance",
            }),
            "quota_exhausted",
        )
        self.assertEqual(
            provider_failure_type({"code": 402}),
            "quota_exhausted",
        )
        self.assertEqual(
            provider_failure_type({"message": "too many requests"}, http_status=429),
            "http_429",
        )


if __name__ == "__main__":
    unittest.main()
