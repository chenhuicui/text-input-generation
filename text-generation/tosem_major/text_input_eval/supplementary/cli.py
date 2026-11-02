"""Run with PYTHONPATH=tosem_major python -m text_input_eval.supplementary ..."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

from .context import ROOT, digest, encoded, load_cases, prepare
from .protocol import ARMS, Evaluator
from .runner import CapacityGuard, Gateway, Runner
from .storage import Ledger


def read_config(path: Path):
    config = json.loads(path.read_text())
    allowed = {"protocol_version", "cache_root", "models", "experiments", "replicates", "schedule_seed",
               "endpoint", "oracle_manifest", "oracle_command", "oracle_timeout", "api_url", "api_timeout",
               "case_ids", "refusal_exact_matches", "e1", "e2"}
    if set(config) - allowed:
        raise ValueError(f"Unknown config keys: {sorted(set(config) - allowed)}")
    def positive(value):
        return type(value) is int and value > 0
    if not positive(config["replicates"]) or config["endpoint"] not in ("rule", "behavior"):
        raise ValueError("Invalid replicate count or endpoint")
    if (not config["experiments"] or len(set(config["experiments"])) != len(config["experiments"])
            or set(config["experiments"]) - {"E1", "E2"}):
        raise ValueError("Experiments must be unique E1/E2")
    models = config["models"]
    if not models or len({m["id"] for m in models}) != len(models):
        raise ValueError("Models must be nonempty and unique")
    for m in models:
        if set(m) - {"id", "max_prompt_bytes", "gateway_verified", "gateway_settings", "tokenizer_json",
                     "reserved_tokens", "context_window_tokens"}:
            raise ValueError("Unknown model config keys; credentials belong in the environment")
        if not positive(m["max_prompt_bytes"]):
            raise ValueError("max_prompt_bytes must be positive")
        if m.get("tokenizer_json"):
            if not positive(m.get("reserved_tokens")) or not positive(m.get("context_window_tokens")):
                raise ValueError("Local tokenizer requires context_window_tokens and reserved_tokens")
            m["tokenizer_json"] = str((path.parent / m["tokenizer_json"]).resolve())
    for key in ("history_limit", "max_candidate_chars", "max_diagnostic_chars"):
        if not positive(config["e2"][key]):
            raise ValueError(f"Invalid {key}")
    if set(config["e2"]["budgets"]) != {"valid_pptr", "invalid_bug"} or not all(
            positive(x) for x in config["e2"]["budgets"].values()):
        raise ValueError("Budgets must include both tasks and include the shared first draw")
    for key, allowed_values in (("representations", {"extracted", "xml"}),
                                ("conditions", {"original", "id_rename", "irrelevant_nodes"})):
        values = config["e1"][key]
        if not values or len(set(values)) != len(values) or set(values) - allowed_values:
            raise ValueError(f"Invalid E1 {key}")
    if not positive(config["e1"]["noise_nodes"]):
        raise ValueError("noise_nodes must be positive")
    for key in ("cache_root", "oracle_manifest"):
        if config.get(key):
            config[key] = str((path.parent / config[key]).resolve())
    if config.get("oracle_command") and (not isinstance(config["oracle_command"], list)
                                         or not all(isinstance(x, str) for x in config["oracle_command"])):
        raise ValueError("oracle_command must be an argv array (no shell interpolation)")
    return config


def build_manifest(config: dict):
    all_cases = load_cases(Path(config["cache_root"]))
    selected = config.get("case_ids")
    if selected and set(selected) - {c.case_id for c in all_cases}:
        raise ValueError("Unknown case_ids in configured cohort")
    cases = [c for c in all_cases if not selected or c.case_id in selected]
    oracle_cases = json.loads(Path(config["oracle_manifest"]).read_text())["cases"] if config.get("oracle_manifest") else {}
    guard = CapacityGuard(config["models"])
    cells = []
    for case in cases:
        variants = []
        if "E1" in config["experiments"] and case.task == "valid_pptr":
            variants += [("E1", r, c) for r in config["e1"]["representations"] for c in config["e1"]["conditions"]]
        if "E2" in config["experiments"]:
            variants.append(("E2", "extracted", "original"))
        for experiment, representation, condition in variants:
            prepared = prepare(case, representation, condition, config["e1"]["noise_nodes"])
            if experiment == "E2" and oracle_cases.get(case.case_id, {}).get("public_specs"):
                prepared["prompt"] += "\nPublic specifications:\n" + json.dumps(oracle_cases[case.case_id]["public_specs"], ensure_ascii=False)
                prepared["prompt_hash"] = digest(prepared["prompt"])
            cells.append({"experiment": experiment, "case_id": case.case_id, "task": case.task,
                "representation": representation, "condition": condition,
                **{k: v for k, v in prepared.items() if k not in ("xml", "prompt")},
                "initial_capacity": {m["id"]: guard.check(m["id"], prepared["prompt"]) for m in config["models"]}})
    sources = list(Path(__file__).parent.glob("*.py"))
    sources += [ROOT / "tosem_major/text_input_eval/services/one_shot.py"]
    sources += list((ROOT / "tosem_major/text_input_eval/validators").rglob("*.py"))
    sources += list((ROOT / "src").rglob("*.py"))
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}
    extra_hashes = {}
    for m in config["models"]:
        if m.get("tokenizer_json"):
            extra_hashes[m["tokenizer_json"]] = hashlib.sha256(Path(m["tokenizer_json"]).read_bytes()).hexdigest()
    # Oracle definition dependencies are explicit, never guessed from names.
    for oracle in oracle_cases.values():
        for filename, expected_hash in oracle.get("frozen_files", {}).items():
            actual = hashlib.sha256(Path(filename).read_bytes()).hexdigest()
            if actual != expected_hash:
                raise ValueError(f"Oracle dependency changed: {filename}")
            extra_hashes[filename] = actual
    manifest = {"schema_version": 1, "config": config, "source_hashes": hashes,
                "dependency_hashes": extra_hashes, "oracle_cases": oracle_cases,
                "available_case_ids": [c.case_id for c in all_cases],
                "cases": [asdict(c) for c in cases], "cells": cells}
    return cases, manifest


def write_plan(output: Path, manifest: dict):
    output.mkdir(parents=True, exist_ok=True)
    target = output / "manifest.json"
    if target.exists() and digest(json.loads(target.read_text())) != digest(manifest):
        raise ValueError("Output directory contains a different frozen manifest; choose a new directory")
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    config = manifest["config"]
    multiplier = len(config["models"]) * config["replicates"]
    theoretical, applicable = Counter(), Counter()
    reasons = Counter()
    for c in manifest["cells"]:
        calls = multiplier if c["experiment"] == "E1" else multiplier * (1 + 4 * (config["e2"]["budgets"][c["task"]] - 1))
        theoretical[c["experiment"]] += calls
        if c["exclusion_reason"]:
            reasons[c["exclusion_reason"]] += 1
        else:
            applicable[c["experiment"]] += calls
    summary = {"manifest_hash": digest(manifest), "endpoint": config["endpoint"],
        "cohort": dict(Counter(c["context"]["task"] for c in manifest["cases"])),
        "aut_counts": {task: len({c["aut"] for c in manifest["cases"] if c["context"]["task"] == task and c["aut"] != "unknown"})
                       for task in ("valid_pptr", "invalid_bug")},
        "theoretical_max_model_calls": dict(theoretical), "structurally_applicable_max_model_calls": dict(applicable),
        "excluded_condition_cells": dict(reasons),
        "binding_issue_cases": [c["context"]["case_id"] for c in manifest["cases"] if c["binding_issues"]],
        "oracle_ready_cases": sum(v.get("status") == "ready" for v in manifest["oracle_cases"].values()),
        "gateway_unverified_models": [m["id"] for m in config["models"] if not m.get("gateway_verified")],
        "note": "Call ceilings precede capacity/oracle gating and success stopping; repetitions are not seeds."}
    (output / "plan_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    # A reviewable template, not an invented ready oracle.
    template = {"cases": {c["context"]["case_id"]: {"status": "evidence-missing", "version": "",
        "task": c["context"]["task"], "aut": c["aut"], "binding_issues": c["binding_issues"],
        "apk_sha256": "", "reset_description": "", "definition_source": "", "public_specs": [],
        "frozen_files": {}} for c in manifest["cases"]}}
    template_path = output / "oracle_template.json"
    if not template_path.exists():
        template_path.write_text(json.dumps(template, ensure_ascii=False, indent=2))
    return summary


def export_report(output: Path):
    if not (output / "results.sqlite3").is_file():
        raise FileNotFoundError("No results.sqlite3 in the requested run directory")
    db = sqlite3.connect(output / "results.sqlite3")
    manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
    records = [json.loads(r[0]) for r in db.execute("SELECT result FROM trajectories ORDER BY id")]
    requests = []
    for request_id, dims, state, result in db.execute("SELECT id, dimensions, state, result FROM requests ORDER BY created_at, id"):
        requests.append({"request_id": request_id, "dimensions": json.loads(dims), "state": state,
                         **(json.loads(result) if result else {})})
    db.close()
    (output / "trajectories.jsonl").write_text("".join(encoded(r) + "\n" for r in records))
    (output / "requests.jsonl").write_text("".join(encoded(r) + "\n" for r in requests))
    groups = defaultdict(list)
    keys = ("experiment", "task", "model", "representation", "condition", "arm", "endpoint")
    for row in records:
        groups[tuple(row[k] for k in keys)].append(row)
    # Include groups not yet run, retaining planned denominators on partial runs.
    cfg = manifest["config"]
    expected = Counter()
    for cell in manifest["cells"]:
        for m in cfg["models"]:
            for arm in (("single",) if cell["experiment"] == "E1" else ARMS):
                key = (cell["experiment"], cell["task"], m["id"], cell["representation"], cell["condition"], arm, cfg["endpoint"])
                expected[key] += cfg["replicates"]
    rows = []
    for key, planned in sorted(expected.items()):
        values = groups[key]
        completed = [r for r in values if r["status"] in ("success", "budget_exhausted", "refusal")]
        rescued = [r for r in completed if r["shared_first_failed"]]
        success = sum(r["status"] == "success" for r in completed)
        row = {**dict(zip(keys, key)), "planned_trajectories": planned, "completed": len(completed),
               "incomplete": sum(r["status"] == "incomplete" for r in values),
               "excluded": sum(r["status"] == "excluded" for r in values), "pending": planned - len(values),
               "successes": success, "observed_success_fraction_planned": success / planned,
               "success_rate_completed": success / len(completed) if completed else None,
               "shared_first_failed_completed": len(rescued),
               "rescue_rate_completed": sum(r["status"] == "success" for r in rescued) / len(rescued) if rescued else None}
        # Success@k denominators are completed trajectories; early refusals count
        # as failure. API/device errors remain incomplete at every cutoff.
        for k in (1, 2, 3, 5, 10, 30):
            budget = 1 if key[0] == "E1" else cfg["e2"]["budgets"][key[1]]
            if k <= budget:
                row[f"success_at_{k}_completed"] = (sum(r["first_success_turn"] is not None and r["first_success_turn"] <= k
                    for r in completed) / len(completed)) if completed else None
        rows.append(row)
    columns = list(dict.fromkeys(k for r in rows for k in r))
    with (output / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    completed_requests = [r for r in requests if r.get("submitted") is True]
    # Costs are physical requests, so the shared first draw is counted once.
    usage = [r["usage"] for r in completed_requests if isinstance(r.get("usage"), dict)]
    token_totals = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        known = [u[field] for u in usage if type(u.get(field)) in (int, float)]
        token_totals[field] = {"sum_known": sum(known) if known else None, "requests_with_field": len(known)}
    request_groups = defaultdict(list)
    for request in requests:
        d = request["dimensions"]
        request_groups[(d["experiment"], d["task"], d["model"], d["representation"], d["condition"], d["arm"])].append(request)
    cost_rows = []
    for key, group in sorted(request_groups.items()):
        submitted = [r for r in group if r.get("submitted") is True]
        cost_rows.append({**dict(zip(keys[:6], key)), "physical_submitted_requests": len(submitted),
            "elapsed_ms": sum(r.get("elapsed_ms", 0) for r in submitted),
            "evaluation_elapsed_ms": sum(r.get("evaluation_elapsed_ms", 0) for r in group),
            "diagnostic_available_requests": sum(any(d.get("public") is True and d.get("source") in
                ("ui_observation", "public_spec_check", "output_parser", "output_schema")
                for d in r.get("evaluation", {}).get("diagnostics", [])) for r in submitted),
            "requests_missing_usage": sum(not isinstance(r.get("usage"), dict) for r in submitted)})
    with (output / "physical_costs.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(cost_rows[0]) if cost_rows else list(keys[:6]))
        writer.writeheader()
        writer.writerows(cost_rows)
    summary = {"physical_submitted_requests": len(completed_requests),
               "uncertain_requests": sum(r["state"] in ("reserved", "evaluating") for r in requests),
               "run_status_counts": dict(Counter(r.get("evaluation", {}).get("run_status", r["state"]) for r in requests)),
               "requests_with_usage": len(usage), "requests_missing_usage": len(completed_requests) - len(usage),
               "token_totals": token_totals,
               "total_request_elapsed_ms": sum(r.get("elapsed_ms", 0) for r in completed_requests),
               "total_evaluation_elapsed_ms": sum(r.get("evaluation_elapsed_ms", 0) for r in requests),
               "planned_trajectories": sum(expected.values()), "saved_trajectories": len(records),
               "metric_scope": cfg["endpoint"], "cost_note": "Raw usage is exported per physical request; unknown usage/cost is not zero."}
    (output / "run_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "run", "report"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-new-calls", type=int, help="Pause at this physical call count; resume with the same command/directory")
    args = parser.parse_args()
    if args.mode == "report":
        print(json.dumps(export_report(args.output), ensure_ascii=False, indent=2))
        return
    if args.config is None:
        parser.error("--config is required for plan/run")
    if args.max_new_calls is not None and args.max_new_calls <= 0:
        parser.error("--max-new-calls must be positive")
    config = read_config(args.config.resolve())
    cases, manifest = build_manifest(config)
    plan = write_plan(args.output, manifest)
    if args.mode == "plan":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    if plan["gateway_unverified_models"]:
        parser.error("Freeze verified gateway model/version/settings/capacity before live calls (gateway_verified).")
    if any(not m.get("gateway_settings") for m in config["models"]):
        parser.error("Record the verified gateway model revision, generation defaults and capacity in gateway_settings.")
    if config["endpoint"] == "behavior":
        for case in cases:
            oracle = manifest["oracle_cases"].get(case.case_id, {})
            required = ("version", "apk_sha256", "reset_description", "definition_source", "frozen_files")
            if oracle.get("status") != "ready" or not all(oracle.get(k) for k in required):
                parser.error(f"Behavior oracle is not ready: {case.case_id}. Fix the frozen cohort/oracle before model calls.")
        if not config.get("oracle_command"):
            parser.error("Behavior endpoint requires oracle_command")
    key = os.environ.get("MUST_LAB_API_TOKEN", "").strip()
    if not key:
        parser.error("MUST_LAB_API_TOKEN is empty")
    ledger = Ledger(args.output / "results.sqlite3", manifest)
    try:
        runner = Runner(config, cases, ledger, Gateway(config["models"], key, config["api_url"], config["api_timeout"]),
            Evaluator(config["endpoint"], manifest["oracle_cases"], config.get("oracle_command"), config["oracle_timeout"]), args.output)
        status = runner.run(args.max_new_calls)
    finally:
        ledger.close()
    print(json.dumps({"status": status, **export_report(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
