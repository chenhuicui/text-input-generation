# RQ2：三种表示 × A/B 历史结果与 C/D 新实验

这是本轮讨论后的入口：`text_input_eval.supplementary.session_cli`。
旧 `text_input_eval.supplementary` 入口仍是先前的 E1/E2 草案，包含不同的反馈分组、共享首轮和新会话策略，**不能拿它运行本轮 C/D**。本轮没有修改原论文结果、旧反馈服务或旧实验入口。

## 实现范围

| 组 | 数据或代码来源 | 会话与反馈 |
|---|---|---|
| A | 导入已有 RQ1 case-level CSV | 历史独立调用；不是新生成的 session 轨迹 |
| B | 导入已有 RQ2 case-level CSV；复用 `services.feedback_based.FeedbackBuilder` 作模板依据 | 历史原反馈；本入口不续接旧 B 的 session |
| C | 新实现 | 独立首轮，后续将返回的 `data.id` 作为下一轮请求的 `id`；结构化展示 B 已包含的信息和要求 |
| D | 新实现 | 与 C 相同，加当前执行中有来源的 UI 提示；无可用提示时发送与 C 完全相同的文本 |

默认配置 `configs/supplementary/rq2_cd_session.json` 保留历史 9 个模型 ID，覆盖 115 个页面和 37 个 bug、`extracted/full`、`xml/xml`、`vision/vision`。只有 C、D 生成新输入。模型 ID 是历史队列，不代表已验证当前服务版本或容量。

每个 case × model × representation × arm × replicate 独立建立响应 ID 链。首轮请求的 `id` 为空；此后将上一轮 `data.id` 原样放入下一轮请求的 `id`。`data.sessionId` 只作为审计元数据保存，不参与续接。Vision 首轮附原缓存图片，后续只发送反馈和游标。网关必须通过 ID 链保留图片和历史；本地代码不能证明服务端实际保留了它们。`gateway_settings.id_chain_retention` 记录后续核实的依据。

没有 ID 链 fork API，因此 C/D 不共享首轮候选，也不复用旧 B 的会话。可以按同一案例比较结果；首轮失败后的挽救率以每组自己的失败集合为条件，不能宣称是共同失败起点上的因果比较。

默认页面总预算 3 次、bug 总预算 30 次，均包含首轮；`replicates: 1` 表示每个组合一条新轨迹。预算 3 次不是 3 次独立重复。全量 C/D 为 8,208 条轨迹、最多 78,570 次模型调用，成功或拒答会提前停止。这是配置上限，没有执行这些调用。

## C 的信息等价范围

`session_feedback.py` 从原 `FeedbackBuilder` 提取任务目标、错误概述、再生成要求、字段 ID 和 JSON schema。它保留最近 5 轮候选、检查结果、错误类别与详情，并使用原历史详情的 500 字符截断规则。最新错误详情仍按原模板单独保留，不额外编造修正规则。

每个实际反馈请求保存 `feedback_audit`，包括同一观测对应的 B 参考提示、C 的共享信息对象及哈希。C 对这些相同事实重新组织为候选—检查—已知原因—下一轮要求。

这里的等价是“给定同一失败记录，B 模板与 C 的内容等价”，**不是历史 B 轨迹已经与新 C 完全可比**。当前公共输出检查还要求字段值为文本，并单独处理基础设施故障；旧 B 的字段检查和异常处理需要用原逐轮记录核对。`abcd_inventory.csv` 将历史来源和新实验口径分别标出，不自动计算 C−B 的机制增益。

最近 5 轮是显式反馈窗口；ID 链中更早的模型回复和提示仍可能被网关保留。代码不把这个窗口误写成服务端只保留 5 轮。

## D 的证据与 oracle

默认 C/D 都使用 `endpoint: behavior` 和同一执行 oracle。原 validator 结果另存为 `rule_verdict`，不会代替缺失的真实执行结果。JSON 无法解析、字段不匹配或值不是文本时，不启动 app，记录为输出检查失败；规则、行为和任务结论分别存储。

真实执行复用已有 `Evaluator` 的命令适配器协议。每轮要求恢复约定初态，执行输入，再返回绑定到 `request_id`、候选哈希和 oracle 版本的判定及文件证据。执行失败或证据缺失记为 incomplete，不当作模型生成的任务失败反馈。

D 当前支持从保存的 XML 中读取明确指定控件上的实际提示，不调用额外模型生成诊断。oracle 可声明：

```json
{
  "public_diagnostic_targets": [
    {"id": "password_error_message", "selector": {"resource-id": "your.package:id/password_error"}}
  ]
}
```

这是字段格式示例，不是任何现有 AUT 的真实 ID。必须用对应应用实际观察到的控件声明。声明不包含私有 bug 触发字符串。

可用诊断必须同时满足：本轮失败执行、候选与初态回执匹配、声明过的控件、证据路径位于本轮目录、XML 文件哈希匹配，以及提示文本确实出现在该控件上。过期轨迹、任意诊断文本、私有 validator 规则或仅标记 `public_spec_check` 的内容均不会进入 D。没有额外信息时完全回退为 C，并单独记录覆盖情况。

