# INT-018 三层意图 Demo 本地工程候选

日期：2026-09-23。等级 D。验证对象为分支 `codex/INT-017-local-candidate` 上 INT-018 的同一工作树候选，数据库最终迁移 `0009`；Python 后端、PostgreSQL、Worker、Console 与 Agent Web 均在本地 Compose 运行。20 项功能、数据、开发身份和联调宿主全部是**模拟工程资源**，不计入真实业务功能台账。

## 产出与纵向链路

- `backend/src/intent_platform/demo/` 为 20 个不同用途的功能包提供实体/动作映射、输入输出契约、至少 5 条开发问法、错误与未知语义、幂等及关联模拟数据。7 类业务实体、6 类动作、5 个版本化业务 Skill 均可在 Console 管理；开发管理员显式安装后，同一智能体的发布快照启用全部 20 项。
- 模型配置在后管保存不可变版本，凭据仅服务端加密持有；无可用模型时，匹配 Skill 的规则链可以作为明确标注的工程预览运行，无 Skill 的问题不会把固定输出伪装成模型计划。有限模型计划及一次修正的测试使用桩模型，只证明协议、校验与 Worker 执行闭环。
- 计划与步骤入库，最多 8 个逻辑步骤、5 个批量对象、20 次业务调用和 3 个并行调用。非法或越权计划在第一笔调用前拒绝；写操作需要确认，未知回执先对账而不重发。最终答复只取获准结果，模拟 Skill 答复是可读业务摘要。
- `/console/` 展示实体层、动力层、策划层与执行结果的结构化轨迹；`/chat/{应用标识}` 与 `/embed/{应用标识}` 使用限定运行身份。用户只看到获准业务内容、简化步骤和实际耗时，不能通过管理 API 读取轨迹。管理端可审查同空间其他用户及已撤权用户的历史轨迹，普通运行 API 与空间隔离保持原约束。

## 已执行验证

