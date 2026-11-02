"""CLI and fail-closed runner for the Reviewer-2 specification pilot.

No command performs a paid call or touches Android unless the caller selects
``qualify-model`` or ``run`` and supplies the exact confirmation token.  The
default ``validate`` and ``preflight`` commands are local and read-only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .abcdef_gateway import ABCDEFSubjectGateway
from .spec_feasibility_contract import (
    ARMS,
    PROTOCOL_ID,
    candidate_partition,
    canonical_json,
    parse_candidate,
    render_prompt,
    request_cost_usd,
    required_resource_ids,
    sha256_json,
    usage_tokens,
    validate_config,
)
from .structured_runner import load_base_prompt


REPO_ROOT = Path(__file__).resolve().parents[3]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(config_path: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()


def _frozen_baselines(config_path: Path, config: dict[str, Any]) -> dict[str, str]:
    """Load the original extracted/full prompts through the A--F loader."""
    baseline = config["baseline_prompt"]
    registry_path = _resolve(config_path, baseline["case_registry"])
    if registry_path is None or not registry_path.is_file():
        raise RuntimeError("frozen case registry is unavailable")
    if _sha256_file(registry_path) != baseline["case_registry_sha256"]:
        raise RuntimeError("frozen case registry SHA-256 differs")
    registry = _read_json(registry_path)
    rows = registry.get("cases") if isinstance(registry, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("frozen case registry has no case list")
    by_id = {
        row.get("case_id"): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }
    prompts: dict[str, str] = {}
    for case in config["cases"]:
        case_id = case["case_id"]
        row = by_id.get(case_id)
        if not isinstance(row, dict) or row.get("task") != "invalid_bug":
            raise RuntimeError(f"{case_id} is absent from the frozen invalid-bug registry")
        if row.get("required_resource_ids") != required_resource_ids(case):
            raise RuntimeError(f"{case_id} resource ID differs from the frozen registry")
        prompts[case_id] = load_base_prompt(REPO_ROOT, row)
    return prompts


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


def _qualification_contract(config: dict[str, Any]) -> dict[str, Any]:
    model = config.get("model", {})
    runtime = config.get("runtime", {})
    return {
        "protocol_id": config.get("protocol_id"),
        "model_id": model.get("id"),
        "revision": model.get("revision"),
        "capacity_verified": model.get("capacity_verified"),
        "max_prompt_bytes": model.get("max_prompt_bytes"),
        "pricing_usd_per_million_tokens": model.get("pricing_usd_per_million_tokens"),
        "max_request_cost_usd": model.get("max_request_cost_usd"),
        "api_url": runtime.get("api_url"),
        "fresh_provider_context_value": runtime.get("fresh_provider_context_value"),
        "provider_cursor_reuse": runtime.get("provider_cursor_reuse"),
        "max_transport_attempts": runtime.get("max_transport_attempts"),
    }


def _apksigner_path() -> str | None:
    direct = shutil.which("apksigner")
    if direct:
        return direct
    sdk = Path.home() / "Library" / "Android" / "sdk" / "build-tools"
    candidates = sorted(sdk.glob("*/apksigner"), reverse=True) if sdk.is_dir() else []
    return str(candidates[0]) if candidates else None


def _apk_signer_digest(path: Path) -> tuple[str | None, str | None]:
    executable = _apksigner_path()
    if executable is None:
        return None, "apksigner is unavailable"
    completed = subprocess.run(
        [executable, "verify", "--print-certs", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        return None, "apksigner verification failed"
    match = re.search(
        r"Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F]{64})",
        completed.stdout,
    )
    if match is None:
        return None, "apksigner did not report one certificate SHA-256 digest"
    return match.group(1).lower(), None


def _frozen_file_gate(
    rows: list[dict[str, Any]],
    *,
    gate: str,
    path: Path | None,
    expected_sha256: str | None,
) -> None:
    status = "pass"
    detail = None
    actual = None
    if path is None or not path.is_file():
        status = "block"
        detail = "file is missing"
    else:
        actual = _sha256_file(path)
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            status = "block"
            detail = "expected SHA-256 is not frozen"
        elif actual != expected_sha256:
            status = "block"
            detail = "SHA-256 drift"
    rows.append({
        "gate": gate,
        "status": status,
        "path": str(path) if path else None,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual,
        "detail": detail,
    })


def local_preflight(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Check immutable local prerequisites without provider, ADB, App, or Docker calls."""
    validate_config(config)
    rows: list[dict[str, Any]] = []
    rows.append({
        "gate": "protocol_frozen_and_enabled",
        "status": "pass" if (
            config.get("protocol_status") == "frozen"
            and config.get("live_run_enabled") is True
        ) else "block",
        "detail": (
            None if config.get("protocol_status") == "frozen" and config.get("live_run_enabled") is True
            else "draft configuration cannot launch a live run"
        ),
    })
    baseline = config["baseline_prompt"]
    registry_path = _resolve(config_path, baseline.get("case_registry"))
    _frozen_file_gate(
        rows,
        gate="extracted_prompt_case_registry",
        path=registry_path,
        expected_sha256=baseline.get("case_registry_sha256"),
    )
    try:
        frozen_baselines = _frozen_baselines(config_path, config)
        baseline_error = None
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        frozen_baselines = {}
        baseline_error = str(exc)
    for case in config["cases"]:
        case_id = case["case_id"]
        prompt = frozen_baselines.get(case_id)
        rows.append({
            "gate": f"{case_id}:original_extracted_prompt",
            "status": "pass" if isinstance(prompt, str) and bool(prompt) else "block",
            "representation": "extracted/full",
            "prompt_sha256": (
                hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                if isinstance(prompt, str) else None
            ),
            "prompt_bytes": len(prompt.encode("utf-8")) if isinstance(prompt, str) else None,
            "detail": baseline_error,
        })
    model = config.get("model", {})
    qualification_path = _resolve(config_path, model.get("qualification_receipt"))
    _frozen_file_gate(
        rows,
        gate="model_qualification_receipt",
        path=qualification_path,
        expected_sha256=model.get("qualification_receipt_sha256"),
    )
    if qualification_path is not None and qualification_path.is_file():
        try:
            qualification = _read_json(qualification_path)
            valid_qualification = (
                qualification.get("schema_version") == "spec-feasibility-model-qualification-1"
                and qualification.get("model_id") == model.get("id")
                and qualification.get("expected_revision") == model.get("revision")
                and qualification.get("fresh_provider_context") is True
                and qualification.get("qualification_contract_sha256")
                == sha256_json(_qualification_contract(config))
                and qualification.get("pricing_usd_per_million_tokens")
                == model.get("pricing_usd_per_million_tokens")
                and isinstance(qualification.get("request_cost_usd"), (int, float))
                and qualification["request_cost_usd"] >= 0
                and qualification["request_cost_usd"] <= model.get("max_request_cost_usd")
                and isinstance(qualification.get("transport_attempts"), list)
                and len(qualification["transport_attempts"]) == 1
            )
        except (json.JSONDecodeError, OSError, TypeError, AttributeError):
            valid_qualification = False
        rows.append({
            "gate": "model_qualification_contract_matches",
            "status": "pass" if valid_qualification else "block",
            "detail": None if valid_qualification else "qualification receipt differs from the frozen model/gateway/cost contract",
        })
    pricing = model.get("pricing_usd_per_million_tokens")
    rows.append({
        "gate": "model_revision_and_pricing_frozen",
        "status": "pass" if (
            isinstance(model.get("revision"), str)
            and bool(model["revision"].strip())
            and isinstance(pricing, dict)
            and all(isinstance(pricing.get(key), (int, float)) and pricing[key] >= 0 for key in ("input", "output"))
            and isinstance(model.get("max_request_cost_usd"), (int, float))
            and model["max_request_cost_usd"] > 0
        ) else "block",
        "detail": "model revision, current prices, and per-request ceiling must be frozen",
    })

    for case in config["cases"]:
        case_id = case["case_id"]
        execution = case["execution"]
        _frozen_file_gate(
            rows,
            gate=f"{case_id}:apk",
            path=_resolve(config_path, execution.get("apk_path")),
            expected_sha256=execution.get("apk_sha256"),
        )
        apk_path = _resolve(config_path, execution.get("apk_path"))
        signer = execution.get("apk_signer_sha256")
        actual_signer = None
        signer_error = None
        if apk_path is not None and apk_path.is_file():
            actual_signer, signer_error = _apk_signer_digest(apk_path)
        signer_pass = (
            isinstance(signer, str)
            and len(signer) == 64
            and actual_signer == signer.lower()
        )
        rows.append({
            "gate": f"{case_id}:apk_signer_verified",
            "status": "pass" if signer_pass else "block",
            "expected_signer_sha256": signer,
            "actual_signer_sha256": actual_signer,
            "detail": None if signer_pass else (
                signer_error or "APK signer digest is missing or differs"
            ),
        })
        _frozen_file_gate(
            rows,
            gate=f"{case_id}:manifest",
            path=_resolve(config_path, execution.get("manifest_path")),
            expected_sha256=execution.get("manifest_sha256"),
        )
        snapshot = _resolve(config_path, execution.get("snapshot_path"))
        rows.append({
            "gate": f"{case_id}:snapshot",
            "status": "pass" if snapshot is not None and (snapshot / "snapshot.pb").is_file() else "block",
            "path": str(snapshot) if snapshot else None,
            "detail": None if snapshot is not None and (snapshot / "snapshot.pb").is_file() else "snapshot.pb is missing",
        })
        command = execution.get("reset_command")
        rows.append({
            "gate": f"{case_id}:reset_command_frozen",
            "status": "pass" if (
                isinstance(command, list)
                and bool(command)
                and all(isinstance(item, str) and item for item in command)
            ) else "block",
            "detail": "reset argv must be frozen" if not command else None,
        })
        for index, source in enumerate(case["requirement"]["sources"], 1):
            _frozen_file_gate(
                rows,
                gate=f"{case_id}:source_archive:{index}",
                path=_resolve(config_path, source.get("archive_path")),
                expected_sha256=source.get("archive_sha256"),
            )
        for index, asset in enumerate(execution.get("required_local_assets", []), 1):
            _frozen_file_gate(
                rows,
                gate=f"{case_id}:required_asset:{index}",
                path=_resolve(config_path, asset.get("path")),
                expected_sha256=asset.get("sha256"),
            )
        runtime_qualification_path = _resolve(
            config_path, execution.get("runtime_qualification_receipt")
        )
        _frozen_file_gate(
            rows,
            gate=f"{case_id}:runtime_qualification_receipt",
            path=runtime_qualification_path,
            expected_sha256=execution.get("runtime_qualification_receipt_sha256"),
        )
        if runtime_qualification_path is not None and runtime_qualification_path.is_file():
            try:
                runtime_qualification = _read_json(runtime_qualification_path)
                installed = runtime_qualification.get("installed", {})
                reset_receipt = runtime_qualification.get("reset_receipt", {})
                valid_runtime_qualification = (
                    runtime_qualification.get("schema_version")
                    == "spec-feasibility-runtime-qualification-1"
                    and runtime_qualification.get("protocol_id") == config.get("protocol_id")
                    and runtime_qualification.get("case_id") == case_id
                    and runtime_qualification.get("no_model_call") is True
                    and runtime_qualification.get("device_serial") == execution.get("device_serial")
                    and (
                        runtime_qualification.get("target_resource_ids")
                        == required_resource_ids(case)
                        or (
                            len(required_resource_ids(case)) == 1
                            and runtime_qualification.get("target_resource_id")
                            == required_resource_ids(case)[0]
                        )
                    )
                    and (
                        runtime_qualification.get("target_fields_present") is True
                        or runtime_qualification.get("target_field_present") is True
                    )
                    and runtime_qualification.get("reset_command") == execution.get("reset_command")
                    and reset_receipt.get("reset_verified") is True
                    and reset_receipt.get("reset_id") == execution.get("reset_id")
                    and installed.get("package") == execution.get("package")
                    and installed.get("version_code") == str(execution.get("version_code"))
                    and installed.get("remote_apk_sha256") == execution.get("apk_sha256")
                    and runtime_qualification.get("local_apk_sha256") == execution.get("apk_sha256")
                    and runtime_qualification.get("local_apk_signer_sha256")
                    == execution.get("apk_signer_sha256")
                )
            except (json.JSONDecodeError, OSError, TypeError, AttributeError):
                valid_runtime_qualification = False
            rows.append({
                "gate": f"{case_id}:runtime_qualification_contract_matches",
                "status": "pass" if valid_runtime_qualification else "block",
                "detail": (
                    None if valid_runtime_qualification
                    else "runtime qualification receipt differs from the frozen case contract"
                ),
            })
    blockers = [row for row in rows if row["status"] != "pass"]
    return {
        "schema_version": "spec-feasibility-local-preflight-1",
        "protocol_id": PROTOCOL_ID,
        "checked_at": _utc_now(),
        "config_path": str(config_path),
        "config_sha256": _sha256_file(config_path),
        "read_only_scope": "local files only; no provider, ADB, App, or Docker calls",
        "ready_for_live_run": not blockers,
        "gates": rows,
        "blocker_count": len(blockers),
    }


