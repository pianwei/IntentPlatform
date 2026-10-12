# INT-031 功能配置精简与业务目录本地交付记录

日期：2026-10-12。等级D。验证对象为本工作区当前未提交候选及本地镜像，不代表提交、生产发布或银行验收。实施范围依据[批准基线](../../../方案/功能配置精简与业务目录实施基线_2026-10-10.md)。

## 交付内容

- 增加版本化平台默认业务对象/动作目录和空间自定义目录，支持显式采用默认版本、维护、停用、按已发布引用查看对象→动作→直接功能→组合功能关系。
- 新功能支持无输入参数、无业务数据输出及HTTP 204；空输出仅可记录已判定状态，不向事件、文件、模型和用户投影泄露未批准的原始响应。
- 手工创建不展示无意义来源审查；真实导入待核对事项仍保留服务端校验及发布门禁。功能、智能体与目录版本/变更入口按真实版本及修订关联。
- 用户聊天以获准Markdown结果为主，避免重复详情；保留可读结果、表格、引用、文件和必要交互。精简Console与聊天界面的字号、间距和状态层次。
- 不改写旧AgentRelease、运行会话、模型配置或账户/空间权限；本地应用链接保持`/console/`、`/chat/{public_id}`及`/embed/{public_id}`。

## 验证记录

| 范围 | 结果 | 证据 |
|---|---|---|
| 后端完整测试 | 753通过，3个需隔离实际进程的故障用例跳过，2条警告。3个故障用例另以真实独立进程3/3通过。 | [`pytest.log`](../../../output/INT-031/backend-20261012-094520/pytest.log)、[JUnit](../../../output/INT-031/backend-20261012-094520/results.xml)、[进程故障测试](../../../output/INT-021/identity-20261012-101316/pytest.log) |
| 前端完整测试 | 41个文件、282通过；1个需另生成PostgreSQL运行归档夹具的条件测试未运行。源码复核定向集14/14通过。 | [`frontend-full-final.log`](../../../output/INT-031/integration-checks/frontend-full-final.log)、[`frontend-source-review.log`](../../../output/INT-031/integration-checks/frontend-source-review.log) |
| 正式与开发构建 | 类型检查及双应用两种构建均通过。Console JS chunk 778.14kB，保留大于500kB的构建提示；依赖注释提示不影响产物。 | [`frontend-build.log`](../../../output/INT-031/integration-checks/frontend-build.log)、[`frontend-build-dev.log`](../../../output/INT-031/integration-checks/frontend-build-dev.log) |
| 真实官网模型开发集 | DeepSeek V4.1 Flash，非思考模式；106/106通过：单场景70、复合12、自主规划12、模型兜底12。评测仅使用登记的智驭合成目录，报告明确`bank_acceptance=false`。 | [106题汇总](../../../output/INT-031/upgrade-20261012-100958/model-106/report.json)、逐题报告目录[`model-106`](../../../output/INT-031/upgrade-20261012-100958/model-106) |
| 0017→0018升级恢复 | 从保存的0017备份在隔离PostgreSQL恢复并执行迁移；44张旧表的1560行逐行一致，12条发布保留，45张既有RLS表及平台/空间隔离检查通过；默认目录为6个动作、7个对象。 | [升级报告目录](../../../output/INT-031/upgrade-20261012-100958)，[备份](../../../.local/backups/intent-20261012-100248-162-int031-pre-upgrade.dump) |
| 离线冷启动 | 候选镜像在禁止外连的隔离网络通过静态资源、Console/Chat/Embed路由、一次合成调用、重复消息、事件恢复及接入Worker检查；`acceptance_evidence=false`，仅算本地工程证据。 | [`report.json`](../../../output/offline/intent-offline-int031-20261012/report.json) |
| 本地主服务更新 | 主数据库迁移0018成功，API、Worker、接入Worker和Web重建健康；`/health`返回数据库可用、development及fixture模式；Console/Chat/Embed路由、资源及公开应用描述符在切换日志中均为HTTP 200。主PostgreSQL和持久卷未重建。 | [迁移日志](../../../output/INT-031/cutover-20261012/migration.log)、[服务更新日志](../../../output/INT-031/cutover-20261012/services-up.log)、[HTTP检查](../../../output/INT-031/cutover-20261012/http-smoke.log)、[实时健康检查](../../../output/INT-031/cutover-20261012/health-final.log) |
| 浏览器检查 | Playwright桌面检查保存Console登录及1440×900聊天截图；另在隔离IAB页以1280×720查看聊天欢迎页，只有配置的3条建议问题，无模拟角色选择或业务功能菜单；没有发送消息。直接访问Embed提示需已登记可信宿主，符合保护预期。 | [Console登录页](../../../output/INT-031/cutover-20261012/browser/console-login.png)、[Chat桌面页](../../../output/INT-031/cutover-20261012/browser/chat-1440x900.png) |

## 未完成及适用边界

- 390/768视口、三档字号、键盘/焦点操作及原生浏览器200%缩放未在本轮实测。Playwright CLI默认OS用户数据目录无写权限，改用工作区目录启动后会话仍直接关闭；IAB控制接口未提供视口覆盖。本轮只记录为待补验，没有用CSS静态检查代替实际视口结果。
- 前端有一项条件归档测试因本轮没有生成其专用评测归档夹具而跳过；完整后端中3个进程故障用例的专用测试已另行3/3通过。
- 本轮Embed检查只验证入口和无可信宿主时的拒绝，未重新执行真实外围宿主的Key换票握手。真实宿主、行内身份/权限、银行接口与硬件验收均未完成；INT-005至INT-007所涉R1–R3继续搁置。
- 离线、模拟业务及官网模型开发集不计入真实业务功能数量；本地部署不等于生产部署。
- 正式构建提示Console JS chunk 778.14kB，后续可按独立性能任务做代码拆分，本轮没有改变验收口径以消除提示。

## 主服务保护与清理

- 升级前备份：`.local/backups/intent-20261012-100248-162-int031-pre-upgrade.dump`；主服务切换前备份：`.local/backups/intent-20261012-102551-557-int031-pre-cutover.dump`。两份均保留。
- 本地回退标签：`intent-backend:pre-int031-20261012`、`intent-web:pre-int031-20261012`；候选标签：`intent-backend:int031-20261012`、`intent-web:int031-20261012`。Compose仍使用`:local`别名。代码回退和数据库恢复必须分别规划，不应只回退镜像而保留不兼容的数据库状态。
- 后端/模型升级、前端构建、进程故障及离线冷启动使用的独立容器、网络和测试卷在保存报告后已按各自收尾日志删除；离线报告另证明容器、卷和网络清理完成。未对镜像执行全局清理，不触碰主PostgreSQL卷、用户数据、备份或历史报告。
- 工作区保留既有未提交改动；未提交、推送、生产部署或执行业务写入。

## 项目记录与Skills

- 唯一动态状态写入[ TASKS.md 的 INT-031 条目](../TASKS.md#int-031-功能配置精简与业务目录维护)。`BASELINE.md`记录数据库/本地候选事实，[LOCAL_RUNBOOK.md](../../operations/LOCAL_RUNBOOK.md)记录入口、版本与恢复边界，[TESTING.md](../TESTING.md)记录可重用命令和验收规则。
- 四个项目Skills已包含目录/无输出版本规则、实际执行证据、权限与撤权边界、迁移与恢复约束；Skills不写本任务动态进度，本轮无需新增稳定规则。
- 本轮未产生SHA256相关检验；R1–R3继续搁置。
