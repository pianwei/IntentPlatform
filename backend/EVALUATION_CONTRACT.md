# 评测数据、运行证据与归档契约 v1

INT-012.1 的 `evaluation_spec.py` 与 `evaluation_scoring.py`，以及 INT-012.2 的 `evaluation_evidence.py`、`evaluation_runner.py`、`evaluation_cli.py` 共用此契约。依据一期方案 V2.1 §11.2–11.5、路由契约 V2.1 §9 和 `docs/project/TESTING.md`。纯评分／投影无数据库或业务调用；执行器通过现有授权 Runtime API 收集观测。此批未实现归档管理 API、Console 展示或完整消融分析，不创建真实业务评测数据。

## 入口与版本

```python
from intent_platform.evaluation_spec import EvaluationDataset, ObservationBatch
from intent_platform.evaluation_scoring import score_evaluation

dataset = EvaluationDataset.model_validate_json(dataset_json)
observations = ObservationBatch.model_validate_json(observation_json)
report = score_evaluation(dataset, observations)
report_json = report.model_dump_json(indent=2)
```

所有模型拒绝未知字段和隐式类型转换，JSON 数值必须有限；不接受缺失金标。`schema_version` 固定为 `1`，`scorer_version` 固定为 `gold-exact-v1`，数据集／观测／报告的 `observation_normalization_version` 固定为 `runtime-observation-v1`。当前契约尚未发布；发布后修改评分或归一化含义必须新增相应版本，不能悄悄改变已有报告口径。数据内容或金标改变须由数据管理方赋予新 `version`；本模块不提供业务数据冻结服务。

`EvaluationDataset.reference` 和 `ObservationBatch.dataset` 都是 `{dataset_id,version,purpose}`，`purpose` 只能为 `development|sealed`。两者必须完全相等，且与 `configuration.purpose` 一致；不允许把开发观测换个用途计入封存报告。未知、重复 case/step ID 拒绝；漏 case/step 会生成失败记录，并保留完整金标分母。

## 数据集与场景

- `annotation`：`provenance` 为 `synthetic|business_reviewed`，另有 `author`、`review_ref`、`frozen_ref`。声明业务复核必须有复核引用；sealed 还必须有冻结引用。这些是待核验的来源声明，不是评分器确认过的业务审批。
- `scenarios`：每项有 `case_id`、`category:S1|S2|S3`、`workspace_id`、`user_id`、`split_group`、`tags` 和有序 `steps`。不读取或推断用户权限；执行器必须在已授权环境按场景身份收集观测。
- S1 单步、单诉求，必须有 `primary_capability_id`；金标决定为 READY 或 NEEDS_CONFIRMATION。每个 case 仅归入这一功能分母，即使另一个等价能力命中也不转移统计归属。
- S2 单步，`boundary` 必须为 `ambiguity|out_of_scope|authorization`；前两组对应 AMBIGUOUS、OUT_OF_SCOPE，authorization 由金标显式选择 FORBIDDEN 或 OUT_OF_SCOPE（候选过滤后可能没有可路由功能），均要求零执行；三个组独立报告分母。HTTP 401/403 仍是身份／权限故障，不能借金标改写为自然语言正常拒绝。
- S3 至少两个有序关键步骤，所有步骤都参与整场景判定；漏步、错步、步骤乱序均判整场景失败。
- `split_group` 由数据责任人按会话／表达族分配。交接前调用 `validate_dataset_separation(development,sealed)` 拒绝相同表达族或 case ID；该函数不能检测改名后的语义污染。封存材料仍需受控存储，不能进入提示、别名或优化循环。

本模块不自动补齐 20 功能、50 条／功能或任何建议样本数，不提供自动验收阈值。样本规模与门槛仍按 DEC-004 冻结；未提供的数据不能记为已执行。

## 逐步输入与金标