def _gateway(config: dict[str, Any]) -> ABCDEFSubjectGateway:
    runtime = config["runtime"]
    token = os.environ.get(runtime["api_token_env"])
    if not token:
        raise RuntimeError(f"missing provider token environment variable: {runtime['api_token_env']}")
    return ABCDEFSubjectGateway(
        runtime["api_url"],
        token,
        timeout_seconds=float(runtime["api_timeout_seconds"]),
        verify_ssl=bool(runtime.get("verify_ssl", False)),
        max_transport_attempts=1,
        transport_retry_backoff_seconds=0,
    )


def qualify_model(config_path: Path, config: dict[str, Any], output: Path, confirmation: str) -> dict[str, Any]:
    if confirmation != "QUALIFY-PILOT-V1":
        raise ValueError("qualification requires --confirm QUALIFY-PILOT-V1")
    validate_config(config)
    model = config["model"]
    if not isinstance(model.get("revision"), str) or not model["revision"].strip():
        raise ValueError("freeze the expected model revision before qualification")
    pricing = model.get("pricing_usd_per_million_tokens")
    if not isinstance(pricing, dict) or set(pricing) != {"input", "output"}:
        raise ValueError("freeze current input/output pricing before qualification")
    if not isinstance(model.get("max_request_cost_usd"), (int, float)) or model["max_request_cost_usd"] <= 0:
        raise ValueError("freeze a positive per-request cost ceiling before qualification")
    if output.exists():
        raise FileExistsError("qualification output already exists; refusing a duplicate paid call")
    reservation = output.with_suffix(output.suffix + ".reserved")
    _write_new(reservation, {
        "schema_version": "spec-feasibility-paid-call-reservation-1",
        "role": "model_qualification",
        "reserved_at": _utc_now(),
        "model": model["id"],
        "config_sha256": _sha256_file(config_path),
    })
    gateway = _gateway(config)
    prompt = (
        "Return exactly this JSON object and no markdown: "
        '{"qualification":"spec-feasibility-pilot-v1"}'
    )
    result = gateway.generate(model, prompt)
    if result.get("run_status") != "ok":
        raise RuntimeError(f"model qualification failed: {result.get('error_type')}")
    if result.get("response_model") not in {model["id"], model["revision"]}:
        raise RuntimeError("qualification response model differs from the frozen model")
    usage_tokens(result.get("usage"))
    try:
        parsed = json.loads(result["raw_response"])
    except json.JSONDecodeError as exc:
        raise RuntimeError("qualification response was not exact JSON") from exc
    if parsed != {"qualification": "spec-feasibility-pilot-v1"}:
        raise RuntimeError("qualification response content differs")
    qualification_cost = request_cost_usd(result["usage"], pricing)
    if qualification_cost > float(model["max_request_cost_usd"]):
        raise RuntimeError("qualification response exceeded the frozen per-request cost ceiling")
    receipt = {
        "schema_version": "spec-feasibility-model-qualification-1",
        "protocol_id": PROTOCOL_ID,
        "qualified_at": _utc_now(),
        "qualification_config_sha256": _sha256_file(config_path),
        "qualification_contract_sha256": sha256_json(_qualification_contract(config)),
        "model_id": model["id"],
        "expected_revision": model["revision"],
        "response_model": result.get("response_model"),
        "fresh_provider_context": True,
        "transport_attempts": result.get("transport_attempts"),
        "usage": result.get("usage"),
        "pricing_usd_per_million_tokens": pricing,
        "request_cost_usd": qualification_cost,
        "response_sha256": result.get("raw_response_sha256"),
    }
    _write_new(output, receipt)
    return receipt