| 检查 | 实际结果 |
|---|---|
| 后端完整套件 | 项目根 `./scripts/verify-local.ps1 backend -TestDatabase intent_demo_release_0923_test`，独立受限 PostgreSQL 库从空库迁移至 `0009`；**432 passed、3 skipped、1 既有 Starlette 弃用提示**。覆盖功能、连接器、版本、权限、状态机、投影、模型配置及新跨用户管理员轨迹。 |
| 20 项业务包 | `test_demo_catalog.py` 对全部 20 项逐一执行正确输入、非法输入、确定失败及超时未知；每包至少 5 条开发问法在包内登记并校验数量。另验证同名客户、关联/筛选、空结果、整数分统计、两个可对账的模拟写操作。开发问法尚未用真实模型逐条评测。 |
| 复合场景 | 数据库 Worker 跑通客户资金概览、账户收支分析、贷款概览、客户业务全景、异常账户核查；另覆盖同名消歧、缺日期补参、拒绝及接受写入确认、空客户失败、无 Skill 无模型明确停止、桩模型无 Skill 条件链、非法后续步骤零调用、未知写结果对账且无重发，共超过 10 类可复现场景。 |
| 用户/管理投影 | `test_trace_projection.py` 覆盖旧事件白名单、敏感字段遮蔽、功能撤权、对象撤权后的 Run/Event/SSE/History/File 拒读；管理员跨用户与撤权后轨迹可查，其他空间拒绝；非管理员直接调用只读数据库函数返回空。 |
| 前端及构建 | 项目根 `./scripts/verify-local.ps1 frontend`，列表卡片改善后完整重跑 **83 passed、1 skipped**；TypeScript、Console 与 Agent Web 正式构建均通过。表格结果新增组件用例验证带业务名称的多行显示。 |
| 真实进程故障窗口 | `backend/.venv/Scripts/python.exe scripts/verify_process_faults.py --run`，独立库 **3 passed / 35.96s**；[结果](../../../output/process-faults/20260923T101611Z-ebff74d829/summary.json)。有限策划的未知写恢复另在后端套件中通过。 |
| 既有数据升级 | 升级前归档保留在被忽略的 `.local/backups/intent-20260923-182811-685-demo-pre-upgrade.dump`；开发库 `0006→0008` 后原有发布 7、运行 106、调用 104、事件 536 条数量保持，未改写旧发布。最终 `./scripts/dev.ps1 up` 应用 `0009` 后，开发库 `agent_releases=8`、`runs=109`、`calls=116`、`events=575`、`execution_plans=3`；后者包括新演示对话。生产数据库升级尚未执行。 |
| 浏览器单入口 | Console 在 `http://127.0.0.1:5173/console/` 安装 20 功能/5 Skills 并查看三层日志；独立聊天页和本地宿主 `http://localhost:5173/console/dev-host.html` 均打开同一 20 功能智能体。宿主 `localhost` → iframe `127.0.0.1` 以一次性票据建立运行会话；资金概览完成 3 个逻辑步骤/4 笔业务调用，答复列出两个账户及 **14,500.00 CNY** 合计，用户端显示可展开步骤与约 1.2 秒实际总耗时。宿主是开发夹具，并非真实外围系统。 |
| 断外网冷启动 | `./scripts/verify-offline-local.ps1 -RunName intent-offline-demo-20260923-01`：全新 internal 网络/数据库、无外部 TCP，Console/Chat/Embed 和 6 个静态资源可读；合成运行单次调用、重复消息、事件恢复、接入 Worker、运行令牌拒绝管理 API 均通过。见[本地离线报告](../../../output/offline/intent-offline-demo-20260923-01/report.json)；未使用真实模型与业务。 |
| 备份与隔离恢复 | `./scripts/backup-local.ps1 -Label demo-candidate` 保存被忽略的 `.local/backups/intent-20260923-190045-290-demo-candidate.dump`；新建 `intent_demo_restore_20260923` 并 `pg_restore --exit-on-error`，再运行 `backend/scripts/verify_backup_restore.py --source intent --target intent_demo_restore_20260923`：**38 表、1,889 行逐值一致**，RLS 31 表、FORCE RLS 13 表、31 条策略、序列状态一致，schema `0009`；受限应用角色读许可记录成功，无身份及无关身份读不到。归档和恢复库保留供复核。 |

本地浏览器检查使用 Playwright CLI 会话 `demo`；页面快照在被忽略的 `.playwright-cli/`，其中最终跨 Origin 会话见 `page-2026-09-23T10-51-00-332Z.yml`，最终独立页表格及答复见 `page-2026-09-23T10-59-28-627Z.yml` 后的检查快照。开发镜像构建输出包含工具自动生成的制品摘要；项目没有新增任何 SHA256 检验或校验步骤。

## 集成、交付与待验证

集成：`./scripts/dev.ps1 up` 已重建并启动 API、数据库、在线 Worker、接入 Worker 与统一 Web 入口，五个服务健康；最新迁移 `0009`。正式 Web 构建仍不包含开发身份与联调宿主。管理/运行凭据和接口范围分离。历史不可变发布继续按旧策略读取。

交付：本地**工程 Demo 候选**可从 Console 安装和配置，再经独立聊天或开发 iframe 演示规则链；接口与复现步骤见 [Demo 契约](../../../backend/DEMO_CONTRACT.md)。此状态不是“智能 Demo 已通过真实模型验证”，也不是 M1–M3 的真实业务验收或正式发版。

待验证：真实可访问模型的协议、名称、明确版本、服务端凭据到位后，应在 Console 配置并实跑无 Skill 动态计划和规定复合问法。真实银行 20 项、身份内省、对象授权、真实宿主 Origin、封存金标与硬件仍需后续分批提供；真实 HTTP/Dify/HiAgent、第三方 Cookie 限制下的正式宿主、S1/S2/S3、内网离线/备份恢复及性能口径均保持待验证。本次浏览器只证明本地开发宿主。生产候选还须按实际环境执行升级和恢复演练。