每一步有 `step_id`、`stimulus`、`expected`。`stimulus.kind` 为：request（`text`）、answer（`values`、`option_id` 或 `option_label` 三选一）、confirm（`confirmed`）、cancel（无附加参数）。每个场景以 request 开始；真实 run/revision、问题和确认快照由执行器绑定，不能通过话术绕开运行接口。`option_id` 仅用于 function_choice 的稳定能力 ID；实体／历史选项必须用 `option_label` 对当前问题的 label 唯一、区分大小写、无隐式裁剪地匹配，再提交当前 opaque ID。零匹配、重名均拒绝，不默认第一项，不从 expected 选择答案。

`expected` 必须显式提供：

| 字段 | 含义 |
|---|---|
| `decision` | 最终服务端决定：READY、NEEDS_CONFIRMATION、NEEDS_ARGUMENTS、AMBIGUOUS、OUT_OF_SCOPE、FORBIDDEN、CANCELLED、ERROR |
| `routes` | 一个或多个完整允许组合，每个 `actions` 项含 `capability_id/version/arguments`；组合顺序不影响结果，重复调用的数量会影响结果；不可从不同允许组合各取一部分拼接 |
| `semantics` | 业务金标给定的动作、角色、关系、否定及关键限制等 JSON 结构；明确为空表示没有额外维度，不得据此宣称已覆盖本体语义 |
| `clarification_kind/options` | kind 为 arguments、function_choice、entity_mention、entity_choice、history_choice 或 null。AMBIGUOUS 至少两个区分选项：function_choice 使用稳定 capability ID 集合，entity_choice/history_choice 使用唯一 label 集合；arguments/entity_mention 无选项，执行前后无问题为 null。原 opaque ID 只留轨迹 |
| `confirmation` | `{required,accepted}`；必要确认必须对应 S1 NEEDS_CONFIRMATION，READY 不得无依据增加确认 |
| `execution` | `{status,count,result,displayed_result}`；状态为 NOT_EXECUTED、SUCCEEDED、FAILED、UNKNOWN、CANCELLED；无执行为零调用和 null 结果 |

参数、语义、业务结果和展示结果按类型敏感的精确 JSON 比较；对象键顺序忽略，数组顺序保留，`true`、`1`、`1.0`、`"1"` 不等价。不调用模型自评，不做隐式模糊匹配。需要业务归一化的字段由执行器按已冻结的确定性规则处理，并记录其版本；否则升级评分版本。

## 观测与配置

`ObservationBatch` 还需 `trial_id` 和 `configuration`：`release_id`、`model_id/model_version/model_parameters`、`prompt_version`、`ontology_version`、`function_versions`、`connector_versions`、`environment_ref`、`resource_mode:synthetic|fixture|real`、`ablation` 和 `tags`。每个配置功能必须有连接器版本，金标至少有一个完整组合能由该 release 配置提供。配置只引用版本和安全环境标识，不存秘密值。

`model_version` 必须显式给出已知不可变版本或 null，不能把任意模型服务名称冒充权重版本。已注册确定性 fixture 实现 `development-deterministic-v1` 可作为其实现版本；真实模型版本缺失保留缺口。一个 trial 配置对应一个实际授权候选子集；不同权限范围分成独立 trial。profile 的 `capability_scope=authorized_subset` 与固定 release ID 一起记录；配置功能／连接器版本须与观测子集完全一致。空子集 S2 不能代表全功能 S1，真实 20 功能验收仍需全量授权配置及完整数据范围。

消融 A/B 使用相同数据集与可比较配置，显式改变 `ablation` 及相应本体／提示配置；各次随机重复分别记录 trial 和参数，不把多次运行拼成一份去重后的观测。本模块保留标签和配置，不自动推断消融收益、统计显著性或样本充分性。