def _call_environment(command: list[str], request: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            input=canonical_json(request),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "run_status": "infrastructure_error",
            "o_spec": "UNKNOWN",
            "error_type": type(exc).__name__,
            "unknown_reason": "environment adapter did not return a result",
            "public_observations": ["The execution adapter did not return a result."],
        }
    if completed.returncode != 0:
        return {
            "run_status": "infrastructure_error",
            "o_spec": "UNKNOWN",
            "error_type": "environment_adapter_nonzero_exit",
            "error_message": (completed.stderr or completed.stdout).strip()[-2000:],
            "unknown_reason": "environment adapter exited unsuccessfully",
            "public_observations": ["The execution adapter exited unsuccessfully."],
        }
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "run_status": "infrastructure_error",
            "o_spec": "UNKNOWN",
            "error_type": type(exc).__name__,
            "unknown_reason": "environment adapter returned invalid JSON",
            "public_observations": ["The execution adapter returned invalid output."],
        }
    return value if isinstance(value, dict) else {
        "run_status": "infrastructure_error",
        "o_spec": "UNKNOWN",
        "error_type": "environment_adapter_non_object",
        "unknown_reason": "environment adapter returned a non-object",
        "public_observations": ["The execution adapter returned invalid output."],
    }


def _schedule(config: dict[str, Any]) -> list[dict[str, Any]]:
    replicates = int(config["budget"].get("replicates_per_case_arm", 1))
    rows = [
        {"case_id": case["case_id"], "arm": arm, "replicate_id": replicate_id}
        for case in config["cases"]
        for arm in ARMS
        for replicate_id in range(1, replicates + 1)
    ]
    random.Random(int(config["schedule_seed"])).shuffle(rows)
    return rows


