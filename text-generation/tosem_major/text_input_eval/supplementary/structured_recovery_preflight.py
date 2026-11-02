"""One-click, offline-only recovery audit and preflight for the E2 protocols.

This command verifies the external APK/AVD material, distinguishes exact
historical artifacts from newly reconstructed artifacts, rebuilds the formal
registry/freeze, and runs the A-F offline preflight.  It never calls a model
API, starts an experiment runner, or changes either formal-run switch.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .abcdef_preflight import build_preflight as build_abcdef_preflight
from .structured_live_calibration import atomic_json, read_json
from .structured_preflight import REPO_ROOT, sha256_file, write_outputs


DEFAULT_CONFIG = REPO_ROOT / "configs" / "supplementary" / "e2_recovery_v1.json"


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _gate(gates: list[dict[str, Any]], gate_id: str, passed: bool, detail: Any) -> None:
    gates.append({"id": gate_id, "passed": bool(passed), "detail": detail})


def _file_gate(
    gates: list[dict[str, Any]], gate_id: str, path: Path, expected_sha256: str,
    *, expected_bytes: int | None = None,
) -> dict[str, Any]:
    exists = path.is_file()
    actual_hash = sha256_file(path) if exists else None
    actual_bytes = path.stat().st_size if exists else None
    passed = exists and actual_hash == expected_sha256
    if expected_bytes is not None:
        passed = passed and actual_bytes == expected_bytes
    detail = {
        "path": str(path), "exists": exists,
        "expected_sha256": expected_sha256, "actual_sha256": actual_hash,
        "expected_bytes": expected_bytes, "actual_bytes": actual_bytes,
    }
    _gate(gates, gate_id, passed, detail)
    return detail


def verify_apk_bundle(config: dict[str, Any], gates: list[dict[str, Any]]) -> dict[str, Any]:
    bundle = config["external"]["apk_bundle"]
    root = Path(bundle["path"])
    sums = root / bundle["sha256sums"]
    _file_gate(
        gates, "apk_bundle_manifest", sums, bundle["sha256sums_sha256"],
    )
    rows: list[tuple[str, str]] = []
    malformed: list[str] = []
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2 or len(parts[0]) != 64:
                malformed.append(line)
            else:
                rows.append((parts[0], parts[1].lstrip("*")))
    missing: list[str] = []
    mismatches: list[dict[str, str]] = []
    for expected, relative in rows:
        path = root / relative
        if not path.is_file():
            missing.append(relative)
            continue
        actual = sha256_file(path)
        if actual != expected:
            mismatches.append({"path": relative, "expected": expected, "actual": actual})
    expected_count = int(bundle["apk_count"])
    detail = {
        "path": str(root), "manifest_entries": len(rows),
        "expected_apk_count": expected_count,
        "missing": missing, "mismatches": mismatches, "malformed": malformed,
    }
    _gate(
        gates, "apk_bundle_files",
        root.is_dir() and len(rows) == expected_count and not missing and not mismatches and not malformed,
        detail,
    )
    return detail


def verify_avd_backup(config: dict[str, Any], gates: list[dict[str, Any]]) -> dict[str, Any]:
    spec = config["external"]["avd_backup_manifest"]
    path = Path(spec["path"])
    file_detail = _file_gate(gates, "avd_backup_manifest", path, spec["sha256"])
    document = read_json(path) if path.is_file() else {}
    cases = document.get("cases", []) if isinstance(document, dict) else []
    missing_snapshots = [
        row.get("case_id") for row in cases
        if not Path(str(row.get("backup_snapshot_path", ""))).is_dir()
    ]
    missing_source_manifests: list[str] = []
    drifted_source_manifests: list[str] = []
    for row in cases:
        manifest = Path(str(row.get("source_manifest_path", "")))
        if not manifest.is_file():
            missing_source_manifests.append(str(row.get("case_id")))
        elif sha256_file(manifest) != row.get("source_manifest_sha256"):
            drifted_source_manifests.append(str(row.get("case_id")))
    passed = all((
        file_detail["actual_sha256"] == spec["sha256"],
        document.get("status") == "complete",
        document.get("case_count") == spec["case_count"],
        document.get("unique_snapshot_count") == spec["unique_snapshot_count"],
        len(cases) == spec["case_count"],
        not missing_snapshots,
        not missing_source_manifests,
        not drifted_source_manifests,
        Path(config["external"]["avd_root"]).is_dir(),
    ))
    detail = {
        "path": str(path), "status": document.get("status"),
        "case_count": len(cases), "unique_snapshot_count": document.get("unique_snapshot_count"),
        "missing_backup_snapshots": missing_snapshots,
        "missing_source_manifests": missing_source_manifests,
        "drifted_source_manifests": drifted_source_manifests,
    }
    _gate(gates, "avd_backup_contract", passed, detail)
    return detail


def verify_oracle_partition(
    config: dict[str, Any], config_path: Path, gates: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = config["expected"]
    reconstructed = config["reconstructed_artifacts"]
    oracle_path = resolve(config_path.parent, reconstructed["formal_oracles"])
    exclusion_path = resolve(config_path.parent, reconstructed["formal_exclusions"])
    oracle_document = read_json(oracle_path) if oracle_path.is_file() else {}
    exclusion_document = read_json(exclusion_path) if exclusion_path.is_file() else {}
    oracles = oracle_document.get("cases", []) if isinstance(oracle_document, dict) else []
    exclusions = exclusion_document.get("cases", []) if isinstance(exclusion_document, dict) else []
    oracle_ids = {row.get("case_id") for row in oracles}
    exclusion_ids = {row.get("case_id") for row in exclusions}
    page = [row for row in oracles if row.get("task") == "valid_pptr"]
    bugs = [row for row in oracles if row.get("task") == "invalid_bug"]
    bug_predicates_valid = all(
        row.get("oracle_kind") == "string_predicate"
        and row.get("predicate", {}).get("true_means") == "bug_revealed"
        for row in bugs
    )
    expected_exclusions = set(expected["not_applicable_cases"])
    passed = all((
        len(oracles) == expected["applicable_cases"],
        len(page) == expected["valid_pptr_oracles"],
        len(bugs) == expected["invalid_bug_oracles"],
        exclusion_ids == expected_exclusions,
        not (oracle_ids & exclusion_ids),
        len(oracle_ids | exclusion_ids) == expected["registry_cases"],
        bug_predicates_valid,
    ))
    detail = {
        "oracle_manifest": str(oracle_path), "formal_oracles": len(oracles),
        "valid_pptr": len(page), "invalid_bug": len(bugs),
        "formal_exclusions": sorted(exclusion_ids),
        "overlap": sorted(oracle_ids & exclusion_ids),
        "covered_cases": len(oracle_ids | exclusion_ids),
        "bug_string_predicates_valid": bug_predicates_valid,
    }
    _gate(gates, "oracle_exclusion_partition", passed, detail)
    return detail


def render_protocol(config: dict[str, Any], partition: dict[str, Any]) -> str:
    unavailable = config["historical_artifacts"]["unavailable_originals"]
    return f"""# E2 恢复协议（2026-09-29）

