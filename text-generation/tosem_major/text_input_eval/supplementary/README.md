# 补充实验运行器

本轮最新的三种表示 × A/B 历史数据与 C/D 新实验使用独立的 [session C/D 实现](SESSION_CD.md) 和 `session_cli` 入口。下文保留的是先前 E1/E2 草案，其共享首轮、新会话策略和分组不适用于本轮方案。

本目录实现 E1 上下文扰动、E2 四组反馈，以及 E0 既有候选回放。复用 `services/one_shot.py` 的 `PromptContext`、`PromptGenerator`、JSON parser、`LLMClient` 和现有 152 个 validator。实际设备适配器复用 `src` 中的 `AppLauncher` 与 `ActionExecutor`。

默认配置面向原 115 页、37 bug，三个模型、三个独立重复。代码本地验证不等于已经产生补充实验结果。E3 的规格验证／黑盒探测案例和预算尚未冻结；这里提供公开规格与诊断接入契约，不声称已完成 E3 实验。E4 仍需要逐例整理真实证据，不能由 runner 推断根因。

## 先生成全量运行清单（不联网、不写原数据库）

在仓库根目录执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major python -m text_input_eval.supplementary plan \
  --config configs/supplementary/e1_e2.json \
  --output output/supplementary_plan_v1
```

输出 `manifest.json`（冻结配置、完整输入、代码哈希、案例和条件）、`plan_summary.json`（覆盖、排除、调用上限）、`oracle_template.json`（逐例证据待补清单）。相对配置路径以配置文件所在目录为基准。`case_ids: []` 表示全量；设置案例名单时保留可用全集，不能按新增结果筛样本。

同一输出目录只允许相同配置、输入和实现。协议或源码修改后使用新目录；不覆盖旧结果。

## 已实现的实验控制

| 实验 | 条件与次数 | 输出和特殊处理 |
|---|---|---|
| E1 | 页面 × extracted/XML × original/id_rename/irrelevant_nodes × 模型 × 3 独立单次生成 | 对照 XML 使用相同序列化；保存提示哈希、正反 ID 映射和结构排除原因 |
| E2 | 页面／bug × extracted × 模型 × 3 重复；每例每模型每重复共享首轮，失败再分 4 组 | 页面总预算 3，bug 30，包含首轮；每次调用新会话；成功即停 |
| E0 | 既有候选 JSONL；没有新模型生成 | 逐候选分别记录当前规则结果与实际运行结果，以及证据和差异矩阵 |

E2 四组：`retry` 只给基础提示；`history` 加候选；`binary` 再加显式目标结果；`typed` 再加有来源的诊断。B/C/D 使用同一最近 5 条历史规则，单条候选最多 8000 字符、诊断字段最多 1000 字符；完整未截断内容始终保存在请求记录。截断参数可配置，正式运行前固定。四组执行顺序以固定调度种子打乱；这个种子只控制顺序，不是 provider 的生成 seed。

当前接口请求字段为 `modelType/id/message`：首轮 `id` 为空，需要续接时把上一轮响应的 `data.id` 放入下一轮 `id`。响应中的 `data.sessionId` 不用于续接。接口返回的 token 计数会规范化为 `prompt_tokens`、`completion_tokens`、`total_tokens`，并保留来源字段；缺失值保持未知，不填 0。没有发送接口不支持的 temperature 或 seed。模型名、实际服务端参数、版本和上下文容量须先核实并记录在模型配置的 `gateway_settings` 中；核实后将 `gateway_verified` 改为 true。API key 只从环境变量读取，不放进配置或结果。

### E1 的适用性

- ID 重命名会同步 schema、提示中的完整 ID 引用和 XML resource-id，输出再逆映射后交给原 validator／设备。保留 app 名、提示文字等其他线索；该实验干预的是 ID 语义线索，不称为纯无关噪声。
- 支持旧代码的 `::#::index` 组合标识，但只有 XML index 与执行器的匹配序号一致时才适用。重复 ID、无法对齐的历史后缀不会猜测绑定，按结构原因排除该重命名条件。
- `irrelevant_nodes` 添加 20 个无文字、不可交互的 `android.view.View` 层级节点。原提取器只处理输入类与相邻 `TextView`，因此 extracted 提示必然不变。该格标记 `identical_prompt_after_extraction`，不新调用模型；XML 格是实际扰动。它检验结构噪声，**不覆盖含语义干扰文本的噪声**。
- 如果另需含文字的干扰，应另定节点位置、可见性和重新提取协议，不能把手工追加提示称为原提取器产生的上下文。

