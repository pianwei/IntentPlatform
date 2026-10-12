# 银行内网意图中台

当前产品范围为“智驭业务助手”：14个模拟业务场景、26项原子功能、六条组合规则、有限自主规划、模型问答及服务端受控模拟范围。复用 FastAPI、PostgreSQL Worker、Console 和 Agent Web；使用方式见[智驭说明](docs/project/ZHIYU_DEMO.md)，最新功能配置与文档Skill改造依据[批准基线](方案/功能配置与文档Skill实施基线_2026-10-10.md)，实际交付与验证状态见[TASKS](docs/project/TASKS.md)。软件版本以 `backend/pyproject.toml` 为准。模拟业务、官网模型及本地宿主不计银行真实验收，R1–R3暂缓。

## 本地启动

启动 Docker Desktop 后，在项目根 PowerShell 执行：

```powershell
./scripts/dev.ps1 up
```

首次启动会下载依赖、构建镜像、迁移数据库，再启动 API、Worker 和双前端。

| 入口 | 地址 |
|---|---|
| 管理平台 | http://localhost:5173/console/ |
| 独立聊天 | 管理平台发布后生成 `/chat/{public_id}` |
| iframe 聊天 | 管理平台发布后生成 `/embed/{public_id}` 及集成代码 |
| API 文档 | http://localhost:5173/docs |
| OpenAPI | http://localhost:5173/openapi.json |

本机当前已发布的[智驭业务助手](http://127.0.0.1:5173/chat/5d019540-83ca-489f-bc38-441dc0a64db3)可直接体验；[示例宿主](http://127.0.0.1:5175)展示Key换票嵌入。旧演示入口已退役，不会跳转或接管到新会话。

API 文档为本地契约查看器，读取本地 OpenAPI，不依赖外部 CDN。

默认使用本地账号登录Console。首次部署先在服务端配置 `INTENT_PLATFORM_ADMINS_JSON`，再运行无默认密码的管理员初始化命令；平台管理员可建号、重置和停用。操作见[本地运行手册](docs/operations/LOCAL_RUNBOOK.md#身份连接器与发布)。独立Chat匿名访问，iframe由宿主后端持用户Key换一次性票据，每位访客独立会话。两套界面共用Web入口，新智能体不需要新服务。

日常配置从同一管理平台完成：

1. 在「功能库」通过三步居中弹窗创建直接调用或组合功能，填写输入输出、实体动作映射和执行配置；可随时展开工作页，保留输入与步骤。接口材料为可选附件，保存并验证后显式发布。
2. 在「智能体」的创建弹窗选择已发布功能和模型；补建功能或模型后返回原编辑上下文。模型保存与测试分开记录，测试失败可继续使用已保存配置或重试测试。
3. 智能体工作台固定保存、试聊、发布入口；外观与访问设置独立成页签。试聊明确使用已发布版本和限定应用的运行身份，未发布修改不进入试聊。
4. 空间切换器进入当前空间设置和成员；平台管理员从「平台管理」进入空间目录或账号管理。成员列表可直接修改角色，详情解释有效权限。
5. 在「运行日志」选择会话和问答轮次，先查看最终答复，再展开阶段定位问题。开发评测归属智能体详情；既有影子对照CLI继续独立使用。
6. 「个人中心→偏好设置」分别调整字号与密度；Chat菜单独立调整该浏览器、该应用的字号。Chat新会话在首次发送时建立服务端记录，刷新与重连只恢复已受理业务。

每个应用复用同一聊天模板。草稿发布后才生效，旧会话固定原版本；权限、渠道和集成撤销即时生效，回滚不会恢复撤销的访问。

空间可重命名、归档和恢复。归档立即停止运行访问及未发送任务，保留获准管理员的历史只读查询；恢复后用户需重新进入，旧票据与恢复凭据不会复活。没有永久删除空间或自定义角色入口。

头像菜单提供个人资料、账号ID、平台/空间权限、密码与集成Key。运行日志按会话汇总，标题可以重复；每轮先看输入、最终输出、状态与耗时，再展开阶段。功能表单按“基本信息与执行→输入输出与映射→验证与发布”组织，草稿保存不等于发布。旧身份的发布和会话仅供获准管理人员只读；新local身份发布复制原业务依赖，不接管旧会话。

访问token只存内存；Console用HttpOnly刷新Cookie恢复，匿名Chat仅按应用保存专用恢复凭据。Key不进入iframe URL或浏览器存储。可运行的宿主后端样例见 [examples/iframe_host.py](examples/iframe_host.py)，使用范围与服务器配置见运行手册。

`./scripts/dev.ps1 status` 查看状态，`logs` 查看日志，`stop` 保留数据停止。详细配置与恢复边界见 [本地运行手册](docs/operations/LOCAL_RUNBOOK.md)，公共类型和接口见 [CONTRACT.md](backend/CONTRACT.md)。

## 阅读与使用

- 产品设计：[方案入口](方案/README.md)。
- Agent 执行入口：[AGENTS.md](AGENTS.md)。
- 当前依据与缺口：[项目事实](docs/project/BASELINE.md)、[待决策](docs/project/DECISIONS.md)。
- 启动到交付：[需求与验收](docs/project/REQUIREMENTS.md)、[阶段计划](docs/project/ROADMAP.md)、[任务记录](docs/project/TASKS.md)。
- 日常开发：[工作规则](docs/project/WORKFLOW.md)、[验证说明](docs/project/TESTING.md)。
- 真实功能接入：[FUNCTIONS.md](docs/project/FUNCTIONS.md)。

## 四个项目级 skills

| Skill | 可直接发出的请求示例 |
|---|---|
| `intent-project-planning` | 使用 `$intent-project-planning`，检查 M0 启动条件，整理缺失材料并更新首批任务。 |
| `intent-task-delivery` | 使用 `$intent-task-delivery`，根据现有任务和证据继续当前已授权的开发工作。 |
| `intent-quality-gate` | 使用 `$intent-quality-gate`，评审本次变更并执行与影响范围匹配的验证。 |
| `intent-release-handoff` | 使用 `$intent-release-handoff`，整理当前版本的移交材料和未满足的发布条件。 |

技能保存在项目的 `.agents/skills/`，保留默认的隐式选择能力，也可指定技能名称。该位置及加载方式按 [OpenAI 官方技能说明](https://learn.chatgpt.com/docs/build-skills) 核对。若客户端未显示新技能，重新打开项目或重启客户端；也可直接要求读取 AGENTS.md 中链接的 SKILL.md。本次文件验证与显式调用演练不代表已验证所有客户端的自动触发行为。

这些技能在执行任务时维护项目记录。尚未配置定时巡检、外部看板、CI 或生产监控，不存在离线后自动持续管理的承诺。

## 银行真实接入的启动顺序

先按 M0 盘点真实功能与接口材料、确认负责人和身份规则、落实内网模型与硬件、冻结评测口径。已明确且已授权的独立工作可以并行，受未决信息影响的实现暂不作结论。具体事项见 TASKS.md；不要求用户重新填写一套空白模板。

后端位于 `backend/`，双前端分别位于 `frontend/console/` 与 `frontend/agent-web/`，共用 `frontend/shared/`。实际启动、构建和测试命令登记于 TESTING.md；验证事实由 TASKS.md 链接到对应记录。HTTP、Dify、HiAgent 需分别真实联调；HiAgent 映射适配不代表已验证某一私有部署协议。

规范建设的验证证据见 [GOV-001 验证记录](docs/project/records/GOV-001-validation.md)。