每个 `CaseObservation` 有 `case_id` 和有序 `steps`。每步需要 `step_id`、`routing_outcome:completed|timeout|error|not_run`，可提供最终 `decision`、`validation_completed`、`actions`、`semantics`、`clarification_options`、`confirmation` 和 `execution`。无法观测的字段留空而不编造成功；已观测错误值必须保留。能力版本不符合配置会评分失败，不能删掉异常 case。

`confirmation` 观测还包含 `binding_valid` 和 `executions_before_confirmation`。需要确认时，确认前调用次数必须为零；已接受的确认必须绑定正确。`execution.count` 是本步骤的业务调用数，包含失败调用，不能仅统计成功响应。确认前未执行可以通过路由子指标，但不计为业务完成。

`faults` 保存逐项 `{stage,code,detail,evidence_ref?}`；stage 覆盖输入、授权、模型、校验、路由、确认、下游、展示、超时、基础设施及未知。正常的 FORBIDDEN/OUT_OF_SCOPE 决定不是异常；真正的鉴权基础设施故障应记录 fault。不得用一个泛化“失败”替换既有错误归因。`unauthorized_executions` 和 `leakage_events` 默认为 null，只有实际审计证据才填写计数；非零强制该步骤失败。报告汇总已知次数与 `unobserved_safety_steps`，未知不能记作零事件或已完成安全验收。

## 评分报告与分母

| 指标 | 固定口径 |
|---|---|
| S1 N | 数据集中全部 S1 case，包括未运行、超时、澄清和下游失败 |
| S1 A | 最终 routing_outcome=completed、完成服务端校验、有选定动作且决定 READY／NEEDS_CONFIRMATION；仅有模型正确提议不算 |
| S1 C | A 中决定、完整能力组合／版本及确认路由策略符合金标，且没有已观测越权／泄漏的 case；安全未知不抹去路由信息，但完整判定仍失败 |
| 正确路由率／已决准确率／覆盖率 | C/N、C/A、A/N；分母为零返回 `value:null,defined:false`，A=0 明确不能据此达标 |
| 参数／语义准确率 | 对应最终参数或语义比较通过数 / 全部 S1 N，独立于路由指标 |
| 每功能及宏平均 | 按固定 primary_capability_id 计算 N_i/A_i/C_i；配置中没有样本的功能仍列出 N_i=0、rate=null 并警告，不补样本；宏平均只取有样本功能 C_i/N_i，不能表示未评测功能；混淆表保留未决定与失败桶 |
| READY 自动执行覆盖率 | READY 金标中完成正确路由、参数、语义、业务调用与展示的比例 |
| 确认指标 | NEEDS_CONFIRMATION 全部分母统计确认前零执行；金标要求已确认的子集统计绑定正确及确认后业务成功；无样本为 null |
| 端到端成功 | S1 中全部金标匹配、无观测异常且业务状态 SUCCEEDED 的数量 / N；下游失败保留原始分母，可同时拥有正确路由 |
| S2 | 各边界组最终决定、选项、空能力组合及确认策略符合金标的路由比例；独立安全缺口不改写正常拒绝为路由失败，完整 case 仍失败。另报域外 READY／NEEDS_CONFIRMATION 错误接受次数和原组分母，不借用 S1 的 A 定义 |
| S3 | 全部关键步骤匹配且顺序正确的完整场景数 / 全部 S3 场景，不按轮次加权 |

`correct_cases` 是跨类别金标一致性的描述性统计，不能替代 S1/S2/S3 指标或业务完成率。每个 case/step 都保留观测及 `differences` 的 expected/observed；超时、下游错误等原始 faults 与金标差异分开汇总。报告另有越权／泄漏次数、观测状态计数和警告。

`acceptance_evidence` 固定为 **false**；即使数据用途为 sealed、资源声明为 real、所有分数满分，也只标 `evidence_kind:sealed_unverified`。真实环境、数据审批、完整范围、版本一致性及业务验收由后续受控报告核实，本评分器不自动批准。

## 实际运行证据与确定性归一化

