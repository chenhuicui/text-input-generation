"""Local A/B import and C/D reporting without mixing oracle/protocol scopes."""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from .context import encoded
from .session_runner import VARIANTS

BASELINE_GAPS = ["case_level_export_has_no_per_turn_payload_or_execution_evidence",
                 "oracle_apk_initial_state_and_assertion_equivalence_unverified",
                 "historical_model_revision_generation_settings_and_session_retention_unverified",
                 "infrastructure_failures_not_separable_from_case_level_export",
                 "historical_output_checks_and_stop_policy_require_audit",
                 "independent_first_draws_not_shared_failures"]


def read_baselines(config, cases):
    """Read existing exports only; retain exact rows and never call MySQL."""
    cohort = {c.case_id: c for c in cases}
    model_ids = {m["id"] for m in config["models"]}
    records, sources, seen = [], [], set()
    for source in config.get("baselines", []):
        if source["arm"] not in ("A", "B"):
            raise ValueError("Historical imports must be A or B")
        path = Path(source["path"])
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        kept, skipped = 0, 0
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for line, row in enumerate(csv.DictReader(stream), 2):
                representation = row["prompt_setting"]
                if (row["case_id"] not in cohort or row["model"] not in model_ids
                        or representation not in config["representations"]
                        or row["prompt_variant"] != VARIANTS[representation]):
                    skipped += 1
                    continue
                case = cohort[row["case_id"]]
                expected_experiment = (("rq1_valid" if case.task == "valid_pptr" else "rq1_invalid")
                                       if source["arm"] == "A" else
                                       ("rq2_valid_feedback" if case.task == "valid_pptr" else "rq2_invalid_feedback"))
                if row["task"] != case.task or row["experiment"] != expected_experiment:
                    raise ValueError("Historical source task/experiment does not match its declared arm")
                key = (source["arm"], case.case_id, row["model"], representation)
                if key in seen:
                    raise ValueError(f"Duplicate baseline cell: {key}")
                seen.add(key)
                success = int(row["success_final"])
                calls = int(row["actual_call_count"])
                index = float(row["first_success_index"]) if row["first_success_index"] else None
                if success not in (0, 1) or calls < 0 or (index is not None and (not index.is_integer() or index < 1)):
                    raise ValueError("Invalid historical success/count fields")
                index = int(index) if index is not None else None
                if bool(success) != (index is not None):
                    raise ValueError("Historical success and first-success fields disagree")
                records.append({"arm": source["arm"], "origin": "historical_case_level_export",
                    "case_id": case.case_id, "aut": case.aut, "task": case.task, "model": row["model"],
                    "representation": representation, "replicate_id": None,
                    "declared_budget": source["budgets"][case.task], "actual_call_count": calls,
                    "reported_success": bool(success), "first_success_turn": index,
                    "endpoint": source.get("metric_scope", "unverified"), "comparison_ready": False,
                    "compatibility_gaps": BASELINE_GAPS, "source_path": str(path), "source_sha256": sha,
                    "source_row": line, "raw_record": row})
                kept += 1
        sources.append({"arm": source["arm"], "path": str(path), "sha256": sha,
                        "included_rows": kept, "out_of_cohort_rows": skipped})
    planned = len(cases) * len(model_ids) * len(config["representations"])
    counts = Counter(r["arm"] for r in records)
    return records, {"sources": sources, "expected_case_cells_per_arm": planned,
                     "available": {arm: counts[arm] for arm in ("A", "B")},
                     "missing_case_cells": {arm: planned - counts[arm] for arm in ("A", "B")},
                     "comparison_ready": False, "compatibility_gaps": BASELINE_GAPS}