def _qualification_receipt(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    path = _resolve(config_path, config["model"]["qualification_receipt"])
    if path is None or not path.is_file():
        raise RuntimeError("frozen qualification receipt is unavailable")
    value = _read_json(path)
    if not isinstance(value, dict):
        raise RuntimeError("qualification receipt is not a JSON object")
    return value


def _materialize_frozen_inputs(
    output_dir: Path,
    config_path: Path,
    config: dict[str, Any],
) -> None:
    _write_new(output_dir / "frozen_protocol.json", config)
    _write_new(output_dir / "case_contracts.json", {
        "schema_version": "spec-feasibility-case-contracts-1",
        "protocol_id": PROTOCOL_ID,
        "cases": config["cases"],
    })
    baselines = _frozen_baselines(config_path, config)
    baseline_rows = []
    for case_id, prompt in sorted(baselines.items()):
        destination = output_dir / "baseline_prompts" / f"{case_id}.txt"
        _write_new(destination, prompt)
        baseline_rows.append({
            "case_id": case_id,
            "representation": "extracted/full",
            "path": str(destination.relative_to(output_dir)),
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "bytes": len(prompt.encode("utf-8")),
        })
    _write_new(output_dir / "baseline_prompt_manifest.json", {
        "schema_version": "spec-feasibility-baseline-prompt-manifest-1",
        "source_case_registry": config["baseline_prompt"]["case_registry"],
        "source_case_registry_sha256": config["baseline_prompt"]["case_registry_sha256"],
        "prompts": baseline_rows,
    })
    source_rows: list[dict[str, Any]] = []
    for case in config["cases"]:
        for index, source in enumerate(case["requirement"]["sources"], 1):
            archive = _resolve(config_path, source["archive_path"])
            assert archive is not None
            suffix = archive.suffix if archive.suffix else ".bin"
            destination = output_dir / "sources" / case["case_id"] / f"source_{index:02d}{suffix}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive, destination)
            copied_sha = _sha256_file(destination)
            if copied_sha != source["archive_sha256"]:
                raise RuntimeError("source archive changed while materializing the run")
            source_rows.append({
                "case_id": case["case_id"],
                "source_type": source["source_type"],
                "url": source["url"],
                "supports": source["supports"],
                "path": str(destination.relative_to(output_dir)),
                "sha256": copied_sha,
            })
    _write_new(output_dir / "source_manifest.json", {
        "schema_version": "spec-feasibility-source-manifest-1",
        "sources": source_rows,
    })
    qualification = _qualification_receipt(config_path, config)
    _write_new(output_dir / "model_qualification_receipt.json", qualification)


def _write_sha256s(output_dir: Path) -> None:
    rows = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            rows.append(f"{_sha256_file(path)}  {path.relative_to(output_dir)}")
    _write_new(output_dir / "SHA256SUMS", "\n".join(rows) + "\n")


def _prior_paid_state(output_dir: Path) -> tuple[int, float]:
    calls = 0
    cost = 0.0
    for path in output_dir.glob("trajectories/**/turn_*/attempt_record.json"):
        record = _read_json(path)
        if record.get("paid_model_request") is True:
            calls += 1
            cost += float(record.get("request_cost_usd", 0.0))
    return calls, cost


def run_pilot(
    config_path: Path,
    config: dict[str, Any],
    output_dir: Path,
    confirmation: str,
    recovery_from: Path | None = None,
    case_id: str | None = None,
    shard_index: int = 0,
    shard_count: int = 1,
    device_serial: str | None = None,
    avd_name: str | None = None,
    avd_path: Path | None = None,
) -> dict[str, Any]:
    if confirmation != "RUN-PILOT-V1":
        raise ValueError("live execution requires --confirm RUN-PILOT-V1")
    validate_config(config, require_live=True)
    preflight = local_preflight(config_path, config)
    if not preflight["ready_for_live_run"]:
        raise RuntimeError("local preflight has blockers; no paid or Android call was made")
    if output_dir.exists():
        raise FileExistsError("run directory already exists; refusing duplicate or ambiguous calls")
    if recovery_from is not None:
        recovery_from = recovery_from.resolve()
        if not recovery_from.is_dir() or not (recovery_from / "STOPPED.json").is_file():
            raise RuntimeError("recovery source must be a stopped run directory")
        if (recovery_from / "COMPLETED.json").exists():
            raise RuntimeError("refusing to recover model responses from a completed run")
    if shard_count < 1 or shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard index must be within the declared positive shard count")
    if case_id is not None and case_id not in {case["case_id"] for case in config["cases"]}:
        raise ValueError("shard case_id is absent from the frozen protocol")
    if any(value is not None for value in (device_serial, avd_name, avd_path)) and case_id != "B014":
        raise ValueError("device overrides are supported only for the replicated B014 runtime")
    if case_id == "B014" and any(value is None for value in (device_serial, avd_name, avd_path)):
        raise ValueError("a B014 shard requires serial, AVD name, and AVD path together")

    output_dir.mkdir(parents=True)
    _materialize_frozen_inputs(output_dir, config_path, config)
    _write_new(output_dir / "local_preflight.json", preflight)
    schedule = _schedule(config)
    if case_id is not None:
        schedule = [row for row in schedule if row["case_id"] == case_id]
    schedule = [row for index, row in enumerate(schedule) if index % shard_count == shard_index]
    if not schedule:
        raise RuntimeError("the requested shard has no trajectories")
    _write_new(output_dir / "execution_order.json", {
        "schedule_seed": config["schedule_seed"],
        "trajectories": schedule,
    })
    _write_new(output_dir / "run_manifest.json", {
        "schema_version": "spec-feasibility-run-manifest-1",
        "protocol_id": PROTOCOL_ID,
        "started_at": _utc_now(),
        "config_path": str(config_path),
        "config_sha256": _sha256_file(config_path),
        "fresh_provider_context_per_request": True,
        "automatic_paid_transport_retries": False,
        "descriptive_only": True,
        "trajectory_scope": {
            "case_id": case_id,
            "shard_index": shard_index,
            "shard_count": shard_count,
            "scheduled_trajectories": len(schedule),
        },
        "device_override": {
            "device_serial": device_serial,
            "avd_name": avd_name,
            "avd_path": str(avd_path) if avd_path is not None else None,
        },
        "recovery_from": str(recovery_from) if recovery_from is not None else None,
    })
    if recovery_from is not None:
        _write_new(output_dir / "recovery_manifest.json", {
            "schema_version": "spec-feasibility-recovery-manifest-1",
            "source_run": str(recovery_from),
            "source_stopped_sha256": _sha256_file(recovery_from / "STOPPED.json"),
            "policy": (
                "Reuse only a successful paid model response whose prompt is byte-identical; "
                "rerun Android execution without another provider call."
            ),
        })
    gateway = _gateway(config)
    model = config["model"]
    pricing = model["pricing_usd_per_million_tokens"]
    budget = config["budget"]
    qualification = _qualification_receipt(config_path, config)
    qualification_cost = float(qualification["request_cost_usd"])
    case_by_id = {case["case_id"]: case for case in config["cases"]}
    baselines = _frozen_baselines(config_path, config)
    stop_signal: dict[str, Any] | None = None

    for cell in schedule:
        case = json.loads(json.dumps(case_by_id[cell["case_id"]]))
        if case["case_id"] == "B014" and device_serial is not None:
            assert avd_name is not None and avd_path is not None
            case["execution"]["device_serial"] = device_serial
            case["execution"]["snapshot_path"] = str(
                avd_path / "snapshots" / "B014_baseline_v1"
            )
            case["execution"]["reset_command"] = [
                "python3", "-m",
                "text_input_eval.supplementary.spec_feasibility_snapshot_reset",
                "B014",
                "--serial", device_serial,
                "--avd", avd_name,
                "--avd-path", str(avd_path),
            ]
        arm = cell["arm"]
        replicate_id = int(cell["replicate_id"])
        prior_attempts: list[dict[str, Any]] = []
        for turn in range(1, int(budget["turns_per_trajectory"]) + 1):
            calls, cost = _prior_paid_state(output_dir)
            if calls >= int(budget["max_experimental_paid_requests"]):
                stop_signal = {"reason": "experimental_paid_request_cap", "calls": calls}
                break
            if (
                1 + calls >= int(budget["max_total_paid_requests"])
                or qualification_cost + cost + float(model["max_request_cost_usd"])
                > float(budget["max_total_cost_usd"])
            ):
                stop_signal = {
                    "reason": "total_call_or_cost_cap_precheck",
                    "qualification_cost_usd": qualification_cost,
                    "experimental_cost_usd": cost,
                    "qualification_requests": 1,
                    "experimental_requests": calls,
                }
                break
            replicate_dir = f"replicate_{replicate_id:03d}"
            turn_dir = (
                output_dir / "trajectories" / case["case_id"] / arm
                / replicate_dir / f"turn_{turn:02d}"
            )
            turn_dir.mkdir(parents=True, exist_ok=False)
            prompt, prompt_audit = render_prompt(
                case,
                arm,
                prior_attempts,
                baselines[case["case_id"]],
            )
            _write_new(turn_dir / "prompt.txt", prompt)
            _write_new(turn_dir / "prompt_audit.json", prompt_audit)
            recovery_turn = (
                recovery_from / "trajectories" / case["case_id"] / arm
                / replicate_dir / f"turn_{turn:02d}"
                if recovery_from is not None else None
            )
            reusable_response = bool(
                recovery_turn is not None
                and (recovery_turn / "prompt.txt").is_file()
                and (recovery_turn / "model_result.json").is_file()
                and (recovery_turn / "model_request_reserved.json").is_file()
                and _read_json(recovery_turn / "model_result.json").get("run_status") == "ok"
            )
            reservation = {
                "schema_version": "spec-feasibility-paid-call-reservation-1",
                "reserved_at": _utc_now(),
                "case_id": case["case_id"],
                "arm": arm,
                "replicate_id": replicate_id,
                "turn": turn,
                "model": model["id"],
                "fresh_provider_context_value": "",
                "prompt_sha256": prompt_audit["prompt_sha256"],
                "model_request_origin": (
                    "reused_stopped_run" if reusable_response else "new_provider_call"
                ),
                "recovery_source": str(recovery_turn) if reusable_response else None,
            }
            _write_new(turn_dir / "model_request_reserved.json", reservation)
            source_record: dict[str, Any] | None = None
            if reusable_response:
                assert recovery_turn is not None
                if (recovery_turn / "prompt.txt").read_text(encoding="utf-8") != prompt:
                    raise RuntimeError("recovery prompt differs; refusing to reuse the paid response")
                source_reservation = _read_json(recovery_turn / "model_request_reserved.json")
                model_result = _read_json(recovery_turn / "model_result.json")
                source_record_path = recovery_turn / "attempt_record.json"
                if source_record_path.is_file():
                    source_record = _read_json(source_record_path)
                raw_response = model_result.get("raw_response")
                raw_response_sha256 = (
                    hashlib.sha256(raw_response.encode("utf-8")).hexdigest()
                    if isinstance(raw_response, str) else None
                )
                if model_result.get("run_status") != "ok" or (
                    source_reservation.get("prompt_sha256") != prompt_audit["prompt_sha256"]
                    or raw_response_sha256 != model_result.get("raw_response_sha256")
                    or (
                        source_record is not None
                        and (
                            source_record.get("paid_model_request") is not True
                            or source_record.get("model_response_sha256")
                            != model_result.get("raw_response_sha256")
                        )
                    )
                ):
                    raise RuntimeError("recovery source has no auditable successful paid response")
            else:
                model_result = gateway.generate(model, prompt)
            _write_new(turn_dir / "model_result.json", model_result)
            if model_result.get("run_status") != "ok":
                stop_signal = {
                    "reason": "provider_failure_or_ambiguous_paid_request",
                    "case_id": case["case_id"],
                    "arm": arm,
                    "replicate_id": replicate_id,
                    "turn": turn,
                    "error_type": model_result.get("error_type"),
                }
                break
            if model_result.get("response_model") not in {model["id"], model["revision"]}:
                stop_signal = {
                    "reason": "model_revision_drift",
                    "observed_model": model_result.get("response_model"),
                }
                break
            try:
                usage_tokens(model_result.get("usage"))
                call_cost = request_cost_usd(model_result["usage"], pricing)
            except ValueError as exc:
                stop_signal = {"reason": "missing_usage_or_cost_telemetry", "detail": str(exc)}
                break
            record: dict[str, Any] = {
                "schema_version": "spec-feasibility-attempt-record-1",
                "protocol_id": PROTOCOL_ID,
                "case_id": case["case_id"],
                "arm": arm,
                "replicate_id": replicate_id,
                "turn": turn,
                "paid_model_request": True,
                "model_request_origin": (
                    "reused_stopped_run" if reusable_response else "new_provider_call"
                ),
                "execution_origin": None,
                "model_response_sha256": model_result.get("raw_response_sha256"),
                "usage": model_result["usage"],
                "request_cost_usd": call_cost,
                "model_elapsed_ms": model_result.get("elapsed_ms"),
                "usable_response": False,
                "candidate": None,
                "candidate_target_partition": "UNKNOWN",
                "o_spec": "UNKNOWN",
                "unknown_reason": None,
                "public_observations": [],
            }
            try:
                candidate = parse_candidate(model_result["raw_response"], case)
            except ValueError as exc:
                record.update(
                    unknown_reason="candidate did not satisfy the frozen output schema",
                    public_observations=["The candidate did not satisfy the frozen output schema."],
                    parse_error=str(exc),
                )
                _write_new(turn_dir / "attempt_record.json", record)
                prior_attempts.append(record)
                continue
            record.update(
                usable_response=True,
                candidate=candidate,
                candidate_target_partition=candidate_partition(case, candidate),
            )
            _write_new(turn_dir / "execution_request_reserved.json", {
                "schema_version": "spec-feasibility-execution-reservation-1",
                "reserved_at": _utc_now(),
                "candidate_sha256": sha256_json(candidate),
                "case_id": case["case_id"],
                "arm": arm,
                "replicate_id": replicate_id,
                "turn": turn,
            })
            reusable_execution = bool(
                reusable_response
                and source_record is not None
                and source_record.get("execution_run_status") == "ok"
                and source_record.get("o_spec") in {"PASS", "FAIL"}
                and recovery_turn is not None
                and (recovery_turn / "execution_result.json").is_file()
                and (recovery_turn / "execution").is_dir()
            )
            if reusable_execution:
                assert recovery_turn is not None
                shutil.copytree(recovery_turn / "execution", turn_dir / "execution")
                execution = _read_json(recovery_turn / "execution_result.json")
                execution_origin = "reused_stopped_run"
            else:
                execution_request = {
                    "protocol_id": PROTOCOL_ID,
                    "case": case,
                    "candidate": candidate,
                    "replicate_id": replicate_id,
                    "turn": turn,
                    "artifact_dir": str(turn_dir / "execution"),
                }
                execution = _call_environment(
                    config["runtime"]["environment_command"],
                    execution_request,
                    float(config["runtime"]["environment_timeout_seconds"]),
                )
                execution_origin = "new_android_execution"
            _write_new(turn_dir / "execution_result.json", execution)
            record.update(
                execution_origin=execution_origin,
                o_spec=execution.get("o_spec", "UNKNOWN"),
                unknown_reason=execution.get("unknown_reason"),
                public_observations=execution.get("public_observations", []),
                execution_run_status=execution.get("run_status"),
                execution_elapsed_ms=execution.get("elapsed_ms"),
            )
            _write_new(turn_dir / "attempt_record.json", record)
            prior_attempts.append(record)
            if execution.get("run_status") != "ok" or record["o_spec"] == "UNKNOWN":
                stop_signal = {
                    "reason": "execution_evidence_incomplete",
                    "case_id": case["case_id"],
                    "arm": arm,
                    "replicate_id": replicate_id,
                    "turn": turn,
                    "unknown_reason": record["unknown_reason"],
                }
                break
            if record["candidate_target_partition"] == "TARGET" and record["o_spec"] == "FAIL":
                break
        if stop_signal is not None:
            break

    if stop_signal is not None:
        _write_new(output_dir / "STOPPED.json", {**stop_signal, "stopped_at": _utc_now()})
    summary = build_summary(output_dir)
    write_report(output_dir, summary)
    if summary["complete"]:
        _write_new(output_dir / "COMPLETED.json", {
            "protocol_id": PROTOCOL_ID,
            "completed_at": _utc_now(),
            "summary_sha256": _sha256_file(output_dir / "summary.json"),
        })
    _write_sha256s(output_dir)
    return summary


def _trajectory_records(output_dir: Path) -> dict[tuple[str, str, int], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for path in sorted(output_dir.glob("trajectories/**/turn_*/attempt_record.json")):
        record = _read_json(path)
        replicate_id = int(record.get("replicate_id", 1))
        grouped.setdefault((record["case_id"], record["arm"], replicate_id), []).append(record)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["turn"])
    return grouped


def build_summary(output_dir: Path) -> dict[str, Any]:
    grouped = _trajectory_records(output_dir)
    all_rows = [row for rows in grouped.values() for row in rows]
    paid_reservations = list(output_dir.glob("trajectories/**/turn_*/model_request_reserved.json"))
    qualification_path = output_dir / "model_qualification_receipt.json"
    qualification = _read_json(qualification_path) if qualification_path.is_file() else None
    qualification_cost = (
        float(qualification.get("request_cost_usd", 0.0))
        if isinstance(qualification, dict) else 0.0
    )
    completed_paid_records = sum(row.get("paid_model_request") is True for row in all_rows)
    reused_paid_responses = sum(
        row.get("model_request_origin") == "reused_stopped_run" for row in all_rows
    )
    missing_cost_telemetry = max(0, len(paid_reservations) - completed_paid_records)
    attempts_without_reservation = max(0, completed_paid_records - len(paid_reservations))
    execution_order_path = output_dir / "execution_order.json"
    frozen_protocol_path = output_dir / "frozen_protocol.json"
    if execution_order_path.is_file():
        execution_order = _read_json(execution_order_path)
        expected_trajectories = len(execution_order["trajectories"])
    elif frozen_protocol_path.is_file():
        frozen_protocol = _read_json(frozen_protocol_path)
        expected_trajectories = (
            len(frozen_protocol["cases"])
            * len(ARMS)
            * int(frozen_protocol["budget"].get("replicates_per_case_arm", 1))
        )
    else:
        expected_trajectories = 6
    target_at_1 = sum(bool(rows) and rows[0]["candidate_target_partition"] == "TARGET" for rows in grouped.values())
    target_at_3 = sum(any(row["candidate_target_partition"] == "TARGET" for row in rows[:3]) for rows in grouped.values())
    violation_at_1 = sum(
        bool(rows)
        and rows[0]["candidate_target_partition"] == "TARGET"
        and rows[0]["o_spec"] == "FAIL"
        for rows in grouped.values()
    )
    violation_at_3 = sum(
        any(row["candidate_target_partition"] == "TARGET" and row["o_spec"] == "FAIL" for row in rows[:3])
        for rows in grouped.values()
    )
    verdicts = Counter(row["o_spec"] for row in all_rows)
    arm_metrics: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        arm_groups = [
            rows for (case_id, cell_arm, replicate_id), rows in grouped.items()
            if cell_arm == arm
        ]
        arm_rows = [row for rows in arm_groups for row in rows]
        arm_verdicts = Counter(row["o_spec"] for row in arm_rows)
        arm_metrics[arm] = {
            "observed_trajectories": len(arm_groups),
            "attempts": len(arm_rows),
            "usable_response_rate": (
                sum(row.get("usable_response") is True for row in arm_rows) / len(arm_rows)
                if arm_rows else None
            ),
            "targeted_candidate_at_1": sum(
                bool(rows) and rows[0]["candidate_target_partition"] == "TARGET"
                for rows in arm_groups
            ),
            "targeted_candidate_at_3": sum(
                any(row["candidate_target_partition"] == "TARGET" for row in rows[:3])
                for rows in arm_groups
            ),
            "violation_detected_at_1": sum(
                bool(rows)
                and rows[0]["candidate_target_partition"] == "TARGET"
                and rows[0]["o_spec"] == "FAIL"
                for rows in arm_groups
            ),
            "violation_detected_at_3": sum(
                any(
                    row["candidate_target_partition"] == "TARGET" and row["o_spec"] == "FAIL"
                    for row in rows[:3]
                )
                for rows in arm_groups
            ),
            "o_spec_counts": {
                key: arm_verdicts.get(key, 0) for key in ("PASS", "FAIL", "UNKNOWN")
            },
        }
    descriptive_deltas = {
        "P1_minus_P0": {
            metric: arm_metrics["P1"][metric] - arm_metrics["P0"][metric]
            for metric in (
                "targeted_candidate_at_1", "targeted_candidate_at_3",
                "violation_detected_at_1", "violation_detected_at_3",
            )
        },
        "P2_minus_P1": {
            metric: arm_metrics["P2"][metric] - arm_metrics["P1"][metric]
            for metric in (
                "targeted_candidate_at_1", "targeted_candidate_at_3",
                "violation_detected_at_1", "violation_detected_at_3",
            )
        },
    }
    total_input_tokens = 0
    total_output_tokens = 0
    for row in all_rows:
        try:
            input_tokens, output_tokens = usage_tokens(row.get("usage"))
        except ValueError:
            continue
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
    return {
        "schema_version": "spec-feasibility-summary-1",
        "protocol_id": PROTOCOL_ID,
        "descriptive_only": True,
        "expected_trajectories": expected_trajectories,
        "observed_trajectories": len(grouped),
        "qualification_paid_requests": 1 if isinstance(qualification, dict) else 0,
        "paid_experimental_request_reservations": len(paid_reservations),
        "paid_experimental_requests_with_attempt_records": completed_paid_records,
        "reused_paid_model_responses": reused_paid_responses,
        "new_provider_responses": completed_paid_records - reused_paid_responses,
        "usable_responses": sum(row.get("usable_response") is True for row in all_rows),
        "usable_response_rate": (
            sum(row.get("usable_response") is True for row in all_rows) / len(all_rows)
            if all_rows else None
        ),
        "targeted_candidate_at_1": target_at_1,
        "targeted_candidate_at_3": target_at_3,
        "violation_detected_at_1": violation_at_1,
        "violation_detected_at_3": violation_at_3,
        "o_spec_counts": {key: verdicts.get(key, 0) for key in ("PASS", "FAIL", "UNKNOWN")},
        "arm_metrics": arm_metrics,
        "descriptive_arm_deltas": descriptive_deltas,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_model_elapsed_ms": sum(
            float(row["model_elapsed_ms"])
            for row in all_rows if isinstance(row.get("model_elapsed_ms"), (int, float))
        ),
        "total_execution_elapsed_ms": sum(
            float(row["execution_elapsed_ms"])
            for row in all_rows if isinstance(row.get("execution_elapsed_ms"), (int, float))
        ),
        "qualification_cost_usd": qualification_cost,
        "experimental_cost_usd": sum(float(row.get("request_cost_usd", 0.0)) for row in all_rows),
        "total_known_cost_usd": qualification_cost + sum(
            float(row.get("request_cost_usd", 0.0)) for row in all_rows
        ),
        "requests_without_cost_telemetry": missing_cost_telemetry,
        "attempt_records_without_request_reservation": attempts_without_reservation,
        "complete": len(grouped) == expected_trajectories and not (output_dir / "STOPPED.json").exists(),
    }