## 身份与边界

- 历史主协议保持为 `{config['historical_protocol']}`（A-D），主表示固定为 `extracted`。
- 新扩展协议为 `{config['extension_protocol']}`（A-F），不得与历史 A-D 结果混写。
- 本恢复产物是重新校准后的新冻结，不冒充丢失的历史原件。
- 本命令只做本地、离线材料校验和预检；不调用付费模型，不启动正式实验。

## Oracle 与结果语义

- 152 个冻结样例中，{partition['formal_oracles']} 个有正式 Oracle，3 个明确标记为 not applicable。
- Page 样例使用 App 行为 Oracle；Bug 样例仍以冻结的字符串谓词判定，App 观察只作为各臂等量反馈。
- `unknown` 与 `infrastructure_error` 必须保持独立状态，禁止折算为语义 0 分或普通失败。

## 独立尝试与恢复

- 每个独立尝试从冻结 AVD 快照或显式 case-specific reset 开始。
- 同一尝试内的下一轮只允许在当前 App 状态上重新定位和清空输入，不得加载快照或重置远端状态。
- 正负对照、reset、recovery 和 formal-adapter smoke 都必须有可哈希证据链。

## 恢复来源声明

- 丢失的历史 registry 原哈希：`{unavailable['case_registry_sha256']}`（当前不可得）。
- 丢失的历史 formal Oracle 原哈希：`{unavailable['formal_oracles_sha256']}`（当前不可得）。
- 原始 formal exclusions 已按原哈希恢复；registry 与 Oracle 使用当前材料重新构建和重新校准。

## 正式运行闸门

