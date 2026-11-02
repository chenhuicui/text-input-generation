import argparse

from text_input_eval.core.evaluator import evaluate_row
from text_input_eval.db.repository import LLMTextGenerationRepository


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--only-mock", type=int, default=1)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    repo = LLMTextGenerationRepository()

    rows = repo.fetch_rows_by_case_id(
        case_id=args.case_id,
        only_mock=(args.only_mock == 1),
        limit=args.limit,
    )

    print(f"Found {len(rows)} rows to force re-evaluate for case_id={args.case_id}.")

    for row in rows:
        row_id = row["id"]

        try:
            eval_success, placeholder_tag = evaluate_row(row)
            repo.update_placeholder_tag(row_id, placeholder_tag)
            repo.update_eval_success(row_id, eval_success)
            print(
                f"[OK] id={row_id} case_id={args.case_id} "
                f"eval_success={eval_success} placeholder_tag={placeholder_tag}"
            )
        except Exception as e:
            print(f"[ERR] id={row_id} case_id={args.case_id} error={e}")


if __name__ == "__main__":
    main()