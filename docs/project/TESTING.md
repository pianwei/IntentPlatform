# 验证分级、全量范围与证据

遵守“小功能小测试，大版本全量测试”。先确定需要证明的需求行为，再选择测试；不能仅按代码行数决定等级。需求见 [REQUIREMENTS.md](REQUIREMENTS.md)，流程见 [WORKFLOW.md](WORKFLOW.md)。不生成 SHA256 相关检验、文件或步骤。

本文维护验证规则、实际命令登记及完整测试范围。验证执行者返回证据与结论，由任务负责人写回唯一任务状态源 [TASKS.md](TASKS.md)；本文件、SKILL.md 和阶段表不复制任务的通过/失败进度。

## A/B/C/D 与最低验证范围

| 等级 | 判定依据 | 必需验证与尺度 |
|---|---|---|
| A 低影响 | 纯文档、文案、无业务行为变化的局部样式 | 检查内容、术语、链接、引用及适用页面效果；只有实际受影响时才构建。不为可逆的低影响调整机械编写自动化测试。本次前期规范文档建设按此执行 |
| B 局部功能/缺陷 | 影响清楚且局限的功能变化 | 相关单元与必要集成测试；正常、异常、边界和原缺陷复现路径。适合时保留能复现原问题的回归用例；涉及功能路由时包含受影响功能及相邻混淆功能 |
| C 跨模块/重要变更 | 公共契约、权限、状态机、数据迁移、关键依赖、重要性能或跨模块行为 | 覆盖受影响调用方和关键端到端链路，按风险加权限、并发、兼容、迁移、性能或恢复验证。两行权限变化仍不能按 B 类缩小验证；全局模型等专项触发按下文升级 |
| D 大版本/系统性变更 | 用户指定大版本、广泛架构/兼容性变化、大范围依赖或数据结构升级 | 执行下文定义的完整软件套件、完整 Agent 评估、关键业务及适用的兼容、升级、恢复和候选版本验证。阶段功能演示或少量用例通过不等于全量通过 |

影响范围扩大时重新分类并记录依据。失败时检查根因，不删断言、跳过必需用例或变更分母来获得通过。确认是过期规则时按需求变更流程修订。没有新改动、失败或未解决疑点时，完成要求后不无故重复全量测试。

改变发布包中的纯显示文案，仍可按 A 类检查；编辑草稿并按既有机制产生新配置，不等于修改发布机制。只有实际改变发布快照契约、共享行为或兼容性时才扩大等级与验证，不能直接覆盖不可变旧快照。

## 本项目必须保留的专项规则

1. **第 21 项现场扩展**：执行新功能测试、原 20 项相邻混淆功能测试，以及原有**全量 S1**；不能仅测新增功能。若新增内容同时改变共享本体、全局提示或模型，则叠加全量回归。依据主文 §5.2、§11.5。
2. **全局模型、提示策略、公共本体变更**：执行全量回归，包含完整软件套件和完整 Agent 评估；记录新旧配置的效果、覆盖、延迟及适用资源消耗对比。单点功能文案或空间私有映射的修改，先评估实际影响，不能因名称相似自动当全局变更，也不能把共享修改伪称单点。依据主文 §11.5、契约 §9 及项目固定约束。
3. **状态机、身份协议或发布版本的大改**：执行全量回归；小范围修复至少按 C 类覆盖相关调用链、拒绝路径及故障窗口。依据契约 §5.4、§7、§9。
4. **真实接入与模型质量**：mock、固定模型输出、静态校验或文档阅读只证明各自层次；不能代替 20 项真实接口联调、真实模型封存评测、真实宿主浏览器或真实硬件性能。测试环境可使用脱敏数据；未获准的副作用不能因执行测试而扩大授权。
5. **正确路由不等于完成业务**：分别验证服务端路由、参数/实体、策略确认、真实调用和结果展示。确认前不得执行业务；未知写结果不得自动重放；下游失败仍进入原始端到端分母，另行归因。
6. **M3 产品验收**：覆盖 REQ-001–REQ-014／A01–A14 与已冻结量化口径、第 21 项专项，以及主文 §12 的企业最低保障。必需检查因环境缺失未执行时保持待验证，不以不适用标记掩盖缺口。

## “全量”包含什么

全量是特定候选版本与配置下项目定义的完整验证范围，不是穷举所有输入。D 类或专项要求全量时，在执行前列出以下集合的实际测试入口、数据版本及适用性；不能只选择已有的少量用例就声称范围完整。