- `E2-structure-v1` 与 `E2-abcdef-v1` 的 `formal_run_enabled` 必须保持 `false`。
- F 臂分析器必须是精确的 `gpt-5.6-sol`、medium、严格 JSON schema；只有真实 API 资格验证和用户明确授权后，才能另行开启正式运行。
"""


def build_material_report(config_path: Path) -> tuple[dict[str, Any], str]:
    config_path = config_path.resolve()
    config = read_json(config_path)
    gates: list[dict[str, Any]] = []
    apk_zip = config["external"]["apk_zip"]
    _file_gate(
        gates, "apk_zip", Path(apk_zip["path"]), apk_zip["sha256"],
        expected_bytes=apk_zip["bytes"],
    )
    apk_bundle = verify_apk_bundle(config, gates)
    avd_backup = verify_avd_backup(config, gates)
    exact = config["historical_artifacts"]["exact_recovered"]["formal_exclusions"]
    _file_gate(
        gates, "exact_historical_formal_exclusions",
        resolve(config_path.parent, exact["path"]), exact["sha256"],
    )
    recovery_seed = resolve(
        config_path.parent, config["reconstructed_artifacts"]["recovery_seed"]
    )
    seed = read_json(recovery_seed) if recovery_seed.is_file() else {}
    _gate(
        gates, "recovery_seed_nonformal",
        recovery_seed.is_file()
        and seed.get("recovery_seed_only") is True
        and seed.get("formal_runner_input") is False
        and seed.get("counts", {}).get("cases") == config["expected"]["registry_cases"],
        {"path": str(recovery_seed), "sha256": sha256_file(recovery_seed) if recovery_seed.is_file() else None},
    )
    tracked = [resolve(config_path.parent, value) for value in config["tracked_recovery_code"]]
    missing_code = [str(path) for path in tracked if not path.is_file()]
    _gate(gates, "tracked_recovery_code", not missing_code, {"missing": missing_code})
    structure_config = resolve(config_path.parent, config["structure_config"])
    abcdef_config = resolve(config_path.parent, config["abcdef_config"])
    structure = read_json(structure_config)
    abcdef = read_json(abcdef_config)
    _gate(
        gates, "formal_switches_closed",
        structure.get("formal_run_enabled") is False
        and abcdef.get("formal_run_enabled") is False,
        {
            "structure_formal_run_enabled": structure.get("formal_run_enabled"),
            "abcdef_formal_run_enabled": abcdef.get("formal_run_enabled"),
            "abcdef_analyzer_api_verified": abcdef.get("analyzer", {}).get("api_verified"),
        },
    )
    partition = verify_oracle_partition(config, config_path, gates)
    protocol = render_protocol(config, partition)
    report = {
        "schema_version": "e2-recovery-v1-material-index-1",
        "recovery_id": config["recovery_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(gate["passed"] for gate in gates) else "block",
        "blocking_gates": [gate["id"] for gate in gates if not gate["passed"]],
        "gates": gates,
        "external_material": {"apk_bundle": apk_bundle, "avd_backup": avd_backup},
        "historical_identity": {
            "exact_recovered": config["historical_artifacts"]["exact_recovered"],
            "unavailable_originals": config["historical_artifacts"]["unavailable_originals"],
            "reconstruction_is_new_freeze": True,
        },
        "oracle_partition": partition,
        "safety": {
            "paid_model_called": False,
            "formal_experiment_started": False,
            "unknown_is_semantic_zero": False,
            "infrastructure_error_is_semantic_failure": False,
        },
    }
    return report, protocol


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline-only E2 recovery material audit and preflight")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = read_json(config_path)
    output_dir = resolve(config_path.parent, config["output_dir"])
    abcdef_output_dir = resolve(config_path.parent, config["abcdef_output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    report, protocol = build_material_report(config_path)
    protocol_path = output_dir / "PROTOCOL.zh-CN.md"
    protocol_path.write_text(protocol, encoding="utf-8")
    material_path = output_dir / "RECOVERY_MATERIAL_INDEX.json"
    atomic_json(material_path, report)
    if report["status"] != "pass":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    structure_config = resolve(config_path.parent, config["structure_config"])
    structure_freeze = write_outputs(structure_config, output_dir)
    abcdef_config = resolve(config_path.parent, config["abcdef_config"])
    abcdef_report = build_abcdef_preflight(abcdef_config, run_tests=True)
    abcdef_output_dir.mkdir(parents=True, exist_ok=True)
    abcdef_report_path = abcdef_output_dir / "preflight_report.json"
    atomic_json(abcdef_report_path, abcdef_report)
    expected_blocks = set(config["expected"]["abcdef_blocking_gates"])
    actual_blocks = set(abcdef_report["blocking_gates"])
    if actual_blocks != expected_blocks:
        raise RuntimeError(
            f"unexpected A-F preflight blockers: expected={sorted(expected_blocks)}, "
            f"actual={sorted(actual_blocks)}"
        )

    freeze_inputs = [
        config_path, material_path, protocol_path,
        output_dir / "case_registry.json", output_dir / "preflight_report.json",
        output_dir / "freeze_manifest.json", abcdef_report_path,
        resolve(config_path.parent, config["reconstructed_artifacts"]["recovery_seed"]),
        resolve(config_path.parent, config["reconstructed_artifacts"]["formal_exclusions"]),
        resolve(config_path.parent, config["reconstructed_artifacts"]["formal_oracles"]),
    ] + [resolve(config_path.parent, value) for value in config["tracked_recovery_code"]]
    recovery_freeze = {
        "schema_version": "e2-recovery-v1-freeze-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "recovery_id": config["recovery_id"],
        "historical_originals_recreated": False,
        "formal_run_enabled": False,
        "files": {str(path): sha256_file(path) for path in freeze_inputs},
    }
    atomic_json(output_dir / "RECOVERY_FREEZE_MANIFEST.json", recovery_freeze)
    print(json.dumps({
        "status": "pass", "material_index": str(material_path),
        "structure_formal_run_allowed": structure_freeze["formal_run_allowed"],
        "structure_blocking_gates": read_json(output_dir / "preflight_report.json")["blocking_gates"],
        "abcdef_formal_run_allowed": abcdef_report["formal_run_allowed"],
        "abcdef_blocking_gates": abcdef_report["blocking_gates"],
        "recovery_freeze": str(output_dir / "RECOVERY_FREEZE_MANIFEST.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
