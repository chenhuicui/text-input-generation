"""Offline prompt-isolation and deterministic Android smoke for B002/B018.

This command never invokes a model.  It materializes the three prompt views,
checks that evaluator-only trigger material stays absent, and optionally runs
one frozen CONTROL and TARGET fixture through the Android adapter.  The two
fixtures are evaluator evidence only and are never appended to a model prompt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .spec_feasibility_android import execute_attempt
from .spec_feasibility_contract import (
    candidate_partition,
    render_prompt,
    required_resource_ids,
    validate_config,
)
from .spec_feasibility_pilot import _frozen_baselines


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2,
    ) + "\n"
    path.write_text(text, encoding="utf-8")


def _prompt_isolation(
    config_path: Path,
    config: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    baselines = _frozen_baselines(config_path, config)
    rows: list[dict[str, Any]] = []
    for case in config["cases"]:
        case_id = case["case_id"]
        keys = required_resource_ids(case)
        control = case["hidden_smoke_fixtures"]["control"]
        previous = [{
            "turn": 1,
            "candidate": control,
            "usable_response": True,
            "candidate_target_partition": "CONTROL",
            "o_spec": "PASS",
            "public_observations": [
                "The frozen action completed and the App remained alive and foreground."
            ],
            "unknown_reason": None,
        }]
        prompts: dict[str, str] = {}
        audits: dict[str, dict[str, Any]] = {}
        for arm in ("P0", "P1", "P2"):
            prompt, audit = render_prompt(
                case,
                arm,
                previous if arm == "P2" else [],
                baselines[case_id],
            )
            prompts[arm] = prompt
            audits[arm] = audit
            _write(output_dir / "prompts" / case_id / f"{arm}.txt", prompt)
            _write(output_dir / "prompts" / case_id / f"{arm}.audit.json", audit)

        serialized_fixtures = [
            json.dumps(fixture, ensure_ascii=False, sort_keys=True)
            for fixture in case["hidden_smoke_fixtures"].values()
        ]
        checks = {
            "p0_is_exact_original_prompt": prompts["P0"] == baselines[case_id],
            "p1_preserves_original_prompt_prefix": prompts["P1"].startswith(baselines[case_id]),
            "p2_preserves_original_prompt_prefix": prompts["P2"].startswith(baselines[case_id]),
            "all_original_resource_ids_present": all(
                key in baselines[case_id] for key in keys
            ),
            "hidden_input_condition_absent": all(
                case["requirement"]["input_condition"] not in prompts[arm]
                for arm in ("P0", "P1", "P2")
            ),
            "hidden_issue_url_absent": all(
                case["hidden_oracle_provenance"]["url"] not in prompts[arm]
                for arm in ("P0", "P1", "P2")
            ),
            "target_partition_feedback_absent": (
                "candidate_target_partition" not in prompts["P2"]
                and "hidden bug-trigger partition is not disclosed" in prompts["P2"]
            ),
            "hidden_smoke_fixture_not_serialized": all(
                fixture not in prompts[arm]
                for fixture in serialized_fixtures
                for arm in ("P1", "P2")
            ),
        }
        rows.append({
            "case_id": case_id,
            "checks": checks,
            "passed": all(checks.values()),
            "baseline_prompt_sha256": audits["P0"]["baseline_prompt_sha256"],
            "p1_prompt_sha256": audits["P1"]["prompt_sha256"],
            "p2_prompt_sha256": audits["P2"]["prompt_sha256"],
        })
    return {
        "passed": all(row["passed"] for row in rows),
        "cases": rows,
    }


def _android_smoke(config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in config["cases"]:
        case_id = case["case_id"]
        results: dict[str, dict[str, Any]] = {}
        partitions: dict[str, str] = {}
        for fixture_name in ("control", "target"):
            candidate = case["hidden_smoke_fixtures"][fixture_name]
            partition = candidate_partition(case, candidate)
            partitions[fixture_name] = partition
            request = {
                "protocol_id": config["protocol_id"],
                "case": case,
                "candidate": candidate,
                "replicate_id": 0,
                "turn": 1,
                "artifact_dir": str(
                    output_dir / "android" / case_id / fixture_name / "execution"
                ),
            }
            result = execute_attempt(request)
            _write(
                output_dir / "android" / case_id / fixture_name / "execution_result.json",
                result,
            )
            results[fixture_name] = result

        control = results["control"]
        target = results["target"]
        control_valid = (
            partitions["control"] == "CONTROL"
            and control.get("run_status") == "ok"
            and control.get("o_spec") == "PASS"
        )
        target_observed = (
            partitions["target"] == "TARGET"
            and target.get("run_status") == "ok"
            and target.get("o_spec") in {"PASS", "FAIL"}
            and target.get("observation", {}).get(
                "candidate_bindings_match_after_fill"
            ) is True
        )
        violation_reproduced = target_observed and target.get("o_spec") == "FAIL"
        repeat_ready = control_valid and violation_reproduced
        blockers: list[str] = []
        if not control_valid:
            blockers.append(
                "The frozen CONTROL path did not establish a live, foreground, non-crashing baseline."
            )
        if control_valid and not violation_reproduced:
            blockers.append(
                "The frozen TARGET path did not reproduce a candidate-bound violation."
            )
        if target.get("observation", {}).get(
            "candidate_bindings_match_after_fill"
        ) is False:
            blockers.append(
                "The App normalized or changed the TARGET candidate before the frozen oracle action, so the hidden trigger was not established at execution time."
            )
        blockers.extend(case["execution"].get("known_blockers", []))
        rows.append({
            "case_id": case_id,
            "control_partition": partitions["control"],
            "target_partition": partitions["target"],
            "control": {
                "run_status": control.get("run_status"),
                "o_spec": control.get("o_spec"),
                "unknown_reason": control.get("unknown_reason"),
                "interaction_error_type": control.get("interaction_error_type"),
                "candidate_bindings_match_after_fill": control.get(
                    "observation", {}
                ).get("candidate_bindings_match_after_fill"),
                "observed_binding_values_after_fill": control.get(
                    "observation", {}
                ).get("observed_binding_values_after_fill"),
            },
            "target": {
                "run_status": target.get("run_status"),
                "o_spec": target.get("o_spec"),
                "unknown_reason": target.get("unknown_reason"),
                "interaction_error_type": target.get("interaction_error_type"),
                "candidate_bindings_match_after_fill": target.get(
                    "observation", {}
                ).get("candidate_bindings_match_after_fill"),
                "observed_binding_values_after_fill": target.get(
                    "observation", {}
                ).get("observed_binding_values_after_fill"),
            },
            "control_baseline_valid": control_valid,
            "target_observation_complete": target_observed,
            "historical_violation_reproduced": violation_reproduced,
            "ready_for_repeat_experiment": repeat_ready,
            "blockers": list(dict.fromkeys(blockers)),
        })
    return {
        "passed": all(row["ready_for_repeat_experiment"] for row in rows),
        "cases": rows,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# B002/B018 source-backed specification smoke",
        "",
        "- Paid model calls: none",
        f"- Offline prompt isolation: {'PASS' if report['prompt_isolation']['passed'] else 'FAIL'}",
        f"- Ready for repeat experiment: {'YES' if report['ready_for_repeat_experiment'] else 'NO'}",
        "",
        "| Case | CONTROL | TARGET | Hidden violation reproduced | Repeat-ready |",
        "|---|---|---|---:|---:|",
    ]
    for row in report.get("android_smoke", {}).get("cases", []):
        lines.append(
            f"| {row['case_id']} | {row['control']['run_status']}/{row['control']['o_spec']} "
            f"| {row['target']['run_status']}/{row['target']['o_spec']} "
            f"| {'yes' if row['historical_violation_reproduced'] else 'no'} "
            f"| {'yes' if row['ready_for_repeat_experiment'] else 'no'} |"
        )
        for blocker in row["blockers"]:
            lines.append(f"\n- **{row['case_id']} blocker:** {blocker}")
    return "\n".join(lines) + "\n"


def run(
    config_path: Path,
    output_dir: Path,
    *,
    android: bool,
    confirmation: str | None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError("output directory already exists")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    output_dir.mkdir(parents=True)
    prompt_isolation = _prompt_isolation(config_path, config, output_dir)
    android_smoke: dict[str, Any] = {"passed": False, "cases": [], "not_run": True}
    if android:
        if confirmation != "RUN-SOURCE-BACKED-SMOKE-V1":
            raise ValueError("Android smoke requires --confirm RUN-SOURCE-BACKED-SMOKE-V1")
        android_smoke = _android_smoke(config, output_dir)
    report = {
        "schema_version": "reviewer2-source-backed-specification-smoke-report-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_path": str(config_path),
        "config_sha256": _sha256_file(config_path),
        "no_model_call": True,
        "no_network_source_fetch": True,
        "prompt_isolation": prompt_isolation,
        "android_smoke": android_smoke,
        "ready_for_repeat_experiment": (
            prompt_isolation["passed"]
            and android
            and android_smoke["passed"]
        ),
    }
    _write(output_dir / "report.json", report)
    _write(output_dir / "REPORT.md", _markdown(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/supplementary/spec_source_backed_smoke_v1.json"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--android", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    report = run(
        args.config.resolve(),
        args.output_dir.resolve(),
        android=args.android,
        confirmation=args.confirm,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