| 集合 | 完整范围 | 适用性与缺口处理 |
|---|---|---|
| 软件基础套件 | 真实工程定义的静态/类型检查、全部单元与集成测试、Console 与 Agent Web 构建及关键端到端测试；覆盖后端、工作器、连接器、发布编译器和持久化契约 | 从实际代码配置登记。未建立的层次记录覆盖缺口；不能把“没有测试”当“不需要测试” |
| Agent 回归 | 同一启用全部 20 功能的智能体运行完整 S1、S2、S3；包含各功能金标、语义/参数、边界、确认、多轮与真实端到端结果，报告逐功能和总体指标 | 使用冻结数据与评分规则；开发集与封存集隔离。记录随机性处理与重复次数，不能用模型自评替代业务金标与客观结果 |
| 产品与隔离 | REQ-001–REQ-014／A01–A14 覆盖矩阵：真实接入、全量/子集智能体、领域扩展、本体正反例与消融、跨空间跨用户、双前端、真实 iframe、身份、回滚、故障及全内网 | 每项关联可执行场景及证据。已有稳定证据是否可复用须说明配置一致性和变更影响；该候选版本要求实际重跑的检查不能只引用旧结果 |
| 性能与资源 | 已冻结路由计时口径、固定并发及开放式到达负载、冷启动、最大历史、文档接入并行、排队/限流/失败与容量曲线 | 记录实际模型与硬件；性能测试未运行时不能宣布对应目标通过。环境参数不齐时关联 DEC-003/004 并列出补验条件 |
| 兼容、升级与恢复 | 实际支持的浏览器/宿主和接口版本、数据迁移与前后版本兼容、备份恢复、候选发布升级/回退、模型/提示/功能组合恢复；验证权限撤销不会随回滚恢复 | 按版本范围确定旧版本、迁移路径、RTO/RPO 与目标环境。确实无数据库迁移或旧版本升级路径时可说明该专项不适用；关键业务恢复、发布回滚等一期必需项不能因此排除 |
| 交付环境 | 获准目标环境的启动、核心入口、本次变更和相关日志/指标；正式产品验收含断外网冷启动和恢复 | 部署命令成功不能代替业务可用；无环境权限时仅记录已完成准备与未验证项，不宣称已部署或上线 |

版本任务必须补齐全量清单与关键业务场景；如某集合尚无法建立，应记录缺口、影响与补验条件，由任务负责人保持正确状态。任何“不适用”均写实际理由，不能用来排除一期既定目标。全量软件＋完整 Agent 集合及适用升级恢复是共同条件，不能互相替代。

## 真实命令登记

已建立本地工程。下列命令从仓库配置和脚本登记；执行事实见 [本地工程验证记录](records/INT-003-local-engineering.md)。除标明前端目录外，执行目录为项目根，要求 Docker Desktop 可访问；脚本在步骤失败时返回失败。命令登记不代表对应验收已通过。