### 请求容量与 Mistral

三个模型分别经过每次实际请求的容量检查，包括 E2 续轮历史。示例 `max_prompt_bytes=200000` 是可配置的保守字节预算，**不是 200000 token，也不是已经验证的模型上限**。超预算不自动裁剪任务、不计语义失败，状态为 `capacity_blocked`。

可配置本地 `tokenizer_json`、`context_window_tokens`、`reserved_tokens`。tokenizer 只数可见 message；reserved_tokens 必须覆盖网关系统／chat template 与输出开销。没有 tokenizer 时 token_count 为 null，不用字符数冒充 token 数。不自动下载 tokenizer。`plan` 检查初始提示，正式续轮按实际内容再检查；初始提示通过不能证明最长历史一定通过。

Mistral 的旧 vision 超限不在这里直接当作文本能力失败。是否纳入正式文本实验由实际网关容量核实决定，保留准确模型 ID。

## Oracle：规则与实际运行分开

每条评估包含 `run_status`、`output_status`、`rule_verdict`、`behavior_verdict`、`goal_verdict`、`diagnostics`，并保存原始响应、候选与 canonical 字段映射。

- `endpoint: rule` 可直接调用旧 validator，结果仅为规则实验；报告写 `metric_scope=rule`，不能直接命名为真实 PPTR／BDR。
- 默认 `endpoint: behavior`。正式调用模型前需要所有纳入 case 的 ready oracle 和执行命令，不能回退到规则结果。没有证据的案例保留未就绪信息。
- JSON／字段错误消费一次生成机会，可判定此次候选未达目标；API、检查器、设备和观测错误分别记为未完成，不能生成业务原因。schema 还要求值为字符串。
- 停止条件是成功、预算耗尽或配置的完整拒答文本精确匹配。默认拒答列表为空；未匹配的自然语言拒答按解析失败处理，避免根据任意关键词误判。四组采用相同规则。
- D 只允许 parser/schema、公开 UI 观测和已在所有组基础提示提供的规格检查。规则返回 false 时原因仍为 unknown；私有 bug 触发条件和完整 oracle 定义不能进入反馈。外部适配器负责其声明的来源真实性，runner 校验当前 request／candidate 绑定和本地证据。

### 使用设备适配器

配置 `oracle_manifest` 为填妥的 JSON，`oracle_command` 为 argv 数组，例如：

```json
["python", "-m", "text_input_eval.supplementary.android_oracle"]
```

从仓库根目录运行，并保留 `PYTHONPATH=tosem_major`。每例需提供以下字段；不要把下面的字段说明当作已确认的 case 数据：

| 字段 | 依据与作用 |
|---|---|
| `status`, `version`, `definition_source` | ready 及版本；对应已有配置、issue、测试或文档的来源 |
| `task`, `aut`, `apk_path`, `apk_sha256`, `device_id` | 精确任务、包名、APK 文件及设备；校验本地和实际安装 APK 的 SHA-256 |
| `reset_id`, `reset_description`, `reset_command`, `reset_timeout` | 已校准的初态恢复程序；覆盖应用和后端状态。不是简单重启 Activity |
| `navigation_steps`, `verify_action` | 沿用旧 YAML 动作结构，分别导航与提交；失败中止执行 |
| `bindings` | 原 schema key → 旧执行器接受的 resource-id 或 `resource-id::#::匹配序号`；逐字段核实 |
| `initial_assertion`, `goal_assertion` | 下方的断言结构；目标必须能辨认指定页面或指定缺陷表现 |
| `observation_seconds`, `poll_seconds` | 固定观察窗口与间隔 |
| `public_diagnostic_targets` | 可选，`id` 与 UI selector；只反馈当前截图 XML 中实际观察到的非空文本 |
| `public_specs` | 可选，带 `id`、来源、版本及文本的公开规格；E2 四组初始提示相同 |
| `frozen_files` | 所有外部 reset 脚本、配置、规格／定义等路径 → SHA-256，运行前校验 |

