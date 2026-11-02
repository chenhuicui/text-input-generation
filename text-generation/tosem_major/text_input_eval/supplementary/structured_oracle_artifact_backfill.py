"""Replace legacy package-file hashes with frozen installed artifact-set hashes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_live_calibration import atomic_json, read_json
from .structured_oracle_promotion import evidence_reference
from .structured_snapshot_backup import sha256_file


def backfill(manifest_path: Path, cases_root: Path) -> dict[str, object]:
    manifest = read_json(manifest_path)
    changed: list[str] = []
    verified: list[str] = []
    for oracle in manifest.get("cases", []):
        if oracle.get("task") != "valid_pptr":
            continue
        case_id = oracle["case_id"]
        inspection = read_json(cases_root / case_id / "inspection.json")
        if inspection.get("status") != "inspect_ready":
            raise ValueError(f"{case_id} promoted Oracle no longer has a ready inspection")
        artifact = inspection.get("artifact", {})
        digest = artifact.get("artifact_set_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or artifact.get("identity_complete") is not True:
            raise ValueError(f"{case_id} installed artifact-set identity is incomplete")
        if str(oracle["app_artifact"].get("version_code")) != str(artifact.get("version_code")):
            raise ValueError(f"{case_id} installed version differs from the promoted Oracle")
        case_root = cases_root / case_id
        promoted_controls = []
        for control_path in case_root.glob("*/control_result.json"):
            control = read_json(control_path)
            if (
                control.get("status") == "controls_passed_local_only"
                and control.get("formal_oracle_promoted") is True
                and control.get("formal_oracle_version") == oracle.get("version")
            ):
                promoted_controls.append((control_path, control))
        if len(promoted_controls) != 1:
            raise ValueError(f"{case_id} has {len(promoted_controls)} promoted control artifacts")
        control_path, control = promoted_controls[0]
        plans = [
            path for path in case_root.glob("control_plan*.json")
            if sha256_file(path) == control.get("plan_sha256")
        ]
        if len(plans) != 1:
            raise ValueError(f"{case_id} promoted plan hash resolves to {len(plans)} files")
        inspection_path = case_root / "inspection.json"
        if sha256_file(inspection_path) != control.get("inspection_sha256"):
            # The live-contract backfill replaces historical package hashes
            # with the installed artifact-set identity and can therefore
            # change the inspection file after promotion.  Admit that audited
            # migration only when every runtime contract used by the Oracle
            # is unchanged.
            frozen_plan = read_json(plans[0])
            if any((
                inspection.get("binding") != oracle.get("binding"),
                frozen_plan.get("verify_action", inspection.get("verify_action"))
                != oracle.get("execute_turn", {}).get("verify_action"),
                frozen_plan.get("goal_assertion", inspection.get("goal_assertion"))
                != oracle.get("goal_assertion"),
            )):
                raise ValueError(f"{case_id} promoted inspection runtime contract drifted")
        smoke_label = control.get("formal_adapter_smoke", {}).get("path")
        smoke_path = (
            Path(smoke_label) if isinstance(smoke_label, str) and smoke_label
            else case_root / "formal_adapter_smoke" / "result.json"
        )
        if not smoke_path.is_absolute():
            smoke_path = (manifest_path.parent / smoke_path).resolve()
        if not smoke_path.is_file():
            raise FileNotFoundError(f"{case_id} formal adapter smoke is unavailable")
        expected_smoke_hash = control.get("formal_adapter_smoke", {}).get("sha256")
        if expected_smoke_hash is not None and expected_smoke_hash != sha256_file(smoke_path):
            raise ValueError(f"{case_id} formal adapter smoke hash drifted")
        smoke = read_json(smoke_path)
        if smoke.get("case_id") != case_id or smoke.get("oracle_version") != oracle.get("version"):
            raise ValueError(f"{case_id} formal adapter smoke identity differs")
        old = json.dumps(oracle, ensure_ascii=False, sort_keys=True)
        oracle["app_artifact"]["sha256"] = digest
        oracle["calibration"].update({
            "positive_control": evidence_reference(control_path, "positive_control"),
            "negative_control": evidence_reference(control_path, "negative_control"),
            "artifact_identity_policy": "installed_apk_set_sha256",
            "inspection_sha256": sha256_file(inspection_path),
            "plan_sha256": sha256_file(plans[0]),
            "formal_adapter_smoke_sha256": sha256_file(smoke_path),
        })
        if json.dumps(oracle, ensure_ascii=False, sort_keys=True) != old:
            changed.append(case_id)
        else:
            verified.append(case_id)
    atomic_json(manifest_path, manifest)
    return {"changed": changed, "already_verified": verified, "total": len(changed) + len(verified)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill installed artifact-set hashes")
    parser.add_argument("--oracle-manifest", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    args = parser.parse_args()
    result = backfill(args.oracle_manifest.resolve(), args.cases_root.resolve())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