`GET /api/v2/runtime/conversations/{cid}/evaluation-profile` 与 `GET /api/v2/runtime/runs/{rid}/evaluation-evidence` 使用 scoped runtime token，只允许实际 owner/session；管理 token 不直接读取运行证据。授权、release、run/revision、事件及账本须来自同一数据库快照。已撤权的实际调用历史仍拒绝读取，profile 可返回当前授权候选子集。

profile 提供真实 release、主体、空间、agent、channel/session、模型、提示、本体、功能／连接器／编译器版本。evidence 提供最终决定、同 revision 服务端 validation_evidence、选定请求、确认事件与审计绑定、Call ID/status/dispatched_at/result、RESULT components、ERROR 和原始 bindings。仅模型 proposal 或过期校验证据不能宣称 validation_completed。

`runtime-observation-v1` 的语义投影规则固定，不读取金标决定保留字段：

- 同 revision、服务端 semantic/input_schema/preconditions 全部通过的 requests 才输出完整语义；请求按原 index 排序，保留 capability_id、action、object_type、result_type、negated、parameter_units。participants 只保留 role/type 并按确定性 JSON 排序；relations 保留 name/source/target 并排序。
- bindings 只投影 state=resolved 的可信实体记录，保留 request_index、parameter、role、source、resolver_ref、实际业务 value 和可用 source_version；历史来源仅保留稳定 binding/index。绑定记录按确定性 JSON 排序。
- run/call/option ID、mention 文案、reference_time 和其他动态定位不进精确语义金标；原信息留在 raw_bindings、validation_evidence、question 与非秘密轨迹。相对时间转换后的业务日期在 actions.arguments 中比较，不抹去参数错误。
- 正常拒绝、澄清歧义和取消没有可执行语义帧，输出空结构，不要求完整执行校验。旧 v1 没有持久语义证据则为 null 并报缺口；不得宣称已覆盖本体语义。HTTP RESULT components 不能证明真实浏览器展示，displayed_result 保持 null；独立越权／泄漏审计也保持 null。

执行器通过 `/me` 验证令牌真实主体／空间，管理 token 仅经正式 chat session 交换取得运行 token；embed 必须由调用方提供已授权宿主运行 token。继续校验 agent/channel/session 及固定 release，之后才提交场景 stimulus。只读业务行为为默认：即使 stimulus 请求肯定确认，缺少 `allow_effects` 也不提交确认；允许时仍绑定实际 run/revision、当前确认事件及未过期快照。否定确认可在默认模式发送。提交超时或 UNKNOWN 不重提，不自动恢复执行后续步骤；只读 history 快照保留恢复证据。

模型／编译／版本缺口、运行异常和漏步保留完整分母；路由信息可独立计算，完整 case／端到端缺证据继续失败。运行器不使用 expected 构造任何 observation。

## CLI 与原子归档