def write_csv(path, rows):
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_baselines(output, records, summary):
    output.mkdir(parents=True, exist_ok=True)
    (output / "historical_ab.jsonl").write_text("".join(encoded(r) + "\n" for r in records))
    (output / "historical_ab_audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))


def export_session_report(output: Path):
    database = output / "results.sqlite3"
    if not database.is_file():
        raise FileNotFoundError("No session C/D results ledger")
    db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        if manifest.get("protocol") != "rq2-id-cursor-cd-v2":
            raise ValueError("This report only accepts a session C/D ledger")
        trajectories = [json.loads(r[0]) for r in db.execute("SELECT result FROM trajectories ORDER BY id")]
        requests = [{"request_id": r[0], "dimensions": json.loads(r[1]), "state": r[2],
                     **(json.loads(r[3]) if r[3] else {})}
                    for r in db.execute("SELECT id, dimensions, state, result FROM requests ORDER BY created_at,id")]
    finally:
        db.close()
    for name, values in (("trajectories", trajectories), ("requests", requests)):
        (output / f"{name}.jsonl").write_text("".join(encoded(r) + "\n" for r in values))
    cfg = manifest["config"]
    keys = ("task", "model", "representation", "arm")
    groups, request_groups, planned = defaultdict(list), defaultdict(list), Counter()
    for row in trajectories:
        groups[tuple(row[k] for k in keys)].append(row)
    for row in requests:
        request_groups[tuple(row["dimensions"][k] for k in keys)].append(row)
    for case in manifest["cases"]:
        for model in cfg["models"]:
            for rep in cfg["representations"]:
                for arm in cfg["arms"]:
                    planned[(case["task"], model["id"], rep, arm)] += cfg["replicates"]
    summary_rows = []
    for key, count in sorted(planned.items()):
        values = groups[key]
        completed = [r for r in values if r["status"] in ("success", "budget_exhausted", "refusal")]
        successful = [r for r in completed if r["status"] == "success"]
        initial_failed = [r for r in completed if r["first_failed"]]
        physical = request_groups[key]
        submitted = [r for r in physical if r.get("submitted") is True]
        audits = [r["feedback_audit"] for r in submitted if r.get("feedback_audit")]
        eligible = [a for a in audits if a.get("evidence_eligible")]
        row = {**dict(zip(keys, key)), "origin": "new_session_cd", "endpoint": cfg["endpoint"],
            "planned_trajectories": count, "completed": len(completed),
            "incomplete": sum(r["status"] == "incomplete" for r in values),
            "paused": sum(r["status"] == "paused" for r in values), "pending": count - len(values),
            "successes": len(successful), "success_rate_completed": len(successful) / len(completed) if completed else None,
            "observed_success_fraction_planned": len(successful) / count,
            "first_failed_completed": len(initial_failed),
            "rescue_rate_within_arm": sum(r["status"] == "success" for r in initial_failed) / len(initial_failed) if initial_failed else None,
            "mean_calls_to_success": sum(r["first_success_turn"] for r in successful) / len(successful) if successful else None,
            "physical_submitted_requests": len(submitted),
            "uncertain_requests": sum(r["state"] in ("reserved", "evaluating", "responded") for r in physical),
            "request_elapsed_ms_known": sum(r.get("elapsed_ms", 0) for r in submitted),
            "execution_elapsed_ms_known": sum(r.get("evaluation_elapsed_ms", 0) for r in physical),
            "feedback_sent": len(audits), "failed_execution_feedback_sent": len(eligible),
            "evidence_enhanced_feedback_sent": sum(a["diagnostic_available"] for a in audits),
            "diagnostic_coverage_failed_execution_feedback": sum(a["diagnostic_available"] for a in eligible) / len(eligible) if eligible and key[3] == "D" else None,
            "d_fallback_feedback_sent": sum(a.get("fallback_to_c", False) for a in audits),
            "monetary_cost": None, "root_cause_diagnosis_accuracy": None}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            known = [r["usage"][field] for r in submitted if isinstance(r.get("usage"), dict)
                     and type(r["usage"].get(field)) in (int, float)]
            row[field + "_sum_known"] = sum(known) if known else None
            row[field + "_requests_known"] = len(known)
        for k in (1, 2, 3, 5, 10, 30):
            if k <= cfg["budgets"][key[0]]:
                row[f"success_at_{k}_completed"] = (sum(r["first_success_turn"] <= k for r in successful) / len(completed)) if completed else None
        summary_rows.append(row)
    write_csv(output / "cd_summary.csv", summary_rows)
    historical = manifest.get("historical_baselines", [])
    history_groups = defaultdict(list)
    for row in historical:
        history_groups[tuple(row[k] for k in keys)].append(row)
    comparison = [{**r, "comparison_ready_against_historical_ab": False} for r in summary_rows]
    for key, rows in sorted(history_groups.items()):
        comparison.append({**dict(zip(keys, key)), "origin": "historical_case_level_export",
            "endpoint": rows[0]["endpoint"], "historical_case_cells": len(rows),
            "historical_reported_success_rate": sum(r["reported_success"] for r in rows) / len(rows),
            "historical_recorded_calls": sum(r["actual_call_count"] for r in rows),
            "comparison_ready_against_historical_ab": False})
    write_csv(output / "abcd_inventory.csv", sorted(comparison, key=lambda r: tuple(r[k] for k in keys)))
    summary = {"planned_trajectories": sum(planned.values()), "saved_trajectories": len(trajectories),
        "new_submitted_requests": sum(r.get("submitted") is True for r in requests),
        "pending_or_uncertain_request_states": dict(Counter(r["state"] for r in requests if r["state"] != "done")),
        "run_status_counts": dict(Counter(r.get("evaluation", {}).get("run_status", r["state"]) for r in requests)),
        "endpoint": cfg["endpoint"], "historical_comparison_ready": False,
        "notes": ["Rescue rates condition on each arm's own first failures; first draws are independent.",
                  "Unknown usage or monetary cost is not zero.",
                  "Evidence matching verifies observed text/provenance, not root-cause accuracy.",
                  "Historical A/B metrics are not pooled with new C/D metrics."]}
    (output / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary
