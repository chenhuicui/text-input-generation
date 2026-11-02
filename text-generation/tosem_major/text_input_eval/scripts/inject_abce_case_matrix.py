#!/usr/bin/env python3
"""Inject the 152-case review matrix into a paper-review-workbench HTML."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


CSS = r"""
/* A/B/C/D/E/F case matrix extension */
.matrix-shell{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}
.matrix-judgment{display:grid;grid-template-columns:repeat(3,minmax(220px,1fr));gap:12px;margin:12px 0}
.matrix-judgment>div{border-radius:9px;padding:13px;background:#f0f4ed;border-left:4px solid var(--green)}
.matrix-judgment .caution{background:#faf0dd;border-left-color:#b88936}.matrix-judgment .stop{background:#f9e9e5;border-left-color:#a44d43}
.matrix-arm-table,.case-table{border-collapse:separate;border-spacing:0;width:100%}.matrix-arm-table th,.matrix-arm-table td,.case-table th,.case-table td{border-right:1px solid var(--line);border-bottom:1px solid var(--line);padding:9px;vertical-align:top;text-align:left}
.matrix-arm-table th,.case-table th{background:#e7eee7;color:#244b39;font-size:12px}.matrix-arm-table tr:first-child th,.case-table thead tr:first-child th{border-top:1px solid var(--line)}
.matrix-arm-table th:first-child,.matrix-arm-table td:first-child,.case-table th:first-child,.case-table td:first-child{border-left:1px solid var(--line)}
.matrix-arm-table th:first-child,.case-table th:first-child{border-top-left-radius:7px}.matrix-arm-table th:last-child,.case-table th:last-child{border-top-right-radius:7px}
.matrix-scroll{overflow:auto;max-height:72vh;border:1px solid var(--line);border-radius:9px;background:white}.case-table{min-width:2860px;font-size:12px}.case-table thead{position:sticky;top:0;z-index:4}.case-table th{position:sticky;top:0;z-index:4}.case-table th:first-child,.case-table td:first-child{position:sticky;left:0;z-index:2;background:#fffdf8;min-width:82px}.case-table th:first-child{z-index:6;background:#dce9df}.case-table th:nth-child(2),.case-table td:nth-child(2){position:sticky;left:82px;z-index:2;background:#fffdf8;min-width:185px}.case-table th:nth-child(2){z-index:6;background:#dce9df}.case-table tr:hover td{background:#f7f3e9}.case-table tr:hover td:first-child,.case-table tr:hover td:nth-child(2){background:#eef4ed}
.case-table td:nth-child(3){min-width:180px}.case-table td:nth-child(4){min-width:175px}.case-table td:nth-child(5){min-width:195px}.case-table td:nth-child(6){min-width:190px}.case-table td:nth-child(7){min-width:250px}.case-table td:nth-child(8){min-width:280px}.case-table td:nth-child(9){min-width:270px}.case-table td:nth-child(10){min-width:310px}.case-table td:nth-child(11){min-width:240px}.case-table td:nth-child(12){min-width:120px}
.case-detail-row td{position:static!important;background:#f8f5ed!important;padding:16px!important}.case-detail{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.case-detail .wide{grid-column:1/-1}.case-detail-card{border:1px solid var(--line);border-radius:9px;background:#fff;padding:13px;min-width:0}.case-detail-card h4{margin:0 0 8px;color:var(--green)}.case-detail-card pre{max-height:420px}.matrix-filters{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;position:sticky;top:0;z-index:7;background:var(--card);padding:8px 0}.matrix-filters input{flex:1;min-width:230px}.matrix-count{font-weight:650;color:var(--green);padding:8px}.matrix-badge{display:inline-block;border-radius:20px;padding:2px 8px;margin:1px 4px 3px 0;background:#e5eee7;color:#335f47;font-size:10px}.matrix-badge.warn{background:#faecd0;color:#815e18}.matrix-badge.stop{background:#f7e3df;color:#8c3b35}.matrix-badge.info{background:#e5edf4;color:#345e78}.matrix-note{color:var(--muted);font-size:11px}.matrix-actions{display:flex;gap:6px;flex-wrap:wrap}.matrix-actions button{font-size:11px;padding:5px 8px}.matrix-empty{text-align:center;padding:26px!important;color:var(--muted)}
@media(max-width:900px){.matrix-judgment,.case-detail{grid-template-columns:1fr}.case-detail .wide{grid-column:auto}.matrix-scroll{max-height:none}.case-table th:first-child,.case-table td:first-child,.case-table th:nth-child(2),.case-table td:nth-child(2){position:static}}
@media print{.matrix-filters{display:none}.matrix-scroll{max-height:none;overflow:visible}.case-table{min-width:0;font-size:8px}.case-table th:first-child,.case-table td:first-child,.case-table th:nth-child(2),.case-table td:nth-child(2){position:static}.case-detail{display:block}.case-detail-card{break-inside:avoid;margin:8px 0}}
"""


JS = r"""
<script>
'use strict';
(() => {
  const M=D.case_matrix||{summary:{},cases:[]}, arms=D.protocol_arms||{}, spec=D.specification_pilot||{};
  for(const c of M.cases){known.add('case:'+c.case_id);labels['case:'+c.case_id]=c.case_id+' '+c.app;}
  const baseRenderMain=renderMain;
  function j(v){return esc(JSON.stringify(v??null,null,2));}
  function badge(text,cls=''){return `<span class="matrix-badge ${cls}">${esc(text)}</span>`;}
  function armOverview(){
    const notes={A:'仅重试机会',B:'只增加候选历史',C:'只增加状态',D:'增加原始视觉证据；需逐例校准',E:'增加确定性 UI 摘要',F:'固定外部分析器；额外调用和成本'};
    return `<table class="matrix-arm-table"><thead><tr><th>条件</th><th>首轮</th><th>后续轮次</th><th>要识别的因素</th><th>本工作台判断</th></tr></thead><tbody>${Object.entries(arms).map(([id,a])=>`<tr><td><b>${esc(id)} · ${esc(a.name)}</b></td><td>${esc(a.first_turn)}</td><td>${esc(a.later_turns)}</td><td>${esc(a.factor)}</td><td>${esc(notes[id]||'')}</td></tr>`).join('')}</tbody></table>`;
  }
  function short(text,n=120){const s=String(text??'');return s.length>n?s.slice(0,n)+'…':s;}
  function specClass(c){if((c.specification_pilot.implementation_contracts||[]).length)return 'impl';if(c.task==='invalid_bug')return 'upper';return 'none';}
  function evidenceClass(c){return (c.E.legacy_evidence_candidate.messages||[]).length?'candidate':'none';}
  function row(c,i){
    const formal=c.formal.in_scope?badge('旧执行基础适用'):badge('N/A','stop');
    const legacyReady=c.formal.legacy_protocol_formal_run_ready?badge('legacy ready','info'):badge('legacy not ready','warn');
    const newReady=c.formal.abcdef_formal_run_ready?badge('A-F ready'):badge('A-F blocked','stop');
    const legacyUi=c.E.legacy_evidence_candidate,uiMessages=legacyUi.messages||[],s=c.specification_pilot,impl=(s.implementation_contracts||[]).length;
    return `<tr class="case-summary-row" data-case-index="${i}" data-task="${esc(c.task_label)}" data-scope="${c.formal.in_scope?'formal':'excluded'}" data-evidence="${evidenceClass(c)}" data-spec="${specClass(c)}" data-matrix-search="${esc((c.case_id+' '+c.app+' '+c.package_or_source_target+' '+c.required_resource_ids.join(' ')+' '+legacyUi.status+' '+s.recommended_role).toLowerCase())}">
      <td><b>${esc(c.case_id)}</b><div class="matrix-note">${esc(c.task_label)} #${c.ordinal}</div></td>
      <td><b>${esc(c.app)}</b><div class="matrix-note">${esc(c.package_or_source_target)}</div><div>${c.required_resource_ids.map(x=>badge(x,'info')).join('')}</div></td>
      <td>${formal}${legacyReady}${newReady}<div class="matrix-note">${esc(c.formal.abcdef_blocker||c.formal.reason_code||'')}</div></td>
      <td><b>独立 base retry</b><div>${esc(short(c.base_material.global,130))}</div><div class="matrix-note">新会话；不说上一轮失败</div></td>
      <td><b>history only</b><div>最近 5 个候选；无 status、原因或 UI 证据</div></td>
      <td><b>PASS / FAIL / UNKNOWN</b><div>只增加执行状态，不给原因</div></td>
      <td>${c.formal.in_scope?badge('smoke passed','info'):badge('N/A','stop')}<div>before + first-change + transient-peak + settled</div><div class="matrix-note">112 Page + 37 Bug 已完成非正式实机 smoke</div></td>
      <td>${uiMessages.length?badge('旧 UI 候选 '+uiMessages.length,'info'):badge('需新捕获','warn')}<div>${esc(uiMessages.join('；')||'从 D evidence bundle 提取 navigation、visible-text delta 和公开消息')}</div><div class="matrix-note">${esc(legacyUi.note)}</div></td>
      <td>${badge('gpt-5.6-sol','info')}${badge('medium','info')}<div>证据原因分析；不给建议</div><div class="matrix-note">额外调用、Token、延迟和成本单列</div></td>
      <td>${badge('clean source: '+s.clean_requirement_status,s.clean_requirement_status==='未建立'?'stop':'')} ${impl?badge('impl candidate '+impl,'warn'):''} ${c.task==='invalid_bug'?badge('benchmark-linked','warn'):''}<div>${esc(s.independent_requirement_source)}</div><div class="matrix-note">Discussion Pilot；不是 A-F/RQ</div></td>
      <td><b>${esc(c.oracle.kind||'none')}</b><div>${esc(c.oracle.version||'')}</div><div class="matrix-note">${esc(c.run_group)}</div></td>
      <td><div class="matrix-actions"><button type="button" data-expand-case="${i}">展开全部物料</button><button type="button" data-open-comment="${i}">批注</button></div></td>
    </tr><tr class="case-detail-row" data-detail-for="${i}" hidden><td colspan="12"><div class="case-detail-host"></div></td></tr>`;
  }
  function detail(c){
    return `<div class="case-detail">
      <div class="case-detail-card wide"><h4>${esc(c.case_id)} · 完整 base prompt（所有 A-F 共享首轮任务材料）</h4><pre>${esc(c.base_material.assembled_prompt)}</pre></div>
      <div class="case-detail-card"><h4>A · 独立重试</h4><pre>${j(c.A)}</pre></div>
      <div class="case-detail-card"><h4>B · 候选历史</h4><pre>${j(c.B)}</pre></div>
      <div class="case-detail-card"><h4>C · 执行状态</h4><pre>${j(c.C)}</pre></div>
      <div class="case-detail-card"><h4>D · 状态 + 时序截图</h4><pre>${j(c.D)}</pre></div>
      <div class="case-detail-card"><h4>E · 可观察 UI 变化</h4><pre>${j(c.E)}</pre></div>
      <div class="case-detail-card"><h4>F · 固定外部分析器</h4><pre>${j(c.F)}</pre></div>
      <div class="case-detail-card wide"><h4>Specification Integration Pilot 来源审计（不属于 A-F）</h4><pre>${j(c.specification_pilot)}</pre></div>
      <div class="case-detail-card"><h4>Oracle（不允许给模型或 F analyzer）</h4><p><b>${esc(c.oracle.rule_source)}</b></p><pre>${esc(c.oracle.validate_method)}</pre><details><summary>公共 helper</summary><pre>${j(c.oracle.helper_sources)}</pre></details></div>
      <div class="case-detail-card"><h4>APK / Play / Bug issue 物料</h4><pre>${j(c.implementation_evidence)}</pre></div>
      <div class="case-detail-card"><h4>物料路径</h4><pre>${j(c.material_paths)}</pre></div>
      <div class="case-detail-card"><h4>审计哈希（不发送给模型，不是 specification）</h4><pre>${j(c.audit_only)}</pre></div>
      <div class="case-detail-card wide"><h4>逐例批注</h4>${annotation('case:'+c.case_id)}</div>
    </div>`;
  }
  function matrixSection(){
    return `<section class="section" id="sec-case-matrix"><div class="kicker">CASE-BY-CASE PROTOCOL AUDIT</div><h2>152 例 A/B/C/D/E/F 大表</h2>
      <div class="matrix-judgment"><div><b>信息量，而不是格式</b><p>B/C 不再比较自然语言与 JSON；A-F 逐层增加 history、status、raw UI、UI delta 和 analysis。</p></div><div class="caution"><b>D/E smoke 已完成</b><p>112 Page 与 37 Bug 均通过；B017 使用明确标注的冻结 XML 加实时像素证据。</p></div><div class="stop"><b>F 与 specification 的边界</b><p>F 是固定外部分析器模块且仍待 API 资格；specification 另做 Discussion Pilot，不属于 A-F 或新 RQ。</p></div></div>
      <div class="matrix-shell"><h3>六种反馈条件改变什么</h3>${armOverview()}</div>
      <div class="matrix-shell"><h3>Specification Integration Pilot（独立于 A-F）</h3><pre>${j(spec)}</pre></div>
      <div class="matrix-shell"><div>${badge('152 cases')} ${badge('149 in-scope')} ${badge('Page smoke 112/112','info')} ${badge('Bug smoke 37/37','info')} ${badge('image gateway 3/3','info')} ${badge('formal held: F API + switch','stop')} ${badge('1,341 paired blocks','info')} ${badge('8,046 trajectories','info')} ${badge('F: gpt-5.6-sol medium','info')}</div>
      <div class="matrix-filters"><input id="matrix-search" type="search" placeholder="搜索 case、App、package、控件、UI/spec 状态"><select id="matrix-task"><option value="">Page + Bug</option><option value="Page">Page</option><option value="Bug">Bug</option></select><select id="matrix-scope"><option value="">全部旧适用性</option><option value="formal">旧协议正式适用</option><option value="excluded">N/A</option></select><select id="matrix-evidence"><option value="">全部旧 UI 候选</option><option value="candidate">存在候选消息</option><option value="none">需新捕获</option></select><select id="matrix-spec"><option value="">全部 spec 来源</option><option value="impl">实现约束候选</option><option value="upper">benchmark-linked</option><option value="none">无 clean source</option></select><span class="matrix-count" id="matrix-count"></span></div>
      <div class="matrix-scroll"><table class="case-table"><thead><tr><th>ID</th><th>App / 输入控件</th><th>就绪状态</th><th>A</th><th>B</th><th>C</th><th>D</th><th>E</th><th>F</th><th>Specification Pilot</th><th>Oracle / 运行分组</th><th>完整物料</th></tr></thead><tbody id="matrix-body">${M.cases.map(row).join('')}</tbody></table></div></div>
      ${annotation('section:case-matrix')}</section>`;
  }
  function applyMatrixFilter(){
    const q=($('#matrix-search')?.value||'').toLowerCase(),task=$('#matrix-task')?.value||'',scope=$('#matrix-scope')?.value||'',evidence=$('#matrix-evidence')?.value||'',spec=$('#matrix-spec')?.value||'';
    let shown=0;
    for(const tr of $$('.case-summary-row')){const yes=(!q||tr.dataset.matrixSearch.includes(q))&&(!task||tr.dataset.task===task)&&(!scope||tr.dataset.scope===scope)&&(!evidence||tr.dataset.evidence===evidence)&&(!spec||tr.dataset.spec===spec);tr.hidden=!yes;const detail=$(`[data-detail-for="${tr.dataset.caseIndex}"]`);if(detail&&!yes)detail.hidden=true;if(yes)shown++;}
    const count=$('#matrix-count');if(count)count.textContent=`显示 ${shown} / ${M.cases.length}`;
  }
  function insertMatrix(){
    if($('#sec-case-matrix'))return;
    const source=$('#sec-sources');if(source)source.insertAdjacentHTML('beforebegin',matrixSection());else $('#main').insertAdjacentHTML('beforeend',matrixSection());
    if(!$('#nav a[href="#sec-case-matrix"]')){const link=document.createElement('a');link.href='#sec-case-matrix';link.textContent='152 例 A/B/C/D/E/F 大表';const sourceLink=$('#nav a[href="#sec-sources"]');if(sourceLink)sourceLink.before(link);else $('#nav').append(link);}
    for(const id of ['matrix-search','matrix-task','matrix-scope','matrix-evidence','matrix-spec'])$('#'+id)?.addEventListener('input',applyMatrixFilter);
    applyMatrixFilter();
  }
  renderMain=function(){baseRenderMain();insertMatrix();};
  renderMain();
  document.addEventListener('click',ev=>{
    const expand=ev.target.closest('[data-expand-case]');
    if(expand){const i=Number(expand.dataset.expandCase),tr=$(`[data-detail-for="${i}"]`),host=$('.case-detail-host',tr);if(!host.dataset.loaded){host.innerHTML=detail(M.cases[i]);host.dataset.loaded='1';}tr.hidden=!tr.hidden;expand.textContent=tr.hidden?'展开全部物料':'收起完整物料';return;}
    const open=ev.target.closest('[data-open-comment]');
    if(open){const i=Number(open.dataset.openComment),tr=$(`[data-detail-for="${i}"]`),host=$('.case-detail-host',tr);if(!host.dataset.loaded){host.innerHTML=detail(M.cases[i]);host.dataset.loaded='1';}tr.hidden=false;const box=$('[data-box-target="case:'+M.cases[i].case_id+'"]',tr);box?.scrollIntoView({behavior:'smooth',block:'center'});$('textarea',box)?.focus();}
  });
})();
</script>
"""


def inject(source: Path, output: Path, report_path: Path | None = None) -> None:
    text = source.read_text(encoding="utf-8")
    if "case matrix extension */" in text:
        raise ValueError("source HTML already contains the case matrix extension")
    if text.count("</style>") != 1 or text.count("</body>") != 1:
        raise ValueError("unexpected workbench HTML structure")
    if report_path is not None:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        payload = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        pattern = r'(<script id="report-data" type="application/json">).*?(</script>)'
        text, count = re.subn(
            pattern,
            lambda match: match.group(1) + payload + match.group(2),
            text,
            count=1,
            flags=re.S,
        )
        if count != 1:
            raise ValueError("source HTML does not contain exactly one report-data block")
        text = re.sub(
            r"<title>.*?</title>",
            "<title>115 个 Page 与 37 个 Bug：A/B/C/D/E/F 反馈协议逐例审阅工作台 · Revision discussion</title>",
            text,
            count=1,
            flags=re.S,
        )
    text = text.replace("</style>", CSS + "\n</style>")
    text = text.replace("</body>", JS + "\n</body>")
    output.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inject(
        args.source.resolve(),
        args.output.resolve(),
        args.report.resolve() if args.report else None,
    )
    print(args.output.resolve())


if __name__ == "__main__":
    main()
