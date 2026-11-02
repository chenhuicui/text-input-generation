from __future__ import annotations

import unittest

from text_input_eval.supplementary.structured_control_candidates import select_pair


class StructuredControlCandidateTests(unittest.TestCase):
    def test_select_pair_requires_exact_keys_and_nonempty_negative(self) -> None:
        rows = [
            {"id": 4, "parsed_input_json": '{"field":"good","extra":"x"}'},
            {"id": 3, "parsed_input_json": '{"field":"good"}'},
            {"id": 2, "parsed_input_json": '{"field":""}'},
            {"id": 1, "parsed_input_json": '{"field":"bad"}'},
        ]
        pair = select_pair(rows, {"field"}, lambda candidate: candidate["field"] == "good")
        self.assertEqual(pair["status"], "pair_available")
        self.assertEqual(pair["positive_control"]["source"]["db_id"], 3)
        self.assertEqual(pair["negative_control"]["source"]["db_id"], 1)


if __name__ == "__main__":
    unittest.main()