def write_report(output_dir: Path, summary: dict[str, Any]) -> None:
    summary_json = output_dir / "summary.json"
    if not summary_json.exists():
        _write_new(summary_json, summary)
    rows = []
    for (case_id, arm, replicate_id), attempts in sorted(_trajectory_records(output_dir).items()):
        rows.append({
            "case_id": case_id,
            "arm": arm,
            "replicate_id": replicate_id,
            "turns": len(attempts),
            "usable_responses": sum(row["usable_response"] for row in attempts),
            "targeted_by_3": any(row["candidate_target_partition"] == "TARGET" for row in attempts[:3]),
            "violation_by_3": any(
                row["candidate_target_partition"] == "TARGET" and row["o_spec"] == "FAIL"
                for row in attempts[:3]
            ),
            "pass": sum(row["o_spec"] == "PASS" for row in attempts),
            "fail": sum(row["o_spec"] == "FAIL" for row in attempts),
            "unknown": sum(row["o_spec"] == "UNKNOWN" for row in attempts),
            "cost_usd": sum(float(row["request_cost_usd"]) for row in attempts),
        })
    csv_path = output_dir / "summary.csv"
    if not csv_path.exists():
        with csv_path.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ["case_id", "arm"])
            writer.writeheader()
            writer.writerows(rows)
    arm_lines = [
        "| Arm | Trajectories | Attempts | Usable rate | Target @1 | Target @3 | Violation @1 | Violation @3 | PASS/FAIL/UNKNOWN |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        metrics = summary["arm_metrics"][arm]
        counts = metrics["o_spec_counts"]
        arm_lines.append(
            f"| {arm} | {metrics['observed_trajectories']} | {metrics['attempts']} | "
            f"{metrics['usable_response_rate']} | {metrics['targeted_candidate_at_1']} | "
            f"{metrics['targeted_candidate_at_3']} | {metrics['violation_detected_at_1']} | "
            f"{metrics['violation_detected_at_3']} | {counts['PASS']}/{counts['FAIL']}/{counts['UNKNOWN']} |"
        )
    delta = summary["descriptive_arm_deltas"]
    report = "\n".join([
        "# Specification-aware feasibility pilot",
        "",
        "This report is descriptive only. It evaluates integration of a supplied, source-backed local requirement contract; it does not evaluate automatic discovery of hidden constraints.",
        "",
        f"- Complete: `{summary['complete']}`",
        f"- Observed trajectories: `{summary['observed_trajectories']}/{summary['expected_trajectories']}`",
        f"- Paid request reservations (qualification + experimental): `{summary['qualification_paid_requests']}` + `{summary['paid_experimental_request_reservations']}`",
        f"- Experimental requests with complete attempt records: `{summary['paid_experimental_requests_with_attempt_records']}`",
        f"- Usable response rate: `{summary['usable_response_rate']}`",
        f"- Targeted candidate @1 / @3: `{summary['targeted_candidate_at_1']}` / `{summary['targeted_candidate_at_3']}`",
        f"- Violation detected @1 / @3: `{summary['violation_detected_at_1']}` / `{summary['violation_detected_at_3']}`",
        f"- O_spec PASS / FAIL / UNKNOWN: `{summary['o_spec_counts']['PASS']}` / `{summary['o_spec_counts']['FAIL']}` / `{summary['o_spec_counts']['UNKNOWN']}`",
        f"- Known qualification / experimental / total cost (USD): `{summary['qualification_cost_usd']:.6f}` / `{summary['experimental_cost_usd']:.6f}` / `{summary['total_known_cost_usd']:.6f}`",
        f"- Requests without cost telemetry: `{summary['requests_without_cost_telemetry']}`",
        "",
        "## Per-arm descriptive results",
        "",
        *arm_lines,
        "",
        f"P1-P0 supplied-specification deltas: `{json.dumps(delta['P1_minus_P0'], sort_keys=True)}`.",
        f"P2-P1 specification-feedback deltas: `{json.dumps(delta['P2_minus_P1'], sort_keys=True)}`.",
        "These are two-case descriptive counts, not inferential estimates.",
        "",
    ])
    report_path = output_dir / "report.md"
    if not report_path.exists():
        _write_new(report_path, report)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reviewer-2 specification feasibility pilot")
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("preflight")
    qualify = subparsers.add_parser("qualify-model")
    qualify.add_argument("--output", type=Path, required=True)
    qualify.add_argument("--confirm", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--confirm", required=True)
    run.add_argument("--recovery-from", type=Path)
    run.add_argument("--case-id")
    run.add_argument("--shard-index", type=int, default=0)
    run.add_argument("--shard-count", type=int, default=1)
    run.add_argument("--device-serial")
    run.add_argument("--avd-name")
    run.add_argument("--avd-path", type=Path)
    report = subparsers.add_parser("report")
    report.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = _read_json(config_path)
    if args.command == "validate":
        validate_config(config)
        value = {
            "valid": True,
            "protocol_id": PROTOCOL_ID,
            "protocol_status": config.get("protocol_status"),
            "live_run_enabled": config.get("live_run_enabled"),
            "config_sha256": _sha256_file(config_path),
        }
    elif args.command == "preflight":
        value = local_preflight(config_path, config)
    elif args.command == "qualify-model":
        value = qualify_model(config_path, config, args.output.resolve(), args.confirm)
    elif args.command == "run":
        value = run_pilot(
            config_path,
            config,
            args.output_dir.resolve(),
            args.confirm,
            args.recovery_from.resolve() if args.recovery_from else None,
            args.case_id,
            args.shard_index,
            args.shard_count,
            args.device_serial,
            args.avd_name,
            args.avd_path.resolve() if args.avd_path else None,
        )
    else:
        value = build_summary(args.run_dir.resolve())
        write_report(args.run_dir.resolve(), value)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