证据匹配只能证明“该文字确实被观察到”，不能证明它就是根因。报告中的 `root_cause_diagnosis_accuracy` 保持未知，不能用成功率提升替代诊断正确性审查。目前没有实现规格解析或从文字自动推导新的业务谓词。

内置 `android_oracle` 只支持单 APK、UI 断言的目标。其 APK、reset、导航、输入绑定、提交动作和断言字段见 [原适配器说明](README.md)。需要崩溃签名、日志或其他症状的 bug 使用独立 `oracle_command` 实现相同证据协议；不能把任意崩溃视为该已知 bug 的复现。37 个案例的真实执行定义仍需依据已有材料填写，代码没有虚构逐例定义。

`endpoint: rule` 可用于明确标为规则口径的 C 实验。D 必须使用 behavior。两种 endpoint 不能在同一实验中按案例临时切换，也不能将规则通过率改名为实际页面通过率。

## 本地准备和历史导入

在仓库根目录执行以下命令只读取本地缓存和导出，不调用模型、MySQL 或 app：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major python -m text_input_eval.supplementary.session_cli plan \
  --config configs/supplementary/rq2_cd_session.json \
  --output output/rq2_cd_session_plan

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major python -m text_input_eval.supplementary.session_cli import-ab \
  --config configs/supplementary/rq2_cd_session.json \
  --output output/rq2_cd_session_plan
```

`plan` 生成配置、代码、缓存提示/图片、oracle 依赖和历史文件的冻结清单及运行前缺项；`import-ab` 另外生成 `historical_ab.jsonl`，保留原 CSV 行、行号和来源哈希。历史文件 A/B 各有 4,104 个 case × model × representation 单元；有记录不等于逐轮证据完整。

case-level CSV 没有逐轮请求载荷、初态/执行轨迹和基础设施状态；不能据此自动确认与新 C/D 的 oracle、模型版本、停止条件和会话历史完全一致。后续核对要使用旧库中已有逐轮候选、session、请求/反馈、原判定及执行证据。导入流程不要求全部重跑 A/B，也不假定这些材料已经缺失。

## 将来实际执行

本轮没有运行实验。实际执行前，应另存配置，填好逐例 oracle 和冻结文件，并记录每个模型的 `gateway_settings`：`model_revision`、`generation_parameters`、`context_limit_source`、`id_chain_retention`；核实后再设置 `gateway_verified: true`。这些是复现元数据，不会自动改写网关的生成参数。新 API 发送 `modelType`、`id`、`message` 和首轮可选 `imageUrl`。

只有显式 `run` 子命令会调用模型/执行器；凭据从原 `MUST_LAB_API_TOKEN` 环境变量读取。未填写运行元数据时入口在调用前停止。`--max-new-calls N` 可限制本次新增调用数，之后使用同一配置和目录恢复。

恢复使用 SQLite 中保存的响应和 ID 链，不重发已完成调用。尚未确认收到的 API 请求或尚未确认完成的 app 执行会阻止自动恢复；返回了完整 API 响应但尚未执行的记录可以继续评价。游标失效、缺少响应 ID 或跨轨迹游标冲突均不会静默重建上下文。冲突会停止整个调度，需要检查受影响轨迹。

本地容量检查计算完整可见会话文本，包括之前的提示、反馈与模型回复，同时限制请求字节数。可选本地 tokenizer 仍不包含网关隐藏模板和图片 token。因此它是提前拦截，并不保证满足模型实际 token 上限。Mistral 等模型的上下文超限与 API 错误单独记为 incomplete/capacity_blocked，不能据此认定模型生成能力低；也不会自动删图或截断历史后混入同一组。

## 输出与离线测试

对真正的 C/D 结果目录使用 `report --output <目录>`，无需模型、MySQL 或 app：

| 文件 | 内容 |
|---|---|
| `results.sqlite3` | 请求载荷、响应 ID 链、token usage、评价、反馈信息审计及轨迹 |
| `evidence/<request_id>/` | app 执行适配器保存的截图、XML、日志和观测 |
| `requests.jsonl` / `trajectories.jsonl` | 可逐轮复核的导出 |
| `cd_summary.csv` | 成功率、各预算成功率、组内挽救率、成功所需调用数、时间/token、诊断覆盖率、未完成状态 |
| `abcd_inventory.csv` | 历史 A/B 与新 C/D 的结果清单，保留来源及不同指标字段 |
| `run_summary.json` | 已调用数量、未完成/不确定状态及指标边界 |

成功率分别给出完成轨迹分母及“已观察成功数/计划轨迹数”，不把未运行或基础设施失败偷偷丢进能力失败。诊断覆盖率分母是实际发出的、此前有明确失败执行的 D 反馈；最终轮失败但不再发送反馈不进入该分母。金额和未返回的 token 保留未知，不能当作零成本。

本轮测试使用假网关和假执行器；新测试还禁止网络连接和 app 子进程：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major python -m unittest discover \
  -s tosem_major/text_input_eval/tests -v
```

离线测试覆盖 ID 链隔离/续接、token usage 规范化、Vision 首轮图片、B/C 信息映射、D 证据核对和回退、异常分类、暂停恢复，以及本地全量队列与历史覆盖。它不代表真实网关或 AUT 已验证。
