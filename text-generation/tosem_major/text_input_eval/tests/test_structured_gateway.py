from __future__ import annotations

import unittest

from text_input_eval.supplementary.structured_gateway import PromptCapacityError, StructuredGateway


class StructuredGatewayTests(unittest.TestCase):
    def test_fresh_three_field_payload_and_response(self) -> None:
        seen = []

        def transport(payload):
            seen.append(payload)
            return {"success": True, "data": {"id": "provider-response", "chat": '{"field":"value"}'}}

        gateway = StructuredGateway("http://example.invalid", "secret", transport=transport)
        result = gateway.generate(
            {"id": "model", "capacity_verified": True, "max_prompt_bytes": 100},
            "prompt",
        )
        self.assertEqual(seen, [{"modelType": "model", "id": "", "message": "prompt"}])
        self.assertEqual(result["run_status"], "ok")
        self.assertEqual(result["raw_response"], '{"field":"value"}')

    def test_unfrozen_or_exceeded_capacity_is_rejected_before_transport(self) -> None:
        gateway = StructuredGateway("http://example.invalid", "secret", transport=lambda _: self.fail("called"))
        with self.assertRaises(PromptCapacityError):
            gateway.generate({"id": "model", "capacity_verified": False, "max_prompt_bytes": 100}, "prompt")
        with self.assertRaises(PromptCapacityError):
            gateway.generate({"id": "model", "capacity_verified": True, "max_prompt_bytes": 2}, "prompt")


if __name__ == "__main__":
    unittest.main()
