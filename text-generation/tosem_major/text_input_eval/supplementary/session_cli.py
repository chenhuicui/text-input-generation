"""Session C/D entry point: plan/import-ab are local; only run invokes backends."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from .context import ROOT, digest, load_cases
from .protocol import Evaluator
from .session_report import export_session_report, read_baselines, write_baselines
from .session_runner import SessionGateway, SessionRunner, prepare_session
from .storage import Ledger

PROTOCOL = "rq2-id-cursor-cd-v2"


def read_session_config(path: Path):
    cfg = json.loads(path.read_text())
    allowed = {"protocol_version", "cache_root", "models", "representations", "arms", "replicates",
               "budgets", "schedule_seed", "endpoint", "oracle_manifest", "oracle_command", "oracle_timeout",
               "api_url", "api_timeout", "case_ids", "refusal_exact_matches", "baselines"}
    if set(cfg) - allowed or cfg["protocol_version"] != PROTOCOL:
        raise ValueError("Unknown config fields or session protocol version")
    for key, values in (("representations", {"extracted", "xml", "vision"}), ("arms", {"C", "D"})):
        if not cfg[key] or len(cfg[key]) != len(set(cfg[key])) or set(cfg[key]) - values:
            raise ValueError(f"Invalid {key}")
    if cfg["endpoint"] not in ("rule", "behavior") or ("D" in cfg["arms"] and cfg["endpoint"] != "behavior"):
        raise ValueError("C can use rule or behavior; D requires behavior for execution evidence")
    def positive(x):
        return type(x) is int and x > 0
    for key in ("replicates", "api_timeout", "oracle_timeout"):
        if not positive(cfg[key]):
            raise ValueError(f"Invalid {key}")
    if type(cfg["schedule_seed"]) is not int:
        raise ValueError("schedule_seed must be an integer")
    if set(cfg["budgets"]) != {"valid_pptr", "invalid_bug"} or not all(positive(x) for x in cfg["budgets"].values()):
        raise ValueError("Positive total call budgets for both tasks are required")
    if len(cfg["case_ids"]) != len(set(cfg["case_ids"])):
        raise ValueError("Duplicate case_ids")
    if not isinstance(cfg["refusal_exact_matches"], list) or not all(isinstance(x, str) for x in cfg["refusal_exact_matches"]):
        raise ValueError("refusal_exact_matches must be a list of strings")
    if not cfg["models"] or len({m["id"] for m in cfg["models"]}) != len(cfg["models"]):
        raise ValueError("Models must be nonempty and unique")
    model_keys = {"id", "max_visible_text_bytes", "max_request_bytes", "gateway_verified", "gateway_settings",
                  "tokenizer_json", "context_window_tokens", "reserved_tokens"}
    for model in cfg["models"]:
        if set(model) - model_keys:
            raise ValueError("Unknown model settings; credentials belong in the environment")
        if not all(positive(model[k]) for k in ("max_visible_text_bytes", "max_request_bytes")):
            raise ValueError("Positive local byte guards are required")
        if model.get("tokenizer_json"):
            if not all(positive(model.get(k)) for k in ("context_window_tokens", "reserved_tokens")):
                raise ValueError("Tokenizer requires a recorded context window and token reserve")
            model["tokenizer_json"] = str((path.parent / model["tokenizer_json"]).resolve())
    for key in ("cache_root", "oracle_manifest"):
        if cfg.get(key):
            cfg[key] = str((path.parent / cfg[key]).resolve())
    command = cfg.get("oracle_command")
    if command is not None and (not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command)):
        raise ValueError("oracle_command must be a nonempty argv array")
    for source in cfg.get("baselines", []):
        if set(source) - {"arm", "path", "metric_scope", "budgets"}:
            raise ValueError("Unknown historical source fields")
        if source["arm"] not in ("A", "B"):
            raise ValueError("Only historical A/B sources are accepted")
        if set(source["budgets"]) != {"valid_pptr", "invalid_bug"} or not all(positive(x) for x in source["budgets"].values()):
            raise ValueError("Declare historical total budgets separately")
        source["path"] = str((path.parent / source["path"]).resolve())
    return cfg


def live_readiness_issues(config, cases, oracles):
    """Local metadata checks only; these do not attest live backend readiness."""
    issues = []
    for model in config["models"]:
        settings = model.get("gateway_settings") or {}
        required = ("model_revision", "generation_parameters", "context_limit_source", "id_chain_retention")
        if model.get("gateway_verified") is not True or any(settings.get(k) is None or settings.get(k) == "" for k in required):
            issues.append(f"gateway_metadata_unverified:{model['id']}")
    if config["endpoint"] == "behavior":
        if not config.get("oracle_command"):
            issues.append("missing_oracle_command")
        for case in cases:
            oracle = oracles.get(case.case_id, {})
            required = ("version", "apk_sha256", "reset_description", "definition_source", "frozen_files")
            if (oracle.get("status") != "ready" or oracle.get("task") != case.task
                    or not all(oracle.get(k) for k in required)):
                issues.append(f"oracle_definition_not_ready:{case.case_id}")
    return issues


def build_session_manifest(config):
    all_cases = load_cases(Path(config["cache_root"]))
    selected = set(config["case_ids"])
    if selected - {c.case_id for c in all_cases}:
        raise ValueError("Unknown configured case IDs")
    cases = [c for c in all_cases if not selected or c.case_id in selected]
    oracles = json.loads(Path(config["oracle_manifest"]).read_text())["cases"] if config.get("oracle_manifest") else {}
    cells = []
    for case in cases:
        for representation in config["representations"]:
            prepared = prepare_session(case, representation, Path(config["cache_root"]))
            cells.append({"case_id": case.case_id, "representation": representation,
                **{k: prepared[k] for k in ("prompt_hash", "image_sha256", "resource_ids", "output_check_version")}})
    sources = set(Path(__file__).parent.glob("*.py"))
    sources.update(ROOT / "tosem_major/text_input_eval/services" / name for name in ("one_shot.py", "feedback_based.py"))
    sources.update((ROOT / "tosem_major/text_input_eval/validators").rglob("*.py"))
    sources.update((ROOT / "src").rglob("*.py"))
    source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}
    dependencies = {}
    for oracle in oracles.values():
        for filename, expected in oracle.get("frozen_files", {}).items():
            actual = hashlib.sha256(Path(filename).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"Changed oracle dependency: {filename}")
            dependencies[filename] = actual
    for model in config["models"]:
        if model.get("tokenizer_json"):
            path = Path(model["tokenizer_json"])
            dependencies[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    historical, historical_audit = read_baselines(config, cases)
    manifest = {"protocol": PROTOCOL, "config": config, "source_hashes": source_hashes,
        "dependency_hashes": dependencies, "oracle_cases": oracles, "cells": cells,
        "available_case_ids": [c.case_id for c in all_cases],
        "cases": [{"case_id": c.case_id, "task": c.task, "aut": c.aut, "source_hash": c.source_hash,
                   "binding_issues": c.binding_issues} for c in cases],
        "historical_baselines": [{**{k: v for k, v in r.items() if k != "raw_record"},
                                  "raw_record_hash": digest(r["raw_record"])} for r in historical],
        "historical_audit": historical_audit,
        "conversation_protocol": {"initial_draw": "independent_per_trajectory",
            "continuation": "response.data.id -> next request.id",
            "vision": "image_on_initial_request_only", "explicit_history_window": 5,
            "conversation_history": "gateway-bound id chain; no local history truncation",
            "unknown_or_infrastructure": "incomplete; never task-negative feedback"},
        "local_readiness_issues": live_readiness_issues(config, cases, oracles)}
    return cases, manifest, historical


def write_session_plan(output: Path, manifest):
    output.mkdir(parents=True, exist_ok=True)
    path = output / "manifest.json"
    if path.exists() and digest(json.loads(path.read_text())) != digest(manifest):
        raise ValueError("Different frozen protocol/input/source; use a new output directory")
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    cfg = manifest["config"]
    multiplier = len(cfg["models"]) * len(cfg["representations"]) * len(cfg["arms"]) * cfg["replicates"]
    tasks = Counter(c["task"] for c in manifest["cases"])
    summary = {"protocol": PROTOCOL, "manifest_hash": digest(manifest), "cohort": dict(tasks),
        "aut_counts": {task: len({c["aut"] for c in manifest["cases"] if c["task"] == task and c["aut"] != "unknown"}) for task in tasks},
        "models": [m["id"] for m in cfg["models"]], "representations": cfg["representations"], "new_arms": cfg["arms"],
        "replicates": cfg["replicates"], "total_call_budgets_including_first": cfg["budgets"],
        "planned_new_trajectories": len(manifest["cases"]) * multiplier,
        "max_new_model_calls_before_success_stopping": sum(tasks[t] * cfg["budgets"][t] for t in tasks) * multiplier,
        "historical_ab_available": manifest["historical_audit"]["available"],
        "historical_ab_missing_case_cells": manifest["historical_audit"]["missing_case_cells"],
        "historical_comparison_ready": False, "endpoint": cfg["endpoint"],
        "local_readiness_issues": manifest["local_readiness_issues"],
        "model_calls_executed_by_plan": 0, "app_executions_by_plan": 0}
    (output / "plan_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    template_path = output / "oracle_template.json"
    if not template_path.exists():
        template = {"cases": {c["case_id"]: {"status": "evidence-missing", "task": c["task"], "aut": c["aut"],
            "version": "", "apk_sha256": "", "reset_description": "", "definition_source": "", "frozen_files": {},
            "public_diagnostic_targets": [], "binding_issues": c["binding_issues"]} for c in manifest["cases"]}}
        template_path.write_text(json.dumps(template, ensure_ascii=False, indent=2))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "import-ab", "run", "report"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-new-calls", type=int)
    args = parser.parse_args(argv)
    if args.mode == "report":
        print(json.dumps(export_session_report(args.output), ensure_ascii=False, indent=2))
        return
    if not args.config:
        parser.error("--config is required")
    if args.max_new_calls is not None and args.max_new_calls <= 0:
        parser.error("--max-new-calls must be positive")
    cfg = read_session_config(args.config.resolve())
    cases, manifest, historical = build_session_manifest(cfg)
    plan = write_session_plan(args.output, manifest)
    if args.mode in ("plan", "import-ab"):
        if args.mode == "import-ab":
            write_baselines(args.output, historical, manifest["historical_audit"])
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    if manifest["local_readiness_issues"]:
        parser.error("Live run metadata is incomplete; inspect plan_summary.json. No model/app calls made.")
    key = os.environ.get("MUST_LAB_API_TOKEN", "").strip()
    if not key:
        parser.error("MUST_LAB_API_TOKEN is empty")
    ledger = Ledger(args.output / "results.sqlite3", manifest)
    try:
        runner = SessionRunner(cfg, cases, ledger, SessionGateway(cfg["models"], key, cfg["api_url"], cfg["api_timeout"]),
            Evaluator(cfg["endpoint"], manifest["oracle_cases"], cfg.get("oracle_command"), cfg["oracle_timeout"]), args.output)
        status = runner.run(args.max_new_calls)
    finally:
        ledger.close()
    print(json.dumps({"status": status, **export_session_report(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
