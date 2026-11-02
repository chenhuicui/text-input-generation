from text_input_eval.core.evaluator import evaluate_row
from text_input_eval.db.repository import LLMTextGenerationRepository


def main():
    repo = LLMTextGenerationRepository()
    rows = repo.fetch_mock_pending(limit=500)

    print(f"Found {len(rows)} mock rows to evaluate.")

    for row in rows:
        row_id = row["id"]
        case_id = row["case_id"]

        try:
            eval_success, placeholder_tag = evaluate_row(row)
            repo.update_placeholder_tag(row_id, placeholder_tag)
            repo.update_eval_success(row_id, eval_success)
            print(
                f"[OK] id={row_id} case_id={case_id} "
                f"eval_success={eval_success} placeholder_tag={placeholder_tag}"
            )
        except Exception as e:
            print(f"[ERR] id={row_id} case_id={case_id} error={e}")


if __name__ == "__main__":
    main()