从 backend 使用项目 Python（参数文件需由已授权调用方准备）：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py score --dataset dataset.json --observations observations.json --output evaluation-trials
.\.venv\Scripts\python.exe scripts/evaluate.py execute --dataset dataset.json --configuration configuration.json --target target.json --bindings bindings.json --base-url http://127.0.0.1:8000 --trial-id trial-001 --output evaluation-trials
```

target 为 `{public_id,agent_id,workspace_id,channel:"chat"|"embed"}`。bindings 为 `{bindings:[{user_id,workspace_id,env_var,runtime_session_id?}]}`，env_var 仅是已设置环境变量名；CLI 不接受明文命令行 token，不记录 Authorization。`--allow-effects` 和 `--allow-sealed` 为相互独立显式开关；sealed 默认拒绝执行，离线评分／归档允许。封存执行仍需要已授权 token 和业务数据审批，不能用于开发优化。

执行前先验证数据／配置并独占创建新 trial 目录；拒绝已有目录、路径穿越及 Windows 保留名称。成功文件只有 `trial.json`，先写入临时文件、flush/fsync，再通过不覆盖的原子链接提交；失败可能留下无 trial.json 的未完成目录，不能复用该目录或因此自动重发业务请求。文件系统不支持原子链接时拒绝归档，不降级为覆盖写入。

`trial.json` 顶层固定为：`schema_version:"1"`、`kind:"intent-evaluation-trial"`、`trial_id`、`created_at`、`acceptance_evidence:false`、`dataset`、`observations`、重算的 `report`、`trace`、`runtime_evidence`、`gaps`、`redactions_applied`。runtime_evidence 按 case ID 保存 `{profile,steps:{step_id:evidence},history?}`。离线 score 明确附 `OFFLINE_OBSERVATIONS_UNVERIFIED` 缺口，无运行轨迹不能补成实际调用证明。CLI 返回 0 表示完成归档，不表示报告达标；参数／凭据读取／归档失败返回 2。

评分输入 dataset／observations（包括 configuration）及权威 report 保留原始业务键和值，不根据 token_count、credential_ref 等业务名称猜测脱敏。写入前重新验证三份模型，检查原报告与重算报告一致；对实际序列化后字节再次验证并重算一致。若已知真实令牌值出现在评分输入或报告中，拒绝归档且不输出有效 trial.json，不以改写输入产生伪通过。trace／runtime_evidence／gaps 仍按秘密字段和已知令牌值脱敏。

归档附 `sanitization:{mode:"known-secrets-and-wire-fields",score_inputs:"preserved",unknown_secrets_checked:false,source_responsibility:"caller-reviewed"}`。调用方负责提供可分享的脱敏业务材料；工具不能自动识别任意业务 JSON 内未知秘密。redactions_applied 仅描述实际轨迹脱敏，不能代替来源审查。接收方应保留原始上传字节，不能先 JSON.parse 再序列化而消除 1/1.0 差异。归档不是验收签名或已核验数据源。

Python `run_comparison(dataset, [(runner,configuration,trial_id),...])` 只执行各自已发布候选；要求模型、参数、功能／连接器、环境、用途一致，release、ablation 和 trial 不同。允许显式本体／提示对照，不更改任何 release，不合并试次或宣称统计显著性；完整消融编排仍待后续。

## 验证与后续集成

纯评分与受控 HTTP 测试在 `backend` 目录运行：`./.venv/Scripts/python.exe -m pytest tests/test_evaluation_scoring.py tests/test_evaluation_runner.py -k "not postgres" -q`。使用显式合成金标、HTTPX MockTransport，覆盖分母、确认、边界、多轮、动态选项、稳定语义、版本／用途、错误归因、令牌脱敏和原子归档。真实 PostgreSQL／Runtime API 专项为同文件 `-k postgres`，使用既有独立 `_test` 库守卫、实际 FastAPI／Worker 与回环 resolver；不得与另一进程并发运行共享测试库 Worker。实际通过数量及 PG API 集成结果以协调者归并的本次命令输出为准。受控 transport 与确定性 fixture 均不算真实模型／连接器／浏览器或业务验收。

后续范围包括归档管理 API／Console、受控封存存储与审批、真实资源证据、完整消融分析和现有开发诊断迁移。此处 CLI 归档不能替代这些能力。

INT-012.3 增量提供独立的有限 A/B/C 影子对照，契约见 [ABLATION_CONTRACT.md](ABLATION_CONTRACT.md)。使用 `scripts/evaluate_shadow.py`、专用数据集与 `shadow.json`；仅比较决策/完整功能组合，失败和未知保留分母，不修改本文件的 gold-exact-v1 评分口径。影子接口不发送金标、不建 Run/Call/Event、不执行业务；B 的正常端到端与 S3 仍必须通过上述正式运行器分别验证。完整统计分析、真实业务验收与封存治理不由该增量自动完成。