| 检查类别 | 当前可查证入口/命令 | 后续登记依据 |
|---|---|---|
| 后端单元/集成 | `./scripts/verify-local.ps1 backend`；套件 `backend/tests/`，源配置 `backend/pyproject.toml` | 独立 intent_test 数据库，真实受限 PostgreSQL 角色；通过条件为 pytest 全部通过，不指向演示库 |
| Console / Agent Web 类型、组件和构建 | `./scripts/verify-local.ps1 frontend`；frontend目录 `npm test`、`npm run build` | package-lock固定依赖；脚本用Linux Node环境安装、测试并构建两应用，任一失败中止 |
| 全部现有软件入口 | `./scripts/verify-local.ps1 all` | 后端全部已建测试 + 前端全部已建测试/类型/正式模式构建；仍不等于M3全量范围 |
| 开发模式集成构建 | `./scripts/dev.ps1 up`；frontend目录 `npm run build:development` | Compose显式启用开发构建，正式默认build不得显示开发身份/夹具宿主 |
| 浏览器与宿主端到端 | 本地 Playwright CLI 操作路径及截图见[INT-009记录](records/INT-009-unified-apps.md) | 已测本地跨站开发宿主及CDP实际阻断第三方Cookie；正式外围宿主和支持浏览器矩阵仍待验证 |
| Agent S1/S2/S3 评测与评分 | backend目录`./.venv/Scripts/python.exe -m pytest tests/test_evaluation_scoring.py tests/test_evaluation_runner.py tests/test_evaluation_archive_integrity.py -q`；CLI `./.venv/Scripts/python.exe scripts/evaluate.py --help`，契约见`backend/EVALUATION_CONTRACT.md`；Console评测诊断可读取严格trial归档 | 执行器可驱动实际运行API，报告保留原分母及未观察项；合成数据、固定模型和本地PG只证明工具，冻结金标、模型/功能配置仍待提供 |
| 有限A/B/C影子对照 | backend目录`./.venv/Scripts/python.exe scripts/evaluate_shadow.py --help`；`test_shadow_routing.py`、`test_shadow_api.py`、`test_shadow_cli.py`由后端套件执行 | API默认关闭，测试服务显式启用并要求限定运行会话的owner兼publisher/admin；A说明参数/B完整领域/C联合去关系，永久零业务派发，失败保留分母；shadow.json与正常E2E/S3报告独立，不标业务验收或因果收益 |
| 隔离与故障 | `backend/tests/test_postgres_api.py`、`test_http_socket_and_recovery.py` 等由backend套件执行 | DB/网络/状态注入层次分别记录；真实内网故障、硬件性能、完整负载曲线未执行 |
| 迁移、备份、升级与恢复 | `python -m intent_platform.migrate`；`./scripts/backup-local.ps1`；隔离库 pg_restore 演练见记录 | Alembic实际迁移文件、本地归档恢复；生产历史版本升级、RTO/RPO及离线冷启仍待真实配置 |
| 0001→0002独立升级 | backend目录`./.venv/Scripts/python.exe scripts/verify_upgrade.py`，先配置指向同一PostgreSQL的owner/app环境URL | 只使用初始为空且具备受限角色的`intent_upgrade_test`，已有证据拒绝覆盖；后续迁移另设独立验证，不将本结果外推 |
| 0002→0003领域升级 | backend目录`./.venv/Scripts/python.exe scripts/verify_domain_upgrade.py`，owner/app环境URL指向同一独立库 | 使用初始为空的`intent_domain_upgrade_test`，拒绝覆盖已有证据；检查旧快照与UNKNOWN记录保留、新领域RLS及不可变约束 |
| 0003→0004来源升级 | backend目录`./.venv/Scripts/python.exe scripts/verify_import_upgrade.py`，owner/app环境URL指向同一独立库 | 使用初始为空的`intent_import_upgrade_test`，拒绝覆盖已有证据；检查旧文档/发布/调用逐值保留、新来源版本RLS与不可变约束 |
| 0004→0005治理升级 | backend目录`./.venv/Scripts/python.exe scripts/verify_governance_upgrade.py`，owner/app环境URL指向同一独立库 | 使用初始为空的`intent_governance_upgrade_test`；保留旧快照并核验治理/尝试记录RLS。四个升级脚本可用`INTENT_UPGRADE_TEST_DATABASE`指定新的`_test`库，拒绝覆盖已有证据；需先创建受限角色可访问的空库 |
| 三故障窗口实际进程验证 | backend目录`./.venv/Scripts/python.exe scripts/verify_process_faults.py --run` | 只允许专库`intent_process_faults_test`；真实子进程终止/自然租约到期/HTTP断连，测试屏障定位故障，保留既有记录；普通pytest跳过，必须单独执行；报告在output/process-faults，不代表前端DOM或真实业务验收 |
| 本地离线冷启动 | 根目录`./scripts/verify-offline-local.ps1`，依赖已缓存`intent-backend:local`、`intent-web:local`及PostgreSQL镜像 | 新独立internal网络/数据库，无宿主端口，禁止拉取构建；HTTP验证三入口/本地资源/运行token边界及合成Worker单次执行，报告在output/offline；停止并保留验证数据，不能替代正式内网验收 |
| 必需 CI 检查 | 尚未建立 | 已存在且能运行的 CI 配置与代码托管检查记录 |
| E0 连接器流式与协议边界 | backend 目录在独立测试环境运行 `./.venv/Scripts/python.exe -m pytest tests/test_connector_transport.py -q`；完整套件由 `./scripts/verify-local.ps1 backend` 执行 | 本地回环合成服务；覆盖分块、压缩与解压超限、重定向、Dify/HiAgent 明确/未知状态、状态查询和写超时单次发送；真实实例协议仍需 R2 单独验证 |
| 正式配置静态与角色预检 | backend 目录 `./.venv/Scripts/python.exe -m pytest tests/test_production_config.py -q`；正式入口见 `infra/production.compose.yaml` 与 `infra/PRODUCTION_DEPLOYMENT.md` | 合成设置验证缺配拒绝；目标环境 `python -m intent_platform.production_preflight` 还须连真实受限数据库执行只读角色检查，模板不构成正式部署验收 |
| E1 有限业务输出与旧版本兼容 | backend 目录 `./.venv/Scripts/python.exe -m pytest tests/test_output_contract.py tests/test_http_socket_and_recovery.py -q`；完整套件由 `./scripts/verify-local.ps1 backend` 覆盖；前端由 `./scripts/verify-local.ps1 frontend` 覆盖 | 合成回环与受限 PostgreSQL 检查 Schema/字段/文件投影、读写错误、旧发布及探测不泄露；`backend/OUTPUT_CONTRACT.md` 是行为边界，不替代真实业务字段确认 |
| E2 数据库接入任务与前端轮询 | backend 目录 `./.venv/Scripts/python.exe -m pytest tests/test_ingest_jobs.py -q`，受限 PostgreSQL 测试库与独立子进程；前端 `./scripts/verify-local.ps1 frontend` 含 `frontend/tests/ingest.test.ts`、`source-import.test.tsx`；本地 Compose 启动后根目录 `python scripts/verify_ingest_local.py` | 检查 202/任务状态、幂等、空间隔离、队列并发、租约过期恢复、失败无修订、旧同步接口及长解析期间在线 API；Console 轮询与固定修订读取。正式环境 Worker 与资源负载仍需 E3/R1 补验 |