断言为 `{"mode":"all","targets":[{"selector":{"resource-id":"实际已核实的ID"},"exists":true}]}`。selector 支持 XML 的 resource-id/text/content-desc/class/index/package；多个属性同时匹配。all/any 对同一份 XML 判定；空目标、空选择器、观测异常均不会被当作成功或正常消失。

reset 命令须输出纯 JSON 回执 `{"reset_verified":true,"reset_id":"与配置相同的版本标识"}`；其含义由已校准的重置程序保证，并配合初态断言核验。不是随意输出 true 就完成了校准。

该适配器目前支持单个 APK 和 UI 断言，保存 reset 日志、前后截图/XML、填值后 XML、断言轨迹与 logcat。若目标断言在输入前已经成立，执行会中止，避免把原有页面状态当作本次成功。split APK、崩溃／日志特征或后端状态型 bug 需要专门适配器；不能把任意崩溃替代为指定 BDR 终点。本地测试未连接 Android 设备。

### 自定义行为适配器契约

命令读取一行 JSON stdin：case_id/task/request_id/candidate/candidate_hash/oracle/artifact_dir。每次调用必须恢复指定初态。stdout 返回纯 JSON，日志放 stderr。成功观测需包含：

```json
{
  "run_status": "ok",
  "request_id": "原请求ID",
  "candidate_hash": "原候选哈希",
  "oracle_version": "配置版本",
  "reset_verified": true,
  "behavior_verdict": false,
  "evidence": [{"path": "after.xml", "sha256": "文件哈希"}],
  "diagnostics": []
}
```

证据路径相对于 artifact_dir，必须是真实存在的非空文件。诊断需 type/detail/source/public/checker_id/evidence_ref；evidence_ref 为 `request:当前ID:证据文件`。无证据不给诊断。执行故障返回 run_status=execution_error，behavior_verdict=null。runner 不把失败原因推给模型。

## 正式执行与恢复

配置和 oracle 校准后，再显式执行 run；下列命令会调用模型：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major python -m text_input_eval.supplementary run \
  --config configs/supplementary/e1_e2.json \
  --output output/supplementary_run_v1 --max-new-calls 20
```

认证使用环境变量 `MUST_LAB_API_TOKEN`。`--max-new-calls` 只限制本次进程物理请求数，不改变实验预算；再次运行同一命令／目录从持久化进度继续。去掉该参数才连续执行全量。

结果写入新目录的 `results.sqlite3`，没有调用旧 MySQL writer，也不更新 raw/curated 表。请求在提交前落盘；响应落盘后再评估。正常恢复复用首轮和已完成续轮。进程若在调用／设备执行过程中被杀死，reserved/evaluating 状态会阻止自动重放，需要先对照服务端／设备证据处理不确定记录。API 错误默认不自动重试，轨迹记为 incomplete；不能静默用下一次重跑替换失败记录。需补跑时另建版本化 run，并在分析中明确规则。

### E2-abcdef-v1 MySQL 分析镜像

A-F runner 的本地 `ledger.sqlite3` 和 evidence 目录仍是 crash-safe 原始记录。不要写入或改造旧表
`t_llm_text_generation`；使用同一 MySQL 数据库中的独立表：`e2_abcdef_runs`、
`e2_abcdef_trajectories`、`e2_abcdef_external_calls`、`e2_abcdef_turns`。同步是幂等
UPSERT，完整 prompt、完整模型回答、解析后的候选 JSON、MUST 返回包、usage/time/session、
逐轮判定与视觉证据路径/哈希都会保留；PNG/XML/MP4 本体仍在 run evidence 目录。

先只核对待同步行数，不访问 MySQL：

```bash
PYTHONPATH=tosem_major python3 -m text_input_eval.supplementary.abcdef_cli \
  sync-mysql --run-dir output/<run> --dry-run
