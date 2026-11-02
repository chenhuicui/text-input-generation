#!/usr/bin/env python3
"""Build the evidence-bound A/B/C/D/E/F protocol review data.

This script is read-only with respect to experiment inputs.  It combines the
frozen case registry, prompts, prior UI/specification audits, and validator
excerpts into a portable review model.  Hashes are retained only as audit
metadata and are never labelled as specifications.  The A-F protocol recorded
here supersedes E2-structure-v1 for future runs; it does not relabel old runs.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rows_by_id(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["case_id"]: row for row in value["cases"]}


def app_name(prompt: dict[str, Any], fallback: str) -> str:
    match = re.search(r"app named\s+(.+?),\s+and there", str(prompt.get("global", "")), re.I)
    return match.group(1).strip() if match else fallback


def base_prompt(prompt: dict[str, Any]) -> str:
    return "\n".join(
        str(prompt[key])
        for key in ("global", "component", "adjacent", "restrictive", "guiding")
        if str(prompt.get(key, "")).strip()
    ).strip()


def specification_judgment(
    case: dict[str, Any], de: dict[str, Any], bug: dict[str, Any] | None
) -> dict[str, Any]:
    e = de["e"]
    implementation = e.get("implementation_contracts") or []
    if case["task"] == "invalid_bug":
        assert bug is not None
        return {
            "clean_requirement_status": "未建立",
            "independent_requirement_source": "未找到与 benchmark trigger 独立且版本绑定的 requirements 文档",
            "surface_or_store_material": "Bug issue/PR 属于 benchmark 来源，不是独立 specification",
            "implementation_contracts": implementation,
            "oracle_derived_rule_status": "可用，但属于完整答案泄漏",
            "recommended_role": "仅用于 Specification Integration Pilot 的来源审计；不进入 A-F 反馈结果",
            "leakage_risk": "极高：issue、fix 或 validator 往往直接暴露已知触发条件",
            "first_turn_policy": "若未来找到独立 requirement，应从首轮提供；当前材料不能构成 clean specification",
            "source_note": bug.get("e_clean_reason", "benchmark-linked source"),
        }
    play = e.get("current_play_source") or {}
    if implementation:
        role = "实现约束候选：只能称 APK 实现事实；不可称官方业务 requirement"
    else:
        role = "当前无 clean specification；需继续寻找版本绑定的自然语言 requirements/RE artifact"
    return {
        "clean_requirement_status": "未建立",
        "independent_requirement_source": (
            "Google Play 当前描述未发现字段级约束"
            if play.get("status") == "retrieved"
            else "当前商店页不可用；未完成外部开发者文档的版本绑定检索"
        ),
        "surface_or_store_material": e.get("surface_spec") or "无新增约束；页面可见提示若存在，已在 A/B/C base prompt 中",
        "implementation_contracts": implementation,
        "oracle_derived_rule_status": "validator 源码齐全，但它是评测答案/接受规则，不是 requirements",
        "recommended_role": role,
        "leakage_risk": "高：将 validator 改写后给模型会直接暴露评测判定边界",
        "first_turn_policy": "真实 specification 应从首轮提供，或用于构造 specification-aware Oracle；不应作为 A-F 失败反馈追加",
        "source_note": e.get("official_independent_status", "not audited"),
    }


def build(repo: Path, scaffold_path: Path) -> dict[str, Any]:
    scaffold = read_json(scaffold_path)
    registry = read_json(repo / "output/e2_structure_v1_20260920/case_registry.json")
    de_all = read_json(repo / "output/de_spec_audit_20260924/de_case_matrix.json")
    de = rows_by_id(de_all)
    upper = rows_by_id(read_json(repo / "output/de_spec_audit_20260924/oracle_upper_specifications.json"))
    page_d = rows_by_id(read_json(repo / "output/de_spec_audit_20260924/page_d_adjudication.json"))
    bug_sources = rows_by_id(read_json(repo / "output/de_spec_audit_20260924/bug_de_source_adjudication.json"))
    apk = rows_by_id(read_json(repo / "output/de_spec_audit_20260924/apk_resource_evidence.json"))
    page_play = rows_by_id(read_json(repo / "output/de_spec_audit_20260924/page_play_sources.json"))
    abcdef_preflight = read_json(repo / "output/e2_abcdef_v1_20260925/preflight_after_non_f_gates/preflight_report.json")
    page_prompts = read_json(repo / "tosem_major/apk_info/cache/pptr_prompts.json")
    bug_prompts = read_json(repo / "tosem_major/apk_info/cache/bug_prompts.json")

    cases = []
    for case in registry["cases"]:
        cid = case["case_id"]
        is_page = case["task"] == "valid_pptr"
        prompt = page_prompts[case["ordinal_in_source"] - 1] if is_page else bug_prompts[case["ordinal_in_source"] - 1]
        de_row = de[cid]
        d_row = page_d.get(cid)
        bug_row = bug_sources.get(cid)
        upper_row = upper[cid]
        apk_row = apk[cid]
        spec = specification_judgment(case, de_row, bug_row)
        formal = case["formal_applicability"]
        readiness = case["protocol_readiness"]
        if not formal["in_scope"]:
            run_group = "不进入 A-F 正式实验：保留为有证据的 N/A"
        elif is_page:
            run_group = "Page D/E 实机 smoke 已通过；等待全局 F 分析器 API 资格与 formal switch"
        else:
            run_group = "Bug 字符串谓词仍是 verdict Oracle；独立 App 观察合同与 D/E smoke 已通过"
        cases.append({
            "case_id": cid,
            "ordinal": case["ordinal_in_source"],
            "task": case["task"],
            "task_label": "Page" if is_page else "Bug",
            "app": app_name(prompt, de_row.get("app_or_package") or case["source_target"]),
            "package_or_source_target": de_row.get("app_or_package") or case["source_target"],
            "required_resource_ids": case["required_resource_ids"],
            "formal": {
                "in_scope": formal["in_scope"],
                "disposition": formal["disposition"],
                "reason_code": formal.get("reason_code"),
                "rationale": formal.get("rationale"),
                "legacy_protocol_formal_run_ready": readiness["formal_run_ready"],
                "blockers": readiness.get("blockers", []),
                "abcdef_formal_run_ready": False,
                "abcdef_blocker": "本例非 F 物料与 smoke 已通过；正式运行只等待全局 F API 资格和 formal switch",
            },
            "base_material": {
                "global": prompt.get("global", ""),
                "component": prompt.get("component", ""),
                "adjacent": prompt.get("adjacent", ""),
                "restrictive": prompt.get("restrictive", ""),
                "guiding": prompt.get("guiding", ""),
                "assembled_prompt": base_prompt(prompt),
            },
            "A": {
                "name": "独立重试",
                "model_visible": "每次在无历史的新会话中重新发送完全相同的 base prompt；不说明上一轮失败",
                "research_factor": "只增加采样机会",
                "case_specific_material": "base_material",
                "prompt_contract": "[UI CONTEXT] + [TASK] + [OUTPUT CONTRACT]；不包含 prior candidate、status、截图、UI delta 或诊断",
            },
            "B": {
                "name": "候选历史",
                "model_visible": "base prompt + 最近 5 个失败候选；不提供执行状态或失败原因",
                "research_factor": "候选历史是否减少重复并扩大探索",
                "prompt_contract": "[PREVIOUS CANDIDATES] + Generate one new candidate. Do not exactly repeat a previous candidate. No execution result is provided.",
            },
            "C": {
                "name": "执行状态",
                "model_visible": "B + 每个候选的 PASS / FAIL / UNKNOWN；不提供原因、截图或 UI 文案",
                "research_factor": "明确执行状态相对候选历史的增量作用",
                "prompt_contract": "[EXECUTION STATUS] attempt=<n>; status=PASS|FAIL|UNKNOWN",
                "status_semantics": {
                    "PASS": "任务 Oracle 确认目标达到",
                    "FAIL": "执行完成且目标明确未达到",
                    "UNKNOWN": "环境、网络、观察或 Oracle 不足，无法作语义判定",
                },
            },
            "D": {
                "name": "状态 + 时序截图",
                "model_visible": "C + 执行前截图 + 点击后时序关键帧；不附加对截图的文字解释",
                "research_factor": "原始视觉执行证据的增量作用",
                "prompt_contract": "[BEFORE EXECUTION] <image>; [AFTER EXECUTION FRAMES] <first-change, transient-message, settled>",
                "capture_protocol": {
                    "pre_action": "t=-0.5s 开始短时录像；t=-0.1s 保存截图与 XML",
                    "action": "t=0 执行冻结的触发动作",
                    "burst": ["t≈0", "t=0.25s", "t=0.5s", "t=1s", "t=2s"],
                    "tail": "随后每 1s 捕获，直到逐例冻结的稳定条件或超时",
                    "model_frames": ["before", "first_changed", "transient_message_peak", "settled"],
                },
                "calibration_status": "passed_non_formal_smoke" if formal["in_scope"] else "not_applicable",
                "current_runner_gap": "112 Page + 37 Bug 时序截图 smoke 已通过；B017 明确使用冻结初始 XML 加实时像素证据，其余为实时 hierarchy",
            },
            "E": {
                "name": "状态 + 截图 + 可观察 UI 变化",
                "model_visible": "D + 从同一时间窗口确定性提取的页面变化、公开错误文案、Toast/Dialog/Snackbar 和导航结果",
                "research_factor": "显式、精简的可观察执行摘要相对原始截图的增量作用",
                "prompt_contract": "[OBSERVED UI CHANGES] navigation_observed, screen_before/after, added/removed_visible_text, public_error_messages, dialog_or_toast, field_state_changes",
                "extraction_policy": "只允许来自截图、XML、Accessibility 或明确系统执行记录的事实；不得推断隐藏业务规则",
                "legacy_evidence_candidate": {
                    "status": de_row.get("d_status"),
                    "messages": de_row.get("d_messages", []),
                    "adjudication": d_row.get("d_rationale") if d_row else (bug_row.get("d_reason") if bug_row else ""),
                    "note": "旧 D 审计材料只作为新版 E 校准候选，不代表新版 E 已就绪",
                },
                "calibration_status": "passed_non_formal_smoke" if formal["in_scope"] else "not_applicable",
            },
            "F": {
                "name": "外部分析器诊断反馈",
                "model_visible": "E + 固定外部分析器对本次执行证据的简短原因分析；不给修改建议",
                "research_factor": "证据约束的诊断分析是否帮助下一轮生成",
                "analyzer": {
                    "role": "external_fixed_analyzer",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "medium",
                    "output_format": "strict_json",
                    "prompt_version": "f-analyzer-v1",
                    "evaluated_as_subject_model": False,
                },
                "allowed_inputs": ["current candidate", "task goal", "PASS/FAIL/UNKNOWN", "before/after frames", "E UI observations"],
                "forbidden_inputs": ["validator source/name", "bug trigger", "expected string/regex", "successful answer", "specification", "future attempts"],
                "output_fields": ["execution_status", "diagnosis_category", "diagnosis", "support_level", "temporal_summary", "evidence_refs", "alternative_explanations", "uncertainty", "recommendation=null"],
                "leakage_gate": "含建议值、隐藏规则、私有 Oracle 或无证据确定性原因时记 analysis_invalid，禁止注入下一轮",
                "cost_boundary": "F 每个失败轮次增加一次分析调用；必须单列 calls/tokens/time/cost",
            },
            "specification_pilot": spec,
            "oracle": {
                "kind": case["formal_oracle"].get("oracle_kind"),
                "version": case["formal_oracle"].get("version"),
                "available": case["formal_oracle"].get("available"),
                "rule_source": upper_row["validator_path"],
                "validate_method": upper_row["validate_method"],
                "helper_sources": upper_row.get("common_helper_sources", {}),
                "model_visibility": "禁止。Oracle 只用于执行后判定；给模型即为答案泄漏",
            },
            "implementation_evidence": {
                "apk_status": apk_row.get("status"),
                "version_code": apk_row.get("version_code"),
                "field_layout_contracts": apk_row.get("target_layout_bindings", []),
                "candidate_resource_strings": apk_row.get("candidate_resource_strings", []),
                "play_source": page_play.get(cid),
                "bug_source": bug_row,
            },
            "audit_only": {
                "prompt_sha256": case["extracted_prompt_sha256"],
                "prompt_record_sha256": case["prompt_record_sha256"],
                "validator_sha256": case["source_files"]["validator_sha256"],
                "cached_xml_sha256": case["source_files"]["cached_xml_sha256"],
                "apk_sha256": case.get("mirror", {}).get("source_sha256"),
                "manifest_sha256": case.get("mirror", {}).get("manifest_sha256"),
                "policy": "只用于数据身份、版本和漂移核验；不发送给模型；不称为 specification",
            },
            "material_paths": {
                **case["source_files"],
                "manifest": case.get("mirror", {}).get("manifest_path"),
                "snapshot": case.get("mirror", {}).get("snapshot_path"),
            },
            "run_group": run_group,
        })

    assert len(cases) == 152
    assert [row["case_id"] for row in cases[:115]] == [f"P{i:03d}" for i in range(1, 116)]
    assert [row["case_id"] for row in cases[115:]] == [f"B{i:03d}" for i in range(1, 38)]

    scaffold["paper"].update({
        "title": "115 个 Page 与 37 个 Bug：A/B/C/D/E/F 反馈协议逐例审阅工作台",
        "title_locked": False,
    })
    scaffold["report"].update({
        "version": "2026-09-25-v6-non-f-gates",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "display_title": "A-F Prompt、时序 UI 证据、外部诊断与 Specification Discussion Pilot",
        "summary": (
            "本版根据作者与 Codex 的最新讨论，废止把 B/C 定义为 legacy 文本与等信息 structured JSON 的方案。"
            "新版反馈条件按信息增量定义：A 独立重试；B 候选历史；C PASS/FAIL/UNKNOWN；D 状态加执行前后时序截图；"
            "E 再加从同一执行窗口提取的真实 UI 变化与公开错误文案；F 再加固定外部 gpt-5.6-sol（medium）"
            "生成的证据约束诊断，且不允许给建议或读取私有 Oracle。Specification/requirements 不属于 A-F，"
            "只做小规模 Integration Pilot，在 Discussion 中回答审稿人，不新增独立 RQ。A-F runner、截图证据链、"
            "UI delta、F analyzer、预检和 crash-safe ledger 已完成实现；112 Page、37 Bug 和三个 subject 模型的"
            "最大真实反馈图 smoke 已通过。正式实验仍保持关闭，等待 F API 资格。"
        ),
        "scope": (
            "覆盖 P001-P115 与 B001-B037 的离线协议审阅。逐例显示 base prompt、A-F 模型可见增量、"
            "旧 UI/spec 来源审计、Oracle、APK/issue/Play 物料、执行适用性与审计哈希。"
            "本页记录协议、物料和当前实现状态：本轮只运行资格探测和不计入结果的 App smoke，未运行正式 subject 实验、未改写快照，也不把旧 E2 结果重命名为 A-F 结果。"
        ),
        "warnings": [
            "非 F 门禁已通过：112/112 Page 截图 smoke、37/37 Bug App 观察 smoke、37/37 Bug 观察合同和三个 subject 模型最大真实反馈图资格。",
            "正式预检当前只阻止两项：F 分析器 API 资格和 formal switch。按作者要求，gpt-5.6-sol 尚未配置期间不重试 F，formal switch 保持关闭。",
            "逐例截图时序必须用已知控制输入先行校准并在正式运行前冻结，不能看到模型结果后临时改等待时间。",
            "F 是被测模型加固定外部分析器的 analysis-augmented protocol；其额外调用、Token、延迟和成本必须单列。",
            "Specification Integration Pilot 只使用可追溯 requirements/RE artifacts；validator、bug trigger、hash 和当前 UI 抽取不能冒充 specification。",
        ],
    })
    scaffold["sources"] = [
        {"id":"SRC-REVIEW","role":"reviewer","label":"Reviewer 2：W2/W3/W5 原文","path":"output/tosem_second_major_review_20260906/decision_email.txt","locator":"136-157, 165-168","read_status":"read"},
        {"id":"SRC-AUTHOR","role":"discussion","label":"作者 2026-09-24：A-F、截图时序、外部分析器与 specification 范围决定","path":"conversation:current-codex-task","locator":"当前任务最新讨论","read_status":"read"},
        {"id":"SRC-PROTOCOL","role":"code","label":"旧 E2-structure-v1 提示与 runner（历史实现，不再代表新协议）","path":"tosem_major/text_input_eval/supplementary/structured_templates.py; structured_runner.py","locator":"build_shared_information, _render_legacy, render_feedback, load_base_prompt","read_status":"read"},
        {"id":"SRC-CAPTURE","role":"code","label":"当前 Android 执行与证据捕获实现","path":"tosem_major/text_input_eval/supplementary/structured_android.py","locator":"_save_evidence, execute_turn: observation loop and final XML","read_status":"read"},
        {"id":"SRC-ABCDEF","role":"code","label":"新版 A-F runner、Prompt、截图、UI delta、F analyzer 与 Oracle/观察分离","path":"tosem_major/text_input_eval/supplementary/abcdef_*.py","locator":"E2-abcdef-v1 modules and tests","read_status":"read"},
        {"id":"SRC-PREFLIGHT","role":"data","label":"非 F 门禁完成后的 A-F 预检报告","path":"output/e2_abcdef_v1_20260925/preflight_after_non_f_gates/preflight_report.json","locator":"gates, counts, blocking_gates","read_status":"read"},
        {"id":"SRC-PAGE-SMOKE","role":"data","label":"112 个 Page 时序截图 smoke","path":"output/e2_abcdef_v1_20260925/page_visual_smoke_v3/report.json","locator":"112/112 passed","read_status":"read"},
        {"id":"SRC-BUG-SMOKE","role":"data","label":"37 个 Bug App 观察 smoke","path":"output/e2_abcdef_v1_20260925/bug_visual_smoke_v1/report.json","locator":"37/37 passed","read_status":"read"},
        {"id":"SRC-IMAGE-PROBE","role":"data","label":"三个 subject 模型最大反馈图资格","path":"output/e2_abcdef_v1_20260925/subject_image_preflight_max_page_v1.json","locator":"1,911,841 bytes; 3/3 exact STORE","read_status":"read"},
        {"id":"SRC-LEGACY-FEEDBACK","role":"code","label":"论文旧 feedback 的真实内容","path":"tosem_major/text_input_eval/services/feedback_based.py","locator":"FeedbackBuilder.build_failed_history_text, build_feedback, check_turn_result","read_status":"read"},
        {"id":"SRC-F-ANALYZER","role":"documentation","label":"GPT-5.6 Sol 官方模型能力","path":"https://developers.openai.com/api/docs/models/gpt-5.6-sol","locator":"image input, structured outputs, reasoning effort","read_status":"read"},
        {"id":"SRC-F-ACCESS","role":"data","label":"F 分析器模型访问预检","path":"output/e2_abcdef_v1_20260924/f_analyzer_model_access_preflight_20260924.json","locator":"direct_openai_api, must_gateway, formal_gate","read_status":"read"},
        {"id":"SRC-REGISTRY","role":"data","label":"152 例冻结登记表与正式适用性","path":"output/e2_structure_v1_20260920/case_registry.json","locator":"cases, counts","read_status":"read"},
        {"id":"SRC-PROMPTS","role":"data","label":"115 Page 与 37 Bug 的 extracted prompts","path":"tosem_major/apk_info/cache/pptr_prompts.json; bug_prompts.json","locator":"source order 1-115 / 1-37","read_status":"read"},
        {"id":"SRC-DE","role":"data","label":"D/E 逐例证据矩阵","path":"output/de_spec_audit_20260924/de_case_matrix.json","locator":"summary, cases","read_status":"read"},
        {"id":"SRC-DPAGE","role":"data","label":"Page D 人工裁决","path":"output/de_spec_audit_20260924/page_d_adjudication.json","locator":"115 cases","read_status":"read"},
        {"id":"SRC-BUG","role":"data","label":"37 Bug issue/PR 来源裁决","path":"output/de_spec_audit_20260924/bug_de_source_adjudication.json","locator":"37 cases","read_status":"read"},
        {"id":"SRC-APK","role":"data","label":"152 APK 字段与资源物料","path":"output/de_spec_audit_20260924/apk_resource_evidence.json","locator":"152 cases","read_status":"read"},
        {"id":"SRC-PLAY","role":"data","label":"115 Page 当前 Google Play 来源核对","path":"output/de_spec_audit_20260924/page_play_sources.json","locator":"113 retrieved, 2 unavailable","read_status":"read"},
        {"id":"SRC-UPPER","role":"code","label":"152 validator/Oracle 源码抽取","path":"output/de_spec_audit_20260924/oracle_upper_specifications.json","locator":"validate_method and helpers","read_status":"read"},
        {"id":"SRC-BACKUP","role":"data","label":"152 例快照恢复前备份","path":"/Volumes/Extreme Pro/emulator/CD_115page_37bug/backups/avd_snapshot_backup_152_20260920_before_oracle_calibration/backup_manifest.json","locator":"mounted and readable on 2026-09-24","read_status":"read"},
    ]
    scaffold["comments"] = [
        {"id":"R2-W2-CRITIQUE","reviewer":"Reviewer 2","quote":"The evaluation relies entirely on heuristic oracles: Page Pass-Through Rate (PPTR) and detecting a predefined set of 37 known bugs (BDR). From a software engineering standpoint, this is highly limiting. PPTR only verifies that some state transition occurred, not that the transition was correct, safe, or compliant with requirements. The paper completely ignores how formal specifications (e.g., state machines, OCL constraints, or use-case diagrams) could be used to generate test oracles or bound the LLM's generation space.","source_id":"SRC-REVIEW","locator":"lines 136-140","kind":"request"},
        {"id":"R2-W2-PROPOSAL","reviewer":"Reviewer 2","quote":"I recommend adding a dedicated discussion (or a preliminary theoretical framework) on how the proposed pipeline could ingest formal specifications or RE artifacts. For instance, if a formal constraint specifies input.length > 8 && regex(input), the methodology should use this formal spec to automatically generate the test oracle, rather than relying on the LLM to guess the validation rules.","source_id":"SRC-REVIEW","locator":"line 140","kind":"request"},
        {"id":"R2-W3","reviewer":"Reviewer 2","quote":"The Extracted Context relies purely on UIAutomator metadata (hint texts, adjacent labels, resource IDs). This is purely syntactic and surface-level. In RE, we know that UI labels are notoriously poor indicators of underlying business rules.","source_id":"SRC-REVIEW","locator":"lines 142-146","kind":"request"},
        {"id":"R2-W5","reviewer":"Reviewer 2","quote":"The feedback protocol (RQ2) is essentially a retry mechanism based on JSON parsing errors or lack of page transition. It lacks a formal model of the system's state space. Appending raw error logs to a prompt is a brittle, ad-hoc solution.","source_id":"SRC-REVIEW","locator":"lines 153-157","kind":"request"},
        {"id":"AUTHOR-SPEC-BOUNDARY","reviewer":"Author","quote":"哈希校验不是自然语言 specification；specification 应来自 requirements。Specification 接入只做初步实验并在 Discussion 回答审稿人，不单设 RQ。","source_id":"SRC-AUTHOR","locator":"2026-09-24 当前讨论","kind":"decision"},
        {"id":"AUTHOR-ABCDEF","reviewer":"Author","quote":"保留 A/B；C 给 PASS/FAIL/UNKNOWN；D 给截图 UI；E 给状态、截图和真实页面变化/公开错误；F 用未参与评估的 gpt-5.6-sol 分析本次执行原因，不给修改建议。","source_id":"SRC-AUTHOR","locator":"2026-09-24 当前讨论","kind":"decision"},
        {"id":"AUTHOR-CAPTURE","reviewer":"Author","quote":"不同 App 的 UI 变化和 Toast 时序不同，需判断是否先行验证，避免立即或延迟单张截图漏证据。","source_id":"SRC-AUTHOR","locator":"2026-09-24 当前讨论","kind":"question"},
    ]
    scaffold["groups"] = [
        {"id":"G-SPEC","title":"Specification 只作为 Discussion Integration Pilot","priority":"P0","comment_ids":["R2-W2-CRITIQUE","R2-W2-PROPOSAL","R2-W3","AUTHOR-SPEC-BOUNDARY"],"interpretation":"审稿人要求 formal specifications 或 RE artifacts 如何约束生成或构造 Oracle；这不是 A-F feedback 的一层。","judgment":"不新增 RQ，不并入 A-F 指标。对有独立、版本绑定来源的案例做少量 paired demonstration，报告接入路径、覆盖和限制；若没有 clean source，只展示接口和 limitation。","reviewer_lens":"来源是否独立于 benchmark trigger/validator？是否只作 exploratory evidence，而非新主结论？","response_strategy":"在 Methods/Discussion 展示 Specification Adapter 和少量示例，不把 hash、当前 UI 或私有 Oracle 改写成 requirement。","task_ids":["T-SPEC-SOURCES","T-SPEC-PILOT"],"evidence_ids":["EV-NO-CLEAN-SPEC","EV-ORACLE-LEAK"],"reply_draft":"我们增加一个非 RQ 的初步 specification-integration pilot，展示 requirements 如何约束生成和构造 scoped oracle，并将无来源或不可观察部分保留为 unknown。"},
        {"id":"G-FEEDBACK","title":"A-F 信息增量反馈协议","priority":"P0","comment_ids":["R2-W5","AUTHOR-ABCDEF"],"interpretation":"A-F 不再比较文本与 JSON 格式，而是依次比较重试、历史、状态、原始截图、显式 UI 变化和外部证据分析。","judgment":"该设计有可解释的信息梯度。F 是固定外部分析器模块而非被测模型自身能力；D/E 必须基于先行时序校准；旧 E2 和旧 smoke 不能重命名。","reviewer_lens":"每一臂是否只新增声明的信息？F 是否泄漏私有 Oracle？额外计算成本是否单列？","response_strategy":"冻结共同首轮、预算、恢复、截图窗口、E extractor、F analyzer prompt 与 leakage gate，再做新版 smoke。","task_ids":["T-PROMPTS","T-CAPTURE-CALIBRATION","T-CAPTURE-RUNNER","T-UI-EXTRACTOR","T-F-ANALYZER"],"evidence_ids":["EV-LEGACY-FEEDBACK","EV-CAPTURE-GAP","EV-OLD-RESULT-BOUNDARY"],"reply_draft":"我们把完整协议拆成六个可审计的信息条件，并将原始视觉证据、确定性 UI 摘要与外部模型诊断分开。"},
        {"id":"G-CASE-MATRIX","title":"152 例 A-F 物料与适用性逐例核对","priority":"P1","comment_ids":["AUTHOR-ABCDEF","AUTHOR-CAPTURE","AUTHOR-SPEC-BOUNDARY"],"interpretation":"作者需要逐例确认 base prompt、A-F 增量、截图时序、旧 UI 证据候选、specification 来源、Oracle 和运行计划。","judgment":"旧登记表有 149 个正式适用案例、3 个 Page N/A，但新版 A-F 全部仍待 Prompt 与 D/E/F 实现和 smoke。旧 ready 不等于新版 ready。","reviewer_lens":"每一臂是否只收到合同允许的信息？截图窗口是否逐例冻结？F 是否泄漏？spec 是否独立？","response_strategy":"以 152 行矩阵逐例显示新协议与旧执行基础，不把旧 UI 审计或旧 smoke 冒充新版证据。","task_ids":["T-MATRIX"],"evidence_ids":["EV-READY","EV-CAPTURE-GAP","EV-OLD-RESULT-BOUNDARY"],"reply_draft":"逐例协议清单将 A-F Prompt、捕获计划、分析器边界、specification 来源和 Oracle 分开记录。"},
    ]
    scaffold["tasks"] = [
        {"id":"T-PROMPTS","title":"实现并冻结 A-F Prompt 合同","group_ids":["G-FEEDBACK"],"kind":"rewrite","necessity":"required","basis":"author_decision","location":{"source_id":"SRC-ABCDEF","locator":"abcdef_contract.py, abcdef_templates.py, abcdef_runner.py"},"action":"废止格式型 B/C；实现 A 独立重试、B 历史、C 状态、D 截图、E UI delta、F 诊断。hash 只留 ledger。","inputs":"本工作台 protocol_arms 和 152 行 base material。","procedure":"增加 arm-delta、证据绑定和 forbidden-field tests；历史结果保持旧 protocol_id。","outputs":"E2-abcdef-v1 模板、runner、schema 和测试。","completion_check":"18 个新版离线测试通过；每臂模型可见增量与合同一致。","run_status":"done","depends_on":[]},
        {"id":"T-CAPTURE-CALIBRATION","title":"逐例校准 UI 变化与 transient message 时序","group_ids":["G-FEEDBACK"],"kind":"verification","necessity":"required","basis":"author_decision","location":{"source_id":"SRC-CAPTURE","locator":"per-case capture plan"},"action":"用冻结控制候选执行短时录像、burst screenshot/XML 与恢复；正式运行前固定观察合同。","inputs":"AVD 快照、控制候选、现有 observation_seconds。","procedure":"不使用正式模型结果调参；保留录屏 PTS、截图 lag、恢复方式和证据 hash。","outputs":"112 Page + 37 Bug capture smoke 与 3 个证据 N/A。","completion_check":"112/112 Page 与 37/37 Bug smoke 通过；B017 降级来源明确标注。","run_status":"done","depends_on":[]},
        {"id":"T-CAPTURE-RUNNER","title":"实现 D 的时序截图证据链","group_ids":["G-FEEDBACK"],"kind":"implementation","necessity":"required","basis":"author_decision","location":{"source_id":"SRC-ABCDEF","locator":"abcdef_android.py, abcdef_evidence.py"},"action":"已增加 pre-action、burst、tail 和短时录像；确定性选择 before/first-change/transient-peak/settled 帧。","inputs":"T-CAPTURE-CALIBRATION 的逐例时间表。","procedure":"每帧保存时间戳、PNG/XML hash、来源和 foreground。","outputs":"D evidence bundle 与 149 例实机 smoke。","completion_check":"112/112 Page、37/37 Bug 和三模型最大图片资格通过。","run_status":"done","depends_on":["T-CAPTURE-CALIBRATION"]},
        {"id":"T-UI-EXTRACTOR","title":"实现 E 的确定性 UI 变化摘要","group_ids":["G-FEEDBACK"],"kind":"implementation","necessity":"required","basis":"author_decision","location":{"source_id":"SRC-ABCDEF","locator":"abcdef_evidence.py"},"action":"已从同一 D bundle 提取导航、可见文字增删、公开错误、Dialog/Toast 和字段状态变化。","inputs":"D 的时序 PNG/XML/Accessibility 证据。","procedure":"不使用 LLM 推断；只输出固定白名单字段；B017 标注 frozen_initial_xml。","outputs":"E observation schema 与逐例证据 bundle。","completion_check":"证据绑定/篡改测试和 149 例实机 smoke 通过。","run_status":"done","depends_on":["T-CAPTURE-RUNNER"]},
        {"id":"T-F-ANALYZER","title":"实现固定 GPT-5.6-Sol medium 分析器和泄漏门禁","group_ids":["G-FEEDBACK"],"kind":"implementation","necessity":"required","basis":"author_decision","location":{"source_id":"SRC-ABCDEF","locator":"abcdef_analyzer.py"},"action":"已实现只基于 E 证据的 strict JSON 原因分析；recommendation 必须为 null，违规输出 analysis_invalid。","inputs":"当前 candidate、task goal、status、D frames、E observations。","procedure":"固定 gpt-5.6-sol、medium、schema 和 forbidden-output gate。","outputs":"F diagnosis record 与调用审计已离线实现。","completion_check":"离线测试通过；当前 MUST 网关对 gpt-5.6-sol/gpt-5.6 均返回不支持，本机无 OPENAI_API_KEY，等待支持方提供可用路由或凭据。","run_status":"blocked","depends_on":["T-UI-EXTRACTOR"]},
        {"id":"T-SPEC-SOURCES","title":"继续寻找独立、版本绑定的 requirements/RE artifacts","group_ids":["G-SPEC"],"kind":"verification","necessity":"conditional","basis":"reviewer_explicit","location":{"source_id":"SRC-PLAY","locator":"developer websites and versioned docs"},"action":"检索开发者文档、帮助中心、开源 requirements/use cases/state models，并在看到 Oracle/bug trigger 前定义纳入规则。","inputs":"package/version/screen mapping 与开发者站点。","procedure":"逐来源保存 URL、日期、版本、原文、屏幕绑定和是否独立于 benchmark。","outputs":"clean specification registry 或可审计的未找到结论。","completion_check":"每条 spec 有自然语言/形式原文、来源、版本、前置状态、约束与预期响应。","run_status":"planned","depends_on":[]},
        {"id":"T-SPEC-PILOT","title":"准备非 RQ 的 Specification Integration Pilot","group_ids":["G-SPEC"],"kind":"experiment","necessity":"conditional","basis":"author_decision","location":{"source_id":"SRC-REVIEW","locator":"W2: ingest formal specifications or RE artifacts"},"action":"对 clean subset 展示有/无 spec 的配对示例，以及 requirement 到 generation constraint 与 scoped oracle 的映射。","inputs":"T-SPEC-SOURCES 的 clean subset。","procedure":"描述性报告覆盖、示例和限制；不并入 A-F 指标，不做独立 RQ，不把 validator 当 requirement。","outputs":"Discussion 表、adapter 示例和 reviewer response evidence。","completion_check":"每条 spec 有来源、版本、前置状态和适用行为；无 clean source 时只保留接口与 limitation。","run_status":"blocked","depends_on":["T-SPEC-SOURCES"]},
        {"id":"T-MATRIX","title":"由作者逐例审阅 152 行 A-F 物料与运行分组","group_ids":["G-CASE-MATRIX"],"kind":"verification","necessity":"required","basis":"author_decision","location":{"source_id":"SRC-REGISTRY","locator":"P001-P115, B001-B037"},"action":"使用本 HTML 的搜索、筛选和逐例批注确认 Prompt 增量、截图校准状态、UI evidence 候选、F 边界与 spec 来源。","inputs":"本页逐例大表。","procedure":"优先审查旧 UI 候选、3 个 N/A、37 个 Bug 的 App 执行缺口和所有 spec 实现候选。","outputs":"含作者批注的导出 HTML/JSON。","completion_check":"每个争议案例有 agreed/needs_revision/deferred 状态，不依赖口头记忆。","run_status":"in_progress","depends_on":[]},
    ]
    scaffold["evidence"] = [
        {"id":"EV-LEGACY-FEEDBACK","claim":"旧论文 feedback 是上一轮候选加 validator 结果/泛化错误及重生成合同，不是真实 App UI feedback","status":"verified","source_ids":["SRC-LEGACY-FEEDBACK"],"details":"旧实现显式发送 previous output/parsed JSON、error type/detail、resource IDs 和 schema；semantic detail 通常只是 Validator returned false。","needed":"旧结果作为 historical full-feedback 保留，不能重命名为新版 B/C/D/E/F。"},
        {"id":"EV-CAPTURE-GAP","claim":"旧 runner 的 final-only XML 缺口已由新版 PNG burst/录像实现并完成实机资格","status":"verified","source_ids":["SRC-ABCDEF","SRC-PREFLIGHT","SRC-PAGE-SMOKE","SRC-BUG-SMOKE","SRC-IMAGE-PROBE"],"details":"实现 pre-action、0/0.25/0.5/1/2 秒 burst、1 秒 tail、screenrecord、关键帧和 UI delta；18 个离线测试、112 Page、37 Bug 与三模型最大图探测通过。","needed":"只等待 F 分析器 API 资格；B017 的冻结 XML 来源必须在结果中保留。"},
        {"id":"EV-OLD-RESULT-BOUNDARY","claim":"旧 E2 smoke/formal 运行不属于新版 A-F","status":"verified","source_ids":["SRC-PROTOCOL"],"details":"旧协议研究 legacy vs same-information structured；新版研究 history/status/screenshot/UI delta/analyzer，estimand 不同。","needed":"新 protocol_id、新 prompt snapshots 和新 smoke。"},
        {"id":"EV-NO-CLEAN-SPEC","claim":"当前审计未找到可直接进入 pilot 的独立 requirements 覆盖集","status":"verified","source_ids":["SRC-DE","SRC-PLAY","SRC-BUG"],"details":"当前 Play listing 没有字段级 clean requirement；Bug issue/PR 与 benchmark trigger 绑定；外部开发者文档尚未穷尽。","needed":"继续版本绑定检索，或只报告接口和 limitation。"},
        {"id":"EV-ORACLE-LEAK","claim":"152 个 validator 完整，但给模型会构成答案泄漏","status":"verified","source_ids":["SRC-UPPER"],"details":"validator 可用于 outcome oracle 和审计，不是外部 requirements，也不是 F analyzer 输入。","needed":"所有 A-F prompt 和 analyzer request 做 forbidden-field 测试。"},
        {"id":"EV-APK","claim":"APK 中有 5 个 Page、11 个 Bug 的字段级实现约束候选","status":"verified","source_ids":["SRC-APK","SRC-DE"],"details":"这些是 layout/inputType/maxLength 等实现事实；其中 4 个 Page 在正式集内。","needed":"如做 E_impl，需明确它不等同于业务 requirements，并与 clean E 分层。"},
        {"id":"EV-READY","claim":"149/152 正式适用案例的非 F 门禁已通过；新版计划为 1,341 paired blocks / 8,046 trajectories","status":"verified","source_ids":["SRC-REGISTRY","SRC-BACKUP","SRC-PREFLIGHT"],"details":"112 Page + 37 Bug，3 模型，3 replicates，6 arms；P031/P082/P104 排除；formal_run_allowed=false。","needed":"等待 gpt-5.6-sol API 资格通过后，由作者显式开启 formal switch。"},
    ]
    scaffold["sections"] = [
        {"id":"protocol-judgment","title":"已确认的新 A-F 反馈定义","task_ids":["T-PROMPTS","T-CAPTURE-CALIBRATION","T-F-ANALYZER"],"body":"A 只增加独立采样机会；B 增加历史候选；C 再增加 PASS/FAIL/UNKNOWN；D 再增加执行前后时序截图；E 再增加从相同证据窗口确定性提取的 UI 变化和公开错误；F 再增加固定 GPT-5.6-Sol medium 的证据分析。\n\n这不是严格格式消融。每一层的 Prompt、证据来源和禁止字段都需要冻结。Hash、selector、validator 和 trigger 不进入模型语义。"},
        {"id":"capture-plan","title":"D/E 的先行时序校准与正式捕获","task_ids":["T-CAPTURE-CALIBRATION","T-CAPTURE-RUNNER","T-UI-EXTRACTOR"],"body":"不能只在点击后立即截一张，也不能统一等待几秒。每例先以控制输入测量 Toast/Dialog/Snackbar、页面跳转、网络返回和稳定时间，再冻结观察窗口。正式执行从动作前开始短时录像，并在 t≈0、0.25、0.5、1、2 秒及后续尾窗保存时间戳证据；给 D 的关键帧由预注册规则确定。E 只描述这些帧/XML 可支持的事实。"},
        {"id":"matrix-guide","title":"152 行大表阅读方法","task_ids":["T-MATRIX"],"body":"每行先显示旧执行基础与新版 A-F 阻塞，再显示 base prompt、A-F 增量、Specification Pilot 来源状态、Oracle 和完整物料。旧 D 审计消息被保留为新版 E 校准候选，但不代表新版 E ready。展开行可查看完整 Prompt 合同、捕获计划、分析器边界和逐例来源。"},
        {"id":"run-plan","title":"新版实验前置顺序","task_ids":["T-PROMPTS","T-CAPTURE-CALIBRATION","T-CAPTURE-RUNNER","T-UI-EXTRACTOR","T-F-ANALYZER","T-SPEC-PILOT"],"body":"1. 实现并测试 A-F Prompt；2. 用控制候选完成逐例截图时序校准；3. 实现 D evidence bundle；4. 实现 E 确定性 UI delta；5. 实现 F analyzer 与泄漏门禁；6. 运行不计入正式结果的新版 Page/Bug smoke；7. 门禁通过后再冻结正式 manifest。\n\nSpecification Integration Pilot 与 A-F 分开，只服务 Discussion 和 reviewer response。"},
    ]
    scaffold["decisions"] = [
        {"id":"DEC-HASH","topic":"hash 的角色","text":"hash 只留在审计、版本和漂移核验中；不是 specification，不应成为模型可见语义。","origin":"作者质疑 + 代码核对","status":"confirmed","updated_at":"2026-09-24","scope":"全部条件和 152 个案例","supersedes":[]},
        {"id":"DEC-ABCDEF","topic":"主反馈协议","text":"采用 A 独立重试、B 历史、C 状态、D 时序截图、E UI 变化摘要、F 外部分析器诊断。废止 legacy-vs-structured 作为主因素。","origin":"作者与 Codex 讨论确认","status":"confirmed","updated_at":"2026-09-24","scope":"新版反馈实验","supersedes":["E2-structure-v1 arm semantics"]},
        {"id":"DEC-CAPTURE","topic":"D/E 捕获","text":"先逐例校准，再冻结 burst/录像窗口；不能使用单张立即截图或结果后调参。","origin":"作者问题 + runner 核对","status":"confirmed","updated_at":"2026-09-24","scope":"所有具有 App 执行路径的案例","supersedes":[]},
        {"id":"DEC-F","topic":"F 分析器","text":"固定外部 gpt-5.6-sol、reasoning_effort=medium、strict JSON；只读 E 证据，不给建议，失败泄漏门禁则 analysis_invalid。","origin":"作者决定","status":"confirmed","updated_at":"2026-09-24","scope":"F 条件","supersedes":[]},
        {"id":"DEC-SPEC","topic":"Specification 处理","text":"不作为 A-F feedback、不新增 RQ；仅做小规模 Integration Pilot，在 Discussion 回答审稿人。","origin":"作者决定 + Reviewer 2 原文","status":"confirmed","updated_at":"2026-09-24","scope":"Discussion / reviewer response","supersedes":["E as a feedback arm"]},
    ]
    scaffold["discussion"]["entries"] = {
        "section:overview": {"status":"agreed","note":"A-F 定义与 Specification Discussion Pilot 已确认；非 F 的代码、实机 smoke 和三模型图片资格已完成。","messages":[{"id":"msg-overview-20260925-v6","parent_id":None,"role":"assistant","author":"Codex","text":"E2-abcdef-v1 的 112 Page、37 Bug App 观察与三模型最大真实反馈图资格均已通过。预检只剩 gpt-5.6-sol API 资格与 formal switch；按作者要求暂不重试 5.6-sol，也未启动正式实验。","created_at":"2026-09-25"}]}
    }
    scaffold["protocol_arms"] = {
        "A": {"name":"独立重试","first_turn":"共享同一初始任务与 UI context","later_turns":"在无历史的新会话重新发送 base prompt；不说明失败","factor":"仅增加采样机会"},
        "B": {"name":"候选历史","first_turn":"与其他臂共享首轮候选","later_turns":"A + 最近 5 个候选；无 status/原因/UI 证据","factor":"candidate history"},
        "C": {"name":"执行状态","first_turn":"与其他臂共享首轮候选","later_turns":"B + PASS/FAIL/UNKNOWN；无原因","factor":"binary/ternary execution outcome"},
        "D": {"name":"时序截图","first_turn":"与其他臂共享首轮候选","later_turns":"C + before/first-change/transient-peak/settled frames","factor":"raw visual execution evidence"},
        "E": {"name":"可观察 UI 变化","first_turn":"与其他臂共享首轮候选","later_turns":"D + navigation、visible-text delta、公开错误、Toast/Dialog/Snackbar、field-state changes","factor":"deterministic observable UI summary"},
        "F": {"name":"外部诊断分析","first_turn":"与其他臂共享首轮候选","later_turns":"E + gpt-5.6-sol medium 的证据约束原因分析；recommendation=null","factor":"analysis-augmented feedback；额外模型调用"},
    }
    scaffold["specification_pilot"] = {
        "name": "Specification Integration Pilot",
        "role": "仅用于 Discussion 和 reviewer response，不是 A-F arm，不新增 RQ",
        "treatment": "对有独立、版本绑定 requirements/RE artifact 的案例，从首轮展示 specification 或用其构造 scoped oracle",
        "comparison": "少量 paired with/without-spec demonstration；描述性报告，不并入 A-F 主指标",
        "prohibited_sources": ["validator rewrite", "bug trigger", "accepted answer", "hash", "current UI extraction without requirement provenance"],
        "current_status": "clean subset 尚待继续审计",
    }
    scaffold["case_matrix"] = {
        "summary": {
            **registry["counts"],
            "d_confirmed_page": de_all["summary"]["page_d_confirmed"],
            "clean_external_spec": 0,
            "bug_upper_bound_e": de_all["summary"]["bug_upper_bound_e"],
            "private_oracle_upper_bound_e": de_all["summary"]["private_oracle_upper_bound_e"],
        },
        "cases": cases,
    }
    scaffold["implementation_preflight"] = abcdef_preflight
    return scaffold


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--scaffold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.repo.resolve(), args.scaffold.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "cases": len(report["case_matrix"]["cases"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
