# 本地开发与恢复手册

适用：软件 0.1.0 本地开发工程。`compose.yaml` 带开发身份和固定模型，是本地配置。内网部署前必须用实际模型、SSO、可信宿主、域名/TLS、凭据及保留策略形成环境配置并执行验收。

## 启动与停止

在项目根 PowerShell 执行 `./scripts/dev.ps1 up`；只启动数据库用 `./scripts/dev.ps1 database`。首次启动下载官方镜像和依赖，迁移先于 API 和 Worker。查看状态用 `status`，查看最近日志用 `logs`，保留数据停止用 `stop`。

默认Web入口为 `http://127.0.0.1:5173`：`/console/` 管理平台，`/chat/{public_id}` 独立聊天，`/embed/{public_id}` 嵌入页，`/docs` 本地接口说明。API默认只在Compose内部8000通信，不发布第二个Web端口；数据库保留本地调试端口 `127.0.0.1:54329`。变更Web端口必须同步CORS和精确宿主Origin，应用链接由统一入口生成。

脚本使用项目 `.local/docker` 作为 Docker 客户端配置目录。该目录被 Git 忽略，不复制个人 Docker 登录信息。Docker Desktop 管道必须允许当前执行环境访问。

## 直接开发

后端用 `python -m pip install -c backend/constraints.txt -r backend/requirements.txt` 安装到独立虚拟环境，再用 `python -m pip install --no-deps -e backend` 安装项目包后使用：

```powershell
python -m intent_platform.migrate
python -m uvicorn intent_platform.main:app --host 127.0.0.1 --port 8000
# 另一个终端
python -m intent_platform.worker
```

主机方式使用根 `.env.example` 的数据库端口及角色；复制为本地 `.env` 后按实际环境填写，令牌与凭据不进 Git。容器环境由 Compose 注入，以服务名连接数据库。

日常启动使用根目录 `./scripts/dev.ps1 up`，统一构建和运行两份前端。组件开发时可在 `frontend/` 使用各自Vite开发命令，属于开发内部工具而非交付入口；代理目标用 `INTENT_API_PROXY` 指向开发API。Windows和Linux的`node_modules`不能混用；当前机器使用Docker安装依赖，应继续在相同Node容器中构建和测试。

Demo 的模型凭据由数据库 `pgcrypto` 加密。开发启动脚本先以本地数据库管理员在目标库安装扩展，再以受限 owner 运行迁移；已有 0006 库也走同一预备步骤。正式环境由数据库管理员在迁移前安装扩展，不提升应用或迁移账号权限。

`npm run build` 生成正式模式前端，不提供开发身份及联调宿主。`npm run build:development` 显式生成本地演示版本。`infra/frontend.Dockerfile` 默认正式构建，只有本地 Compose 传入开发参数。制品在 `frontend/dist/console` 和 `frontend/dist/agent-web`。

## 身份、连接器与发布

- 本地身份仅有 `dev-admin`、`dev-reader` 和 `dev-other`；最后一个属于不同空间。开发夹具由维护者显式创建。
- 正式身份通过服务端 introspection 核实，数据库成员关系另行预配；生产模式还要求可信业务对象授权服务 `INTENT_OBJECT_AUTHORIZATION_URL`。正式 iframe 票据必须具备可信宿主后端的委托上下文，浏览器不能自行宣称宿主或用户。
- 凭据只用安全引用，不填入功能 JSON 或上传文档。连接目标按配置授权，失败或缺少旧依赖不得自动换成最新版本。
- 草稿编辑、验证、功能发布、AgentRelease 发布为独立动作。新会话固定创建时 release；切换当前 release 只影响新会话，权限撤销与功能停用仍按运行请求重新校验。
- HTTP 和工作流探测会调用下游。写探测要求显式允许副作用，并使用已授权测试数据；确认前的运行请求不发送写业务。
- UNKNOWN 表示执行结果待核对，先按原 `call_id` / 外部执行 ID 查询业务状态，不能通过重发消息代替对账。

具体输入输出以 [公共契约](../../backend/CONTRACT.md) 和启动后的 OpenAPI 为准。Dify/HiAgent 部署版本、错误枚举和固定工作流版本能力须取得真实证据。

新发布的真实模型快照还要求`INTENT_MODEL_VERSION`为内网实际模型版本标识；模型名称或URL不代替权重/部署版本。版本改变后旧依赖必须可用或明确拒绝，不能静默替换。开发fixture使用其固定实现版本，不算真实模型证据。

