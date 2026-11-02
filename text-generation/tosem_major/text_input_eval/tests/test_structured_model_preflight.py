from __future__ import annotations

import unittest

from text_input_eval.supplementary.structured_model_preflight import capacity_message, probe_model


class FakeClient:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.payloads = []

    def call(self, payload):
        self.payloads.append(payload)
        if not self.available:
            return {"transport_status": "response", "success": False, "provider_code": "-9", "chat": None}
        message = payload["message"]
        if message.startswith("Return exactly E2_PREFLIGHT_OK"):
            chat = "E2_PREFLIGHT_OK"
        elif message.startswith("Memorize the nonce"):
            chat = "STORED"
        elif message.startswith("This is a fresh request"):
            chat = "FRESH_OK"
        else:
            chat = "CAPACITY_OK"
        return {
            "transport_status": "response",
            "success": True,
            "response_model": payload["modelType"],
            "response_id_present": True,
            "chat": chat,
        }


class StructuredModelPreflightTests(unittest.TestCase):
    def test_full_qualification_uses_only_fresh_empty_id_requests(self) -> None:
        client = FakeClient()
        result = probe_model(client, "model-v1", 1024)
        self.assertTrue(result["gateway_verified"])
        self.assertTrue(result["revision_verified"])
        self.assertTrue(result["generation_defaults_verified"])
        self.assertTrue(result["capacity_verified"])
        self.assertTrue(result["fresh_session_isolation_verified"])
        self.assertEqual(result["max_prompt_bytes"], 1024)
        self.assertTrue(all(payload["id"] == "" for payload in client.payloads))
        self.assertTrue(all(set(payload) == {"modelType", "id", "message"} for payload in client.payloads))

    def test_unavailable_model_stops_after_baseline(self) -> None:
        client = FakeClient(available=False)
        result = probe_model(client, "model-v1", 1024)
        self.assertFalse(result["gateway_verified"])
        self.assertFalse(result["capacity_verified"])
        self.assertEqual(len(client.payloads), 1)

    def test_capacity_message_has_exact_utf8_size(self) -> None:
        self.assertEqual(len(capacity_message(1024).encode("utf-8")), 1024)


if __name__ == "__main__":
    unittest.main()
