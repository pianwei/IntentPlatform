# INT-013 治理、限流与恢复工程证据

日期：2026-09-22。任务状态仅在 [TASKS.md](../TASKS.md)。本记录区分治理候选软件、本地合成故障与真实内网验收，未执行项不计通过。

## 治理契约与审查

[GOVERNANCE_CONTRACT.md](../../../backend/GOVERNANCE_CONTRACT.md) 限定可选成员角色、集成、凭据和 resolver 为服务器批准范围。新应用发布固定身份策略，当前撤销独立覆盖旧会话；空间配额由数据库协调，处理时间按尝试和阶段记录，未观测阶段为 null。

独立只读审查发现并闭环：owner RLS 导致空间排队计数漏算其他用户；管理身份委托未按目标空间核验；旧运行会话可进入不再允许原集成的新发布；迟到对账响应缺少 attempt 核对；未调用连接器时仍记录 connector_ms；管理员读取其他用户的计时投影受 owner 限制。修复保留正文 RLS，只开放经过授权的计数/计时投影；审查者复核关闭。

治理冻结候选后端 **267 passed / 116.13s**（1条已有AnyIO告警），治理定向20项通过；前端完整 **76/76**、类型检查、production/development双应用构建通过。OpenAPI为72路径、77操作、93个schema。其后仅耗时表格显示尾修为最多两位小数，保留null和原始详情，8项局部回归及类型检查通过，开发镜像重新构建；这些是独立命令，不能将测试数量相加。

独立0004→0005升级通过。历史升级工具修复为旧阶段使用冻结SQL/反射、验证后再升head，避免最新ORM读取旧字段；新的三个空库分别重跑0001→0002→head、0002→0003→head、0003→0004→head通过。工具不覆盖既有证据，允许指定以`_test`结尾的新独立库。

## 缓存镜像与断外网冷启动

新增入口 `./scripts/verify-offline-local.ps1`、[独立Compose](../../../infra/offline.compose.yaml)、[HTTP验证器](../../../scripts/offline_probe.py)。每次要求新项目名、新数据库卷，拒绝复用既有证据；不下载、不构建镜像，不发布宿主端口，使用 Docker internal 网络。检查后停止验证容器，保留数据，未删除演示或测试库。

首轮实际运行使用冻结 INT-011 后端镜像、INT-011 键序尾修 web 开发镜像；与后续 INT-013 源码尚未构建的改动分开。报告：[intent-offline-20260922-181121](../../../output/offline/intent-offline-20260922-181121/report.json)。

- 新数据库迁移及 API、Worker、web 冷启动成功；网络设置 internal=true，外部 TCP 探测不可达。
- Console/chat/embed 三条入口及 6 个本地静态资源经网关实际加载；这里是 HTTP 检查，不声称浏览器 DOM 或真实宿主握手通过。
- 使用独立开发身份创建 runtime_session，运行令牌访问管理 API 为 403。
- 固定模型与合成功能实际经 Worker 完成 `run_3a4c3acbebd84392a91e41bcad6e7cd3`，一条 call、五条事件；相同消息 ID 返回原 run，两次事件读取相同，没有增加调用。
- 报告总用时 10.15 秒仅为本机这次启动及探测耗时，不是银行 RTO 或模型路由性能指标；`acceptance_evidence=false`。

该验证使用缓存制品、开发身份、固定模型及合成功能；不能替代内网真实模型/身份/接口/宿主、正式制品升级恢复或 RTO/RPO 确认。治理与后续路由版本形成新候选后，需对新冻结镜像另行验证。

## 实际进程故障验证设计

计划在独立测试数据库运行真实 API、Worker 子进程与回环 HTTP 下游。测试屏障只控制终止时点，不增加生产故障开关，不修改租约时间来伪造自然恢复：发送前终止 Worker，恢复后同一 call 仅发送一次；下游已成功而本地未记账时终止 Worker，到期 UNKNOWN 且不重发；事件已提交而 HTTP 响应未送达时终止 API，旧游标重连得到同一事件且不新增调用。

实际工具为`backend/scripts/verify_process_faults.py --run`，只使用`intent_process_faults_test`，普通pytest显式跳过这3项。独立执行两轮均 **3 passed**，35.93秒及36.42秒。报告：[首轮](../../../output/process-faults/20260922T103700Z-ecd4b5aa45/summary.json)、[复跑](../../../output/process-faults/20260922T103857Z-66b504238a/summary.json)、[历史保留核对](../../../output/process-faults/20260922T103857Z-66b504238a/repeatability-check.json)。两轮6个run、6个call及事件原值均保留，没有清库或修改租约时间。

发送前实际终止并自然到期后恢复只写一次；下游成功后本地提交前终止，恢复为UNKNOWN、没有重发；事件入库但响应body未送达时终止API，HTTP客户端实际收到`ReadError`，重启后旧游标取得完全相同事件。屏障属于测试注入，进程终止、事务、网络与重启是真实机制；报告标注合成业务、`acceptance_evidence=false`、`frontend_dom_tested=false`。扫描本次已知令牌与本地数据库秘密值，未出现在归档中；未知业务秘密仍需材料提供方审查。