新增登记必须包括：名称、覆盖范围、实际命令或人工步骤的来源位置、执行目录、必要运行时/环境/数据、预期成功条件及报告位置。机器命令必须已从仓库查证；命令已登记不代表每个任务已执行。涉密环境仅保存安全引用，不登记密钥值。手工步骤可作为真实入口，但需足以由另一执行者重现。

GOV-001 规范建设当时只检查 Markdown 内容、路径/链接、ID、分工、交叉引用及代表性流程走查；事实记录见 [GOV-001 验证记录](records/GOV-001-validation.md)。此类检查不计入产品全量软件测试或 Agent 评估。

## 开发用例与封存数据交接

Console 开发诊断接受用户提供的 JSON 数组，每条包含 `text`、`expected_capability_id`，运行时指定 `label` 和发布智能体。它只统计路由提议，不自动把失败、补参、实体绑定、确认或真实执行全部判为成功。

正式评测材料另行提供：数据集版本/用途（development或sealed）、场景ID、S1/S2/S3类别、原始话术与多轮顺序、目标功能/拒绝或澄清金标、参数与实体预期、确认边界、真实结果判定、空间/用户权限、标注人和复核记录。封存数据不得放入别名、提示示例或开发诊断优化循环。未知金标保持待确认，不用AI生成内容填足建议样本数。

M0 冻结时还需确认计分分母、异常归因、逐功能切片、模型随机性、数据访问/保存位置及真实性。建议 S1 1000、S2 200、S3 100 个场景，以及质量/时延阈值仍以 DEC-004 和真实资源确认结果为准；不得擅自降低标准。

## 单次验证证据字段

小任务可在 TASKS.md 的任务记录内写清证据；多场景、长输出或候选版本按需写 `records/任务ID-主题.md`，TASKS.md 只引用该证据。证据文件记录一次实际执行的事实，后续重跑追加或新建记录，不覆盖历史失败使其消失。

| 字段 | 应记录内容 |
|---|---|
| 关联 | 任务 ID、REQ/A 编号、变更等级、触发专项与验证范围理由 |
| 验证对象 | 实际文件或 Git 提交（存在时）、构建编号、候选软件版本；AgentRelease、功能/连接器、文档、本体、模型及参数、提示与组件版本 |
| 环境和数据 | 日期、执行者、环境、真实/模拟工具类型、运行时与关键依赖；评测集/评分规则版本、硬件、负载、历史长度、缓存及随机性策略等相关条件 |
| 实际操作 | 执行目录、完整实际命令或可重复人工步骤、输入/场景、预期可观察行为；不将计划命令写成执行记录 |
| 结果 | 通过、失败、未执行、受阻或不适用；实际输出、退出码（有时）、样本分子/分母、耗时/质量指标；区分基线问题与本次引入问题 |
| 证据位置 | 报告、脱敏轨迹、网络记录、截图或日志的可访问位置；不输出密钥、票据和完整敏感业务响应 |
| 缺口与补验 | 未执行项及原因、不适用依据、影响范围、需要的真实环境或决策、补验方法；缺陷关联任务 |
| 有效性 | 与候选配置是否一致，后续哪些修改会使证据失效，复用证据的依据及仍需重跑范围 |

最终结论分别说明本次产出、已执行验证、未验证范围和交付状态。仅通过静态检查、模拟或文档走查时必须明示层次；真实 API、模型、硬件或浏览器缺失时不得将对应验收写为通过。任务负责人据此更新 TASKS.md，质量执行者不另建状态看板。
