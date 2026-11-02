"""E0: replay exported existing candidates without generating new model output."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .cli import build_manifest, read_config
from .context import digest, encoded, prepare
from .protocol import Evaluator
from .storage import Ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True,
                        help="JSONL: source_record_id, case_id, candidate (JSON object), optional source_rule_verdict")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = read_config(args.config.resolve())
    if config["endpoint"] != "behavior":
        parser.error("E0 compares rules with executed behavior; endpoint must be behavior")
    cases, manifest = build_manifest(config)
    case_map = {c.case_id: c for c in cases}
    records = [json.loads(line) for line in args.candidates.read_text().splitlines() if line.strip()]
    identities = []
    for record in records:
        if record.get("case_id") not in case_map or not isinstance(record.get("candidate"), dict):
            parser.error("Each replay row needs a known case_id and candidate object")
        if not str(record.get("source_record_id", "")).strip():
            parser.error("Each replay row needs a traceable source_record_id")
        identities.append((record["case_id"], str(record["source_record_id"])))
    if len(set(identities)) != len(identities):
        parser.error("Duplicate source identities; add explicit replay IDs when planning repeated replays")
    manifest["replay_records"] = records
    args.output.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(args.output / "replay.sqlite3", manifest)
    evaluator = Evaluator("behavior", manifest["oracle_cases"], config.get("oracle_command"), config["oracle_timeout"])
    results = []
    try:
        ledger.ensure_resumable()
        for record in records:
            dimensions = {"experiment": "E0", "case_id": record["case_id"], "source_record_id": str(record["source_record_id"])}
            request_id = digest(dimensions)
            saved = ledger.get(request_id)
            if saved:
                if saved["state"] != "done":
                    raise RuntimeError("Uncertain replay; reconcile before repeating device actions")
                result = json.loads(saved["result"])
            else:
                c = case_map[record["case_id"]]
                ledger.reserve(request_id, dimensions, record)
                ledger.evaluating(request_id)
                evaluation = evaluator.evaluate(c, prepare(c, "extracted", "original"), encoded(record["candidate"]),
                                                request_id, args.output / "evidence" / request_id)
                result = {**record, "evaluation": evaluation, "request_id": request_id,
                          "submitted": False, "model_calls": 0}
                ledger.finish(request_id, result)
            results.append(result)
    finally:
        ledger.close()
    comparable = [r for r in results if type(r["evaluation"]["rule_verdict"]) is bool
                  and type(r["evaluation"]["behavior_verdict"]) is bool]
    summary = {"model_calls": 0, "records": len(results), "comparable_records": len(comparable),
        "unresolved_records": len(results) - len(comparable),
        "rule_behavior_matrix": dict(Counter(f"rule={r['evaluation']['rule_verdict']},behavior={r['evaluation']['behavior_verdict']}"
                                             for r in comparable)),
        "run_status_counts": dict(Counter(r["evaluation"]["run_status"] for r in results)),
        "note": "Current rule and current execution are compared per exported candidate; scope is this replay cohort."}
    (args.output / "replay_results.jsonl").write_text("".join(encoded(r) + "\n" for r in results))
    (args.output / "replay_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