## 备份身份与隔离恢复

实际 `pg_dump -U intent_owner -d intent_governance_upgrade_test -t workspace_governance` 退出 1：FORCE RLS 拒绝全表导出。保留该失败事实；没有关闭 RLS、给 app/owner 增加 BYPASSRLS，或将失败归档当有效备份。

修正本地 `scripts/backup-local.ps1` 为已有本地管理员 `intent_admin` 执行备份。正式银行环境仍需批准专用备份身份。本地管理权限只用于备份/隔离恢复，API/Worker 继续受限运行。

随后实跑：以管理员导出完成的 `intent_governance_upgrade_test` 到 `/tmp/intent-governance-backup-probe.dump`，创建空 `intent_governance_restore_test`，`pg_restore --exit-on-error` 成功。两个库的 **27 张表、22 行全部原值相等**（直接规范化 JSON 比较，无摘要校验）；版本 0005，workspace_governance/processing_attempts 各一行，ENABLE/FORCE RLS 均为 true。源库、目标库和归档保留，未覆盖演示数据库。

演示库升级前另备份 `.local/backups/intent-20260922-181818-299-before-governance.dump`（当时0004），并保留 `intent-backend:source-import-baseline` 镜像标签；升级前后均 agents=3、releases=5、conversations=9、runs=8、calls=8。0005迁移及API、Worker、web启动健康。随后浏览器新增的合成记录另行计数，不与升级前后比较混用。

## 实际浏览器治理和身份回归

使用冻结INT013后端/前端开发镜像，Chromium153 / Playwright1.63，经同一5173网关操作：

- 获准目录仅显示安全引用和开发标记；空resolver/凭据目录明确展示。成员不能修改自身授权，dev-reader不能勾选服务端未批准的管理角色。
- 限额100/4/8改为2/1/1并保存；另填未保存的3后刷新使用量仍保留3；最终恢复100/4/8，当前revision=2。
- dev-reader执行`governance-reader-001`成功，停用该成员后重新开发登录被拒绝；恢复成员后原会话及结果恢复，只有一条call。run为`run_718a6ffe8f7e4bb3839a987650b46df0`。成员最终有效。
- 管理员页面可查看上述其他用户run的两次处理耗时，只显示计时投影；未经历阶段为“未观测”，不当作零。截图`output/playwright/governance-cross-user-timings.png`已查看；截图为小数格式尾修前。
- 新建专用合成应用`82fb4bae-9ae5-4626-a6f6-c0870539f956`，选择获准development集成后发布v1/v2；v1会话可登录。立即撤销该集成后现有会话不能发消息；切回v1后重新认证仍明确拒绝，数据库该应用run=0。撤销列表仍保留development，当前release为`release_2f89a6a6dd104e79a96df500daba5c3a`。验证结束关闭其Web渠道，原两个演示应用不受影响。
- `localhost:5173`开发宿主嵌入`127.0.0.1:5173`第二演示应用；CDP启用第三方限制并禁用metadata/heuristics豁免、刷新重认证。临时SameSite=None/Secure Cookie第一方可读、iframe不可读，查询`governance-cookie-blocked-001`成功，宿主退出后回到等待身份握手。探针已删除。截图`output/playwright/governance-iframe-blocked-cookies.png`已查看。

Cookie首次仅设置限制时探针仍可读，未计为通过；后续实际探针确认阻断后才计数。自动化一次等待误用按钮名称、一次在恢复尚未稳定时点击未发请求，均修正定位/等待并用实际202响应、结果及数据库确认，不将早期快照计为成功。以上为开发身份、合成功能与本地宿主的工程证据，正式SSO/业务/宿主验收仍待资源。

## 最终组合候选复验

INT-012.3路由和影子指标修复冻结后，后端镜像及含小数显示尾修的web镜像重新构建，`docker compose --profile app up -d --wait --no-build`通过。本地仍只有5173对外Web入口，数据库0005未新增迁移；73条API路径可用，影子开关保持false。更新前备份脚本已实际通过：`.local/backups/intent-20260922-185030-712-before-router-v3.dump`；保留`intent-backend:governance-baseline`镜像。

- 对最终源码再执行专用故障启动器：**3 passed / 35.95s**，[报告](../../../output/process-faults/20260922T105348Z-5a5ad0d429/summary.json)。三个实际下游POST增量仍分别为1，未知写未重放。
- 对最终缓存镜像执行独立断外网冷启动：[报告](../../../output/offline/intent-offline-20260922-185415/report.json)。三入口和6资源通过、运行token拒绝管理、重复消息返回同run、事件原值相等、1call/5events；10.51秒仅为此本机探测，不是银行性能/RTO。验证容器已停止，数据库卷保留。
- 最终浏览器加载新Console后可登录和查询原dev-reader运行，耗时显示431.47/0.6/22.23/456.32ms、未知阶段仍为“未观测”；截图`output/playwright/governance-final-timings.png`已查看。仅图标favicon.ico存在404，不影响入口或业务资源；未宣称控制台零错误。
- 最终检查dev-reader有效，原两个应用chat/embed均启用；两个临时导入/撤销验证应用渠道均关闭。dev-reader验证业务仍只有一条调用。
