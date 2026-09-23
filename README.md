# 银行内网意图中台

一期 V2.1 方案对应的本地工程：FastAPI API、PostgreSQL Worker、Console 和 Agent Web。软件版本以 `backend/pyproject.toml` 为准，方案版本不代表软件版本。当前开发身份、固定模型、模拟功能和联调宿主不计入银行内网真实验收。

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

API 文档为本地契约查看器，读取本地 OpenAPI，不依赖外部 CDN。

本地选择开发管理员登录，在Console「接入准备」显式准备两个开发夹具；在智能体详情配置对话界面、访问控制及发布嵌入，取得独立页和iframe链接。两套界面由同一个Web入口提供，新智能体不需要新服务。令牌仅存内存，刷新后需可信重新认证；受控会话定位ID不作为凭证。夹具不计入真实功能台账。

日常配置从同一管理平台完成：

1. 在「来源文档」上传接口材料，选择OpenAPI操作并建立功能草稿；在「功能目录」确认来源、映射和正负探测后发布。
2. 需要领域语义时，在「领域管理」发布有限领域定义，再让功能引用明确版本。
3. 在「场景智能体」组合已发布功能，配置对话界面、获准身份集成和宿主，预览并发布，复制Web链接或iframe代码。
4. 在「空间治理」管理服务器批准范围内的成员角色、查看安全引用、收紧限额和查询运行耗时。
5. 「评测诊断」查看运行诊断及本地S1/S2/S3归档；有限A/B/C影子对照使用独立CLI，结果与正常业务端到端分开。

每个应用复用同一聊天模板。草稿发布后才生效，旧会话固定原版本；权限、渠道和集成撤销即时生效，回滚不会恢复撤销的访问。

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

## 启动顺序

先按 M0 盘点真实功能与接口材料、确认负责人和身份规则、落实内网模型与硬件、冻结评测口径。已明确且已授权的独立工作可以并行，受未决信息影响的实现暂不作结论。具体事项见 TASKS.md；不要求用户重新填写一套空白模板。

后端位于 `backend/`，双前端分别位于 `frontend/console/` 与 `frontend/agent-web/`，共用 `frontend/shared/`。实际启动、构建和测试命令登记于 TESTING.md；验证事实由 TASKS.md 链接到对应记录。HTTP、Dify、HiAgent 需分别真实联调；HiAgent 映射适配不代表已验证某一私有部署协议。

规范建设的验证证据见 [GOV-001 验证记录](docs/project/records/GOV-001-validation.md)。