```

本地设置 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`
后执行真实同步；命令会创建缺失的新表，不会修改旧表：

```bash
PYTHONPATH=tosem_major python3 -m text_input_eval.supplementary.abcdef_cli \
  sync-mysql --run-dir output/<run>
```

正式 A-F 运行还要求用一个已通过的 smoke 验证 MySQL 行数以及完整 prompt/回答覆盖率：

```bash
PYTHONPATH=tosem_major python3 -m text_input_eval.supplementary.abcdef_cli \
  --config configs/supplementary/e2_abcdef_v1.json qualify-mysql \
  --run-dir output/<passed-smoke> \
  --output output/e2_abcdef_v1_20261002/mysql_mirror_qualification.json
```

`usage_coverage` 和 `provider_envelope_coverage` 单独报告。MUST 未返回 token usage 时保持缺失，
不得写成 0；可通过保存的 session ID 与 token 明细表关联，或在论文中报告覆盖率。

正式运行固定写入 `must_lab_db` 的四张 `e2_abcdef_*` 新表。启动前会核对实际连接的
数据库名；正式运行完成后自动执行幂等同步并在 run 目录写出 `mysql_sync_report.json`。
若 MySQL 同步失败，本地 `ledger.sqlite3` 和 evidence 仍保留为原始记录，但该次运行不得
宣称数据库落盘完成。

## 导出结果

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major python -m text_input_eval.supplementary report \
  --output output/supplementary_run_v1
```

- `requests.jsonl`：每次物理请求的原始使用量、耗时、会话和评估记录；完整请求/原始网关响应在 SQLite 中。
- `trajectories.jsonl`：共享首轮引用、逐组请求链、首成功轮、结束状态。
- `summary.csv`：按实验、任务、模型、表示、条件、分组汇总 planned/completed/incomplete/excluded/pending、成功率、挽救率和预算曲线。
- `physical_costs.csv`：物理请求、模型请求耗时和评估耗时按组分列，`shared` 首轮单独计一次。
- `run_summary.json`：全局物理请求、故障、usage 覆盖及已知 token 合计；缺失 token／费用不是 0。

`observed_success_fraction_planned` 是已观察成功占计划轨迹的比例，部分运行时不是最终成功率。`success_rate_completed` 和曲线同时给完成样本分母；未完成、排除、待跑保留可见。E2 挽救率条件为共享首轮失败的已完成轨迹；全体覆盖率必须同时报告。由于成功即停，C−B 应解释为显式标签提示效应。E1 是单次成功率，不能悄悄与旧 PPTR@3 混算。当前导出不自动做置信区间或显著性检验；正式统计应以 case/AUT 配对／聚类，不能把各次尝试当独立样本。

## E0 回放已有候选

导出候选为 JSONL：`{"source_record_id":"可回查的表名和主键","case_id":"P001","candidate":{"原resource-id":"已有值"}}`。可附 source_rule_verdict，原来源字段会保留。使用相同的已校准行为配置：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major python -m text_input_eval.supplementary.replay \
  --config configs/supplementary/e1_e2.json \
  --candidates /absolute/path/candidates.jsonl --output output/e0_replay_v1
```

该命令可能执行设备回放，但不会调用模型；未就绪 oracle 记录为 unresolved。输出 replay.sqlite3、replay_results.jsonl、replay_summary.json。矩阵比较同一个既有候选的当前规则与当前行为，只能支持实际覆盖案例的校准结论，不据此改写全部旧实验来源。

## 本地验证

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tosem_major python -m unittest discover \
  -s tosem_major/text_input_eval/tests -p 'test_supplementary.py' -v
```

测试使用假网关／假执行观测和真实本地缓存；不消费 API、不连接设备、不写原数据库。