领域与可信实体绑定使用显式`finite-json-v2`，旧功能不会自动升级。`INTENT_RESOLVERS_JSON`由服务器配置版本化resolver引用、类型、空间和获准目标；`INTENT_WORKSPACE_TIMEZONES_JSON`按空间指定时区，缺省UTC。模型及表单只能提供对象描述/已授权选项，不能自行创建resolver目标或凭据授权。已发布的领域、resolver配置和时间策略由release固定，详细协议见[领域契约](../../backend/DOMAIN_CONTRACT.md)。

最小空间治理契约见 [GOVERNANCE_CONTRACT.md](../../backend/GOVERNANCE_CONTRACT.md)。`INTENT_INTEGRATIONS_JSON` 配置获准身份集成引用、版本、空间、用户、智能体、渠道和宿主范围；内省实际返回的 integration_id 必须匹配。`INTENT_MEMBERSHIP_GRANTS_JSON` 指定某空间时，该空间名单就是完整可信上限；Console 不能另加未知身份、给自己提权或突破角色上限。`INTENT_WORKSPACE_LIMITS_JSON` 定义服务器并发/排队上限，Console 只能收紧。空配置仅使用明确开发集成及既有可信成员基线，不能当正式身份接通。

新应用发布固定所选身份策略；应用级即时集成撤销不能通过重新发布、回滚或续期撤回，需要选择新的服务器批准引用。原已发布会话按兼容策略读取，未知集成拒绝。各阶段计时按真实尝试记录，未运行或未观察的阶段为空；总耗时和配额默认值不是银行性能承诺。

领域功能的新发布选择`router-v3`，向模型发送完整授权候选和去重领域投影；旧v1/v2会话保留原行为。有限影子评测接口默认关闭；需要时在测试API服务配置`INTENT_ALLOW_SHADOW_EVALUATION=true`，通过`backend/scripts/evaluate_shadow.py --help`查看独立CLI。Compose环境需用明确的环境覆盖文件注入，单独修改主机`.env`不自动替换Compose中的固定开发配置。接口要求运行会话所有者兼具有效publisher/admin角色，永久不派发业务；正常端到端评分另用`evaluate.py`，详见[影子契约](../../backend/ABLATION_CONTRACT.md)。

## 配置与发布聊天应用

三层模拟 Demo 可在开发 Console“业务动作与 Skills”点击“安装或核对演示数据”，随后在“场景智能体”打开“三层意图演示助手”。默认一个智能体同时发布 20 项模拟功能和 5 个业务 Skill；独立聊天链接与 iframe 代码在该智能体“发布与嵌入”页。未配置模型时，页面标注为规则链工程预览，可用问题及行为见 [三层 Demo 契约](../../backend/DEMO_CONTRACT.md)。要验证无 Skill 的动态规划，在“模型配置”登记获准的 OpenAI 兼容服务、明确版本和服务端凭据，先执行连接及结构化输出测试，再到智能体“任务策划”选择该配置、保存草稿并发布新版本。真实模型凭据不要写入命令、文档或功能包。

仅在本地开发模式，可于项目根被 Git 忽略的 `.env` 设置独立的 `INTENT_MODEL_SECRET_KEY` 和 `INTENT_ALLOW_PUBLIC_DEEPSEEK_MODEL_TEST=true`，重建 Compose 后在 Console 配置官方 `https://api.deepseek.com/chat/completions`、模型名 `deepseek-flash`、版本标签 `DeepSeek-V4.1-Flash`、思考模式“关闭思考（DeepSeek）”及用户提供的密钥。公网例外只开放给 ModelPort 的该精确地址；运行调用要求发布快照恰为 20 项模拟业务功能，真实连接器仍须私网地址。某些开发网络将官网 DNS 解析到 `198.18.0.0/15` TLS 出口，本地例外仅接受这一专用网段并继续按官网主机名验证证书；其它私有或回环地址仍拒绝。不要输入真实客户或业务数据。官方模型名是随服务更新的别名，版本标签记录本次测试所选产品，不证明远端权重永久固定；正式银行环境仍须改用获准内网模型并完成独立验收。若丢失本地加密主密钥，已有模型凭据将无法解密；保管或轮换时须按环境操作处理。

开发模式新增 `INTENT_MODEL_SECRET_KEY` 用于模型配置的服务端加密；正式部署必须单独设置高强度秘密并安全保存，否则模型配置操作拒绝。停用模型配置会使引用它的旧发布明确不可用，不自动切换。Demo 状态在数据库按空间保存，重复安装不重置已创建工单或已冻结账户；需要新环境应使用新的隔离开发空间，不覆盖现有数据。

在管理平台的智能体详情中配置统一模板。对话界面页填写名称、图标、简介、欢迎语、建议问题、主题色、输入提示和历史侧栏；访问控制页维护用户范围及精确宿主来源；发布与嵌入页选择功能、渠道并发布，复制该应用的Web链接、iframe代码与握手说明。

