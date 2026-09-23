# INT-017 E3 本地候选验证

日期：2026-09-23。等级 D；本记录只证明本地软件、合成负载和隔离恢复。产品运行代码基于 E2 提交 `64d01d2`，E3 可复跑验证脚本与故障夹具固定于 `44464bc`；分支 `codex/INT-017-local-candidate`。后端 `0.1.0` 与前端锁定依赖均未改动。没有远程、CI、正式内网部署或真实验收发布。

## 同一候选的软件与制品

| 检查 | 已执行命令、环境与结果 |
|---|---|
| 完整现有软件套件 | 项目根 `./scripts/verify-local.ps1 all`，Docker PostgreSQL 受限测试库、Node 22 锁定依赖。后端 **364 passed / 3 既有 skipped / 1 既有弃用提示**；前端 **82 passed / 1 既有 skipped**；TypeScript 类型检查、Console 与 Agent Web 正式构建均通过。 |
| 本地候选镜像 | `docker --config .local/docker build -f infra/backend.Dockerfile -t intent-backend:e3-candidate-20260923 .` 和同参数的 `infra/frontend.Dockerfile` → `intent-web:e3-candidate-20260923` 均成功。正式 Web 使用默认 `build`，容器内管理/聊天入口文件存在、开发宿主页不存在，`nginx -t` 通过。镜像仅保存在本机；未推送或部署正式环境。 |
| 0005→0006 | 在新库 `intent_ingest_e3b_20260923_test` 运行 `backend/scripts/verify_ingest_upgrade.py`：旧 6 表逐值保留，新接入表 FORCE RLS、受限 app 可按获准身份访问、无身份及无关用户不可读。首个尝试因验证夹具在 0005 的 FORCE RLS 表写入前未设空间身份而停止在迁移前；修正脚本后改用新库重跑成功，原库保留。 |
| 历史升级 | 四个全新隔离库分别重跑 `verify_upgrade.py`、`verify_domain_upgrade.py`、`verify_import_upgrade.py`、`verify_governance_upgrade.py`：0001→0002、0002→0003、0003→0004 均再升 head 验证运行兼容，0004→0005 验证旧记录与新治理边界，全部通过；旧发布快照、会话、调用、来源和权限的相应不变量保持。治理升级脚本现允许通过 `INTENT_UPGRADE_TEST_DATABASE` 选择新 `_test` 库，不覆盖旧证据。 |
| 三故障窗口 | `backend/.venv/Scripts/python.exe scripts/verify_process_faults.py --run`，独立 `intent_process_faults_test`，最终 **3 passed / 36.15s**；发送前退出、下游成功但本地未记账、事件提交后未送达均使用实际子进程及回环 HTTP，每例运行期下游 POST 为 1。报告见 [进程故障结果](../../../output/process-faults/20260923T073545Z-21d199a71b/summary.json)。首次运行因旧合成写功能夹具缺少 E1 新输出契约而在发布前失败；补齐夹具后复跑成功，未放宽产品门禁。 |
| 备份与隔离恢复 | `./scripts/backup-local.ps1 -Label e3-candidate` 导出开发库到被忽略的 `.local/backups/`；使用管理员在新库 `intent_e3_restore_20260923` 执行 `pg_restore --exit-on-error`。`backend/scripts/verify_backup_restore.py` 逐值比较 **28 表、1,533 行**，RLS **21** 表、FORCE RLS **3** 表、21 条策略和序列状态一致，schema 为 0006；恢复库受限 app 身份读取获准接入任务成功，无身份及无关身份读取为 0。源库、目标库和归档保留。 |
| 断外网冷启动 | `./scripts/verify-offline-local.ps1 -RunName intent-offline-e3-20260923-01` 使用缓存本地开发镜像、新 internal 网络和新卷，不拉取、不构建、不开放宿主端口。外部 TCP 不可达；console/chat/embed 及 6 个静态资源、限定运行令牌隔离、单次业务执行、消息与事件恢复、独立接入 Worker 完成合成文档任务均通过。报告见 [离线结果](../../../output/offline/intent-offline-e3-20260923-01/report.json)；验证容器停止，数据保留。 |

合成负载在 [INT-017 负载记录](INT-017-load.md) 中：5 个持续在途会话流、8 次固定到达、12 轮长历史、6 个并行接入任务和 8 次限流竞争均通过。合成端到端时延不能作为银行模型/硬件路由时延验收；开发空间限额恢复 `100/4/8`，临时调整与恢复保留审计记录。

历史升级隔离库依次为 `intent_e3_app_upgrade_test`、`intent_e3_domain_upgrade_test`、`intent_e3_import_upgrade_test`、`intent_e3_governance_upgrade_test`，新增迁移使用 `intent_ingest_e3b_20260923_test`；每个脚本均拒绝覆盖已存在的 schema。恢复使用 `.local/backups/intent-20260923-153725-941-e3-candidate.dump`，先创建 `intent_e3_restore_20260923` 空库，再在 PostgreSQL 容器执行 `pg_restore -U intent_admin -d intent_e3_restore_20260923 --exit-on-error /tmp/intent-20260923-153725-941-e3-candidate.dump`。逐值比对脚本只读，要求通过环境变量传入本地管理员连接配置；归档未进入 Git。

## 四项事实与剩余条件

产出：候选验证脚本、升级和恢复复跑入口、独立接入 Worker 离线探针、生产模式本地镜像。验证：上述完整现有软件、合成负载、历史与新增迁移、三故障窗口、全表恢复及断外网冷启均有实际结果。集成：E0–E2 本地产品代码对应 `64d01d2`，E3 验证代码对应 `44464bc`；本地开发栈仍可运行，候选镜像未发布。交付：E3 **本地工程候选完成**，不是 M1–M3 的真实退出或内网上线。

尚未执行：真实 HTTP/Dify/HiAgent 实例的成功/失败/状态兼容、两项首批真实功能和累计 20 项、真实模型/身份/宿主 iframe、封存 S1/S2/S3、独立本体消融、真实硬件负载、RTO/RPO 和获准目标环境恢复。它们分别依赖用户和资源方提供材料、访问条件及业务确认，保持“待验证”，不得以本地夹具或上述秒数判通过。