普通界面、功能组合和宿主来源列表先保存草稿，再发布生效。查看旧会话时仍读取其创建时的发布界面和业务版本。用户授权、渠道即时停用及宿主即时撤销单独保存，重新发布或回滚不会取消这些撤销。关闭全部渠道时匿名应用配置也不可读取。

运行页重新加载后要重新验证身份。浏览器只保存非秘密的应用、运行会话和对话定位ID，运行令牌仅在内存中；定位ID本身不能读取历史。相同用户、应用、渠道及宿主可以续接未注销的运行会话，切换身份应建立新会话。已经显式退出的运行会话不能续活。恢复历史只读取快照和后续事件，不重新提交已受理消息。

本地开发宿主页为 `/console/dev-host.html`，可用 `localhost:5173` 与 `127.0.0.1:5173` 两个不同Origin做浏览器联调，先在应用配置中发布真实宿主Origin。开发宿主用模拟受托身份代替宿主后端；正式环境必须由真实宿主后端申请绑定用户、智能体、Origin、nonce及续接会话的短期一次性票据。iframe HTML只包含页面地址，不能加入密钥或长期令牌。

## 数据与恢复

PostgreSQL 分离管理员、迁移 owner 和运行 app 角色；运行角色无超级用户和 BYPASSRLS 权限。初始化 SQL 的口令仅供本地开发。API/Worker 使用 app，迁移使用 owner，不能交换。

开发数据保存在 `intent-local_intent_pgdata` volume。不要为解决迁移错误删除 volume，也不要把 `docker compose down -v` 当作日常重启。现有用户数据先备份和核对。

本地备份运行 `./scripts/backup-local.ps1 -Label manual`。脚本使用已有本地数据库管理员 `intent_admin` 执行 PostgreSQL `pg_dump`：迁移 owner 受 FORCE RLS 限制，不能用它导出全空间备份，也不能为备份关闭隔离或提升 API/Worker 权限。先将归档写在容器 `/tmp`，再复制到被 Git 忽略的 `.local/backups/`，避免 Windows PowerShell 二进制重定向损坏归档。备份可能包含会话和业务数据，应按受控数据保管；银行正式环境需另行批准专用备份身份。恢复先在新建隔离数据库验证，核对业务记录和权限，再决定是否替换目标数据；不要覆盖唯一数据副本。

生产发布前还需确认：备份位置与访问控制、恢复责任人、RTO/RPO、旧模型和工作流/功能版本保留期、迁移路径及恢复演练。当前没有这些真实确认，不宣称具备生产恢复保障。

本地断外网工程检查可运行 `./scripts/verify-offline-local.ps1`。先构建本次待验证的后端和 web 本地镜像；脚本只使用缓存，在新的 Docker internal 网络和新数据库卷中冷启动，读取三条入口和本地资源，再执行一条合成请求及重复消息/事件恢复检查。它不修改当前演示库，不发布端口；结束后停止验证容器并保留卷与 `output/offline/` 报告。相同名称的既有数据不能覆盖；报告不是正式部署或真实业务验收。

三个精确故障窗口另用backend目录`./.venv/Scripts/python.exe scripts/verify_process_faults.py --run`。先准备受限角色可访问的独立`intent_process_faults_test`库；启动器只准此库，不清理旧证据。它运行真实API/Worker子进程、通过测试屏障定位终止时点、等待自然租约恢复，并记录HTTP事件恢复。普通pytest中的3项默认跳过，不能将其当作已完成故障验证；证据在`output/process-faults/`。

## 常见问题

| 现象 | 检查与处理 |
|---|---|
| Docker 引擎未连接 | 启动 Docker Desktop，检查 `docker info`；客户端可执行不代表引擎可用 |
| 构建器认证连接中断 | 确认网络后先拉取对应官方镜像再构建；不降低 TLS 校验 |
| 主机依赖下载被阻断 | 使用已验证可访问官方软件源的容器路径，不更换未知镜像源 |
| API 不健康 | 检查迁移退出码、数据库角色/URL、`/health` 与 API 日志 |
| 请求一直等待 | 检查 Worker、租约及事件，不重放未知写调用 |
| iframe 无法登录 | 检查精确 Origin、referrer policy、CSP、source、nonce、票据时效及可信委托，不改成通配来源 |
| 正式环境拒绝启动 | 核对开发身份/固定模型已关闭，正式模型和身份服务已配置，不移除启动保护 |

正式宿主、第三方 Cookie 禁用、真实模型、20 项功能、封存评测与离线冷启动需要独立验收。实际测试事实见 [项目任务](../project/TASKS.md) 引用的证据记录。
