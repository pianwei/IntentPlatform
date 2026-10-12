# 本地开发与恢复手册

适用：软件 0.1.0 当前本地工程。`compose.yaml` 默认local账号身份，开发模型与模拟数据仍是本地配置。内网部署前必须用获准模型、账号/空间授权、可信宿主、域名/TLS、凭据及保留策略形成环境配置并执行验收；旧SSO运行入口已退出当前产品身份路径。

## INT-023 界面操作

新建智能体、功能及模型在居中任务弹窗进行；功能可展开工作页继续，不需先复制表单。完成前两步后在第三步“保存并验证”，验证通过再明确发布。模型“仅保存”和“保存并测试”结果分开，失败可继续测试当前版本；从智能体补建后使用返回按钮选中新资源。

头像→个人中心可分别设置字号和列表密度；聊天显示设置仅影响当前浏览器的该应用。试聊入口运行已发布版本并显示其版本信息；若后台版本资料不可读则显示未记录，不猜测。聊天新建不会立即产生服务端空记录，首次发送才创建，刷新不会重发业务。

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

## 聊天入口排查

当前本机应用为[智驭业务助手](http://127.0.0.1:5173/chat/5d019540-83ca-489f-bc38-441dc0a64db3)，数据库0018，发布v5（scope-v10）；[示例宿主](http://127.0.0.1:5175)保留原Key和目标应用。六条组合使用文档Skill及固定子功能，新会话生效；旧v4/模板和已受理计划不改写。旧模拟助手及其会话已按INT-026清单退役，旧Chat地址网关拒绝、应用查询返回404是预期结果。请使用现有智驭链接。

## INT-031 本地候选与恢复

2026-10-12本地候选已将数据库从0017迁移到0018，并更新API、Worker、接入Worker和Web。Compose继续使用`intent-backend:local`、`intent-web:local`别名；候选标签为`intent-backend:int031-20261012`、`intent-web:int031-20261012`，更新前镜像另留`intent-backend:pre-int031-20261012`、`intent-web:pre-int031-20261012`。迁移备份为`.local/backups/intent-20261012-100248-162-int031-pre-upgrade.dump`，切换前备份为`.local/backups/intent-20261012-102551-557-int031-pre-cutover.dump`。实际迁移、镜像构建、HTTP检查及恢复范围见[INT-031交付记录](../project/records/INT-031-business-catalog-delivery.md)。

如需回退，先停止入口与派发并盘点在途运行/调用，再在独立数据库恢复并验证匹配备份；代码镜像回退与数据库恢复是两个独立动作。不要只把旧镜像标签指回0018数据库，也不要在未核验旧应用渠道、Key、匿名恢复凭据及撤权状态时重新开放入口。主库与`intent-local_intent_pgdata`不是清理对象；不使用全局prune。当前健康端点为`http://127.0.0.1:5173/health`，`fixture_mode=true`仅表示本地演示环境，不能作为真实业务或银行验收证据。

## INT-029 功能配置、测试与恢复

功能编辑顶部提供保存草稿、测试与发布。先维护业务目标、实体和动作，再配置有限类型的输入输出字段；参数与对象关联用于可信识别/授权，不配置不存在的识别服务。实体、动作目录按来源分组，历史内部版本不作为创建时的选择项。输出测试案例只保存获准投影，不改变执行修订。

组合只选择已发布的直接子功能并填写业务介绍和Markdown Skill；文档说明目标、执行规则及回答要求。保存会固定所需Skill版本。文档匹配后模型生成本轮有限计划，校验通过才执行；没有匹配规则时使用智能体显式允许的直接能力自主规划。修改文档或子功能后必须重新测试，不以静态验证代替真实执行。

测试沿真实连接器、Worker和账本运行。需写入时，先查看实际解析的对象及参数，再明确确认同一测试运行；测试环境允许副作用不等于业务确认。HTTP来源要求正常及预期失败探测均有确定账本事实；UNKNOWN、超时和传输错误不能充当业务失败证据，也不能自动重发写操作。不同获准维护者可测试、发布分工，但不能查看另一作者的私有测试结果；发布前仍核验原作者当前权限。

INT-029切换前备份保留在`.local/backups/intent-pre-int029-20261010.dump`，隔离恢复已验证；旧代码镜像为`intent-backend:pre-int029-20261010`、`intent-web:pre-int029-20261010`。当前镜像为`intent-backend:int029`、`intent-web:int029`，主Compose使用`:local`别名。代码回退和数据恢复分开处理；不将旧Web与不兼容API混用，不通过回退撤销身份与渠道保护。0017仅增加私有作者测试隔离，原业务发布、账号、模型和加密凭据保留。执行恢复前先停入口及派发、在独立库核对，并保留现有撤权事实。

管理列表的“已发布”表示存在发布记录，不代表实时聊天渠道仍开启。打开/试聊入口同时依据已发布渠道和实时渠道判断；关闭后显示“聊天已关闭”。访问已关闭渠道的旧链接仍可能收到网关403，这不代表服务停止，不应通过关闭权限检查来恢复访问。需要重新启用时，由有权限的维护者核对该应用的访问配置和当前发布版本。

正常聊天页面若提示“此前的会话无法恢复”，可以明确选择“放弃恢复，重新开始”。匿名访客默认有效期7天，过期或撤销后的恢复凭据不能继续使用；重新开始只建立该浏览器、该应用的新访问，不恢复旧身份或重新提交旧业务。浏览器的其他应用访问不受影响。

若保留的非演示应用提示“发布版本使用旧版访问配置”，属于发布兼容问题，重新连接不能解决。维护者必须先核对功能停用、出站授权和固定依赖，再决定创建新的本地身份发布；现有`migrate-identity`只复制业务快照并切换身份策略，不会恢复已停用功能或接口授权。不要将其视为一键恢复业务。“三层意图演示助手”和“HTTP文档联调 · 合成应用”均已随INT-026旧目录退役，不应再迁移或重新初始化。

## INT-026 本地切换与恢复

维护时停止Web、API和两个Worker，确认无在途运行/调用/接入任务。切换前备份为`.local/backups/intent-20261009-192331-487-INT026-cutover.dump`，已隔离恢复并逐值验证44表22864行、RLS、序列与受限角色。备份中包含结果文件二进制，不需要另删用户目录。清单与完成记录位于`output/INT-026/main-retirement-manifest.json`、`main-retirement-applied.json`；清单内数据零残留，事务恢复所有不可变保护，非目标记录原值不变。

候选镜像为`intent-backend:int026`、`intent-web:int026`；切换前实际运行镜像保留为`intent-backend:pre-int026-20261009`、`intent-web:pre-int026-20261009`。主Compose继续使用`:local`标签；不能仅切旧镜像而继续使用不兼容数据库。恢复时先停入口和派发，将备份恢复至独立库核查，再按授权恢复主库及配套代码。保持退役应用渠道关闭、旧运行身份撤销，核对当前账号、Key和成员撤权后才开放新访问；不得让备份复活旧票据/匿名恢复凭据。

主示例宿主容器`intent-local-example-host`使用`.local/int021-host.env`，仅在本地5175监听，Key不进入浏览器。当前两个获准Origin为`http://127.0.0.1:5175`和`http://localhost:5175`。不要把它或主数据库当测试资源清理；测试资源归属和证据见INT-026报告。

## INT-021 独立界面审核预览

访问 `http://127.0.0.1:5173/console/design-preview/`，使用内置模拟数据。可审核智能体首页、会话日志、功能库中的“新建功能”与余额示例，以及头像下的个人中心。预览保存的草稿只保留在当前页面内存中，返回后可继续编辑，刷新即清除。该入口没有启用新账号、公开Chat或用户Key，不输入真实凭据。

预览代码与正式双应用分离，`build`及`build:development`不包含它。重建Web容器后预览静态目录会消失，需重新执行以下命令部署本地审核预览；不重启容器，不覆盖Console入口：

```powershell
docker --config .local/docker run --rm --network none -v D:/pianwei/work/intent/frontend:/work -w /work node:22.16.0-alpine npm run build:preview
docker --config .local/docker exec intent-local-web-1 mkdir -p /usr/share/nginx/html/console/design-preview
docker --config .local/docker cp frontend/dist/preview/. intent-local-web-1:/usr/share/nginx/html/console/design-preview/
```

命令适用已缓存镜像和Linux依赖的本机，仅用于复看已审核的历史预览。实现说明见[预览README](../../frontend/preview/README.md)，第一包证据见[预览记录](../project/records/INT-021-interaction-preview.md)。用户已审核并授权后续全部实现；正式产品按下文local身份操作，部署与完整候选证据由TASKS归并。

## 身份、连接器与发布

默认 `INTENT_IDENTITY_MODE=local`、`INTENT_ALLOW_DEV_AUTH=false`。平台管理员的稳定user_id来自服务端 `INTENT_PLATFORM_ADMINS_JSON` 数组；Console不能修改平台管理员配置。空数组不授予任何平台管理权，开发模式不再提供默认管理员账号或密码。旧目录、SSO集成与名册配置不是local身份源；`legacy-test`仅允许显式 `INTENT_ENV=test` 的旧回归夹具。

首次初始化：配置管理员ID、迁移数据库并启动服务后，在项目根执行以下命令，由命令交互读取并确认至少12字符的密码，不把密码写入命令历史：

```powershell
$adminSubject = Read-Host '输入已配置的平台管理员稳定ID'
docker --config .local/docker compose exec api python -m intent_platform.bootstrap_account --user-id $adminSubject --username admin --display-name '平台管理员'
```

初始化命令不重置已有账号，也不自动添加空间成员。需要关联已有管理记录时显式指定已确认的旧user_id；这不恢复旧令牌或旧会话。平台管理员在“平台管理→账号管理”中创建账号，或重置账号后安全交付仅返回一次、1小时有效的设置凭证；用户在登录页“首次使用或管理员已重置密码”中设置密码，再登录。不得将设置凭证、密码或Key写入报告。

平台管理员可创建空间并分配管理员；分配给他人不会使创建者自动获得该空间业务权限。空间管理员从活跃本地账号目录选择成员，角色仍受已保存上限约束，成员撤销即时生效。个人中心允许显示名、界面密度和改密，并显示账号ID及服务端判定的权限；不能自行提升角色。

- 密码按Argon2id保存（至少19456 KiB、time_cost=2、parallelism=1）。普通改密要求当前密码、使密码会话失效并保留Key；管理员重置撤销Key；停用使账号、Key及派生运行身份失效。已发送业务结果不宣称撤回。
- Console访问token仅内存，刷新通过HttpOnly、SameSite=Strict且限auth路径的Cookie轮换，生产加Secure；写请求校验Origin。旧SSO `/auth/exchange`、开发 `/dev/login` 在local模式拒绝。
- 生产真实业务仍要求可信对象授权服务 `INTENT_OBJECT_AUTHORIZATION_URL`。iframe使用宿主后端Key与visitor绑定，浏览器不能自行宣称用户或宿主身份。
- 凭据只用安全引用，不填入功能 JSON 或上传文档。连接目标按配置授权，失败或缺少旧依赖不得自动换成最新版本。
- 草稿编辑、验证、功能发布、AgentRelease 发布为独立动作。新会话固定创建时 release；切换当前 release 只影响新会话，权限撤销与功能停用仍按运行请求重新校验。
- HTTP 和工作流探测会调用下游。写探测要求显式允许副作用，并使用已授权测试数据；确认前的运行请求不发送写业务。
- UNKNOWN 表示执行结果待核对，先按原 `call_id` / 外部执行 ID 查询业务状态，不能通过重发消息代替对账。

具体输入输出以 [公共契约](../../backend/CONTRACT.md) 和启动后的 OpenAPI 为准。Dify/HiAgent 部署版本、错误枚举和固定工作流版本能力须取得真实证据。

新发布的真实模型快照还要求`INTENT_MODEL_VERSION`为内网实际模型版本标识；模型名称或URL不代替权重/部署版本。版本改变后旧依赖必须可用或明确拒绝，不能静默替换。开发fixture使用其固定实现版本，不算真实模型证据。

领域与可信实体绑定使用显式`finite-json-v2`，旧功能不会自动升级。`INTENT_RESOLVERS_JSON`由服务器配置版本化resolver引用、类型、空间和获准目标；`INTENT_WORKSPACE_TIMEZONES_JSON`按空间指定时区，缺省UTC。模型及表单只能提供对象描述/已授权选项，不能自行创建resolver目标或凭据授权。已发布的领域、resolver配置和时间策略由release固定，详细协议见[领域契约](../../backend/DOMAIN_CONTRACT.md)。

最小空间治理契约见 [GOVERNANCE_CONTRACT.md](../../backend/GOVERNANCE_CONTRACT.md)。local模式只使用本地账号目录、数据库Membership及其角色上限；`INTENT_INTEGRATIONS_JSON` / `INTENT_MEMBERSHIP_GRANTS_JSON`仅保留给显式历史测试。`INTENT_WORKSPACE_LIMITS_JSON`定义服务器并发/排队上限，Console只能收紧；公开Chat不建立成员身份，也服从空间运行配额。

新应用发布固定local-v1身份策略；应用即时撤销不能通过重新发布、回滚或续期清除。旧身份发布不能直接匿名启动：获准publisher可调用 `POST /api/v2/management/workspaces/{ws}/agents/{aid}/migrate-identity`，传当前`revision`，新建仅替换身份策略的发布，原业务依赖完整复制。旧发布/会话保持原值并只通过管理日志读取，不按旧ID自动接管。各阶段计时按真实尝试记录，未运行或未观察的阶段为空；总耗时和配额默认值不是银行性能承诺。

领域功能的新发布选择`router-v3`，向模型发送完整授权候选和去重领域投影；固定的旧v1/v2业务策略不会被改写，但旧身份会话在local模式仍仅管理只读。有限影子评测接口默认关闭；需要时在测试API服务配置`INTENT_ALLOW_SHADOW_EVALUATION=true`，通过`backend/scripts/evaluate_shadow.py --help`查看独立CLI。Compose环境需用明确的环境覆盖文件注入，单独修改主机`.env`不自动替换Compose中的固定开发配置。接口要求运行会话所有者兼具有效publisher/admin角色，永久不派发业务；正常端到端评分另用`evaluate.py`，详见[影子契约](../../backend/ABLATION_CONTRACT.md)。

## 配置与发布聊天应用

已有演示数据从“智能体”打开“三层意图演示助手”，不反复安装或重置；新环境先由获准操作者导入合成目录，不能通过重新开放旧登录来获得业务权限。同一演示智能体启用20项模拟基础功能与5条规则。独立链接和iframe代码在“发布与嵌入”页。主导航为智能体、功能库和运行日志，设置保留空间管理及模型配置；实体映射、接口资料及组合Skill仍在功能内。新建采用动态三层，发布前检查依赖和问题。

未配置模型时明确标注工程预览，问题及行为见 [Demo契约](../../backend/DEMO_CONTRACT.md)。验证自主规划时，在“设置→模型配置”登记获准OpenAI兼容服务、明确版本及服务端凭据，执行连接和结构化输出测试，再到智能体“任务策划”选择、保存并发布。用户页仅显示最终答复及必要交互，中间步骤和三层事实在管理员“运行日志”查看。真实模型凭据不要写入命令、文档或功能包。

仅在本地开发模式，可于项目根被Git忽略的 `.env` 设置独立 `INTENT_MODEL_SECRET_KEY` 和 `INTENT_ALLOW_PUBLIC_DEEPSEEK_MODEL_TEST=true`，重建Compose后在Console配置已获准官网模型；本轮沿用 `https://api.deepseek.com/chat/completions`、`deepseek-flash`、`DeepSeek-V4.1-Flash` 和“关闭思考（DeepSeek）”。公网例外只开放ModelPort的精确地址，并核验服务端审核登记的模拟功能与元数据，支持子集及已登记扩展；不再要求恰好20项。真实连接器仍只使用获准私网地址，不能把真实材料写进模拟定义绕过保护。部分开发网络采用 `198.18.0.0/15` TLS出口，本地例外仍按官网主机名验证证书。官方别名可能更新，版本标签记录本次所选产品，不证明远端权重永久固定；正式银行模型另行验收。不要输入真实客户数据，安全保管加密主密钥，丢失后旧凭据无法解密。

开发模式新增 `INTENT_MODEL_SECRET_KEY` 用于模型配置的服务端加密；正式部署必须单独设置高强度秘密并安全保存，否则模型配置操作拒绝。停用模型配置会使引用它的旧发布明确不可用，不自动切换。Demo 状态在数据库按空间保存，重复安装不重置已创建工单或已冻结账户；需要新环境应使用新的隔离开发空间，不覆盖现有数据。

在管理平台的智能体详情中配置统一模板。对话界面页填写名称、图标、简介、欢迎语、建议问题、主题色、输入提示和历史侧栏；访问控制页维护用户范围及精确宿主来源；发布与嵌入页选择功能、渠道并发布，复制该应用的Web链接、iframe代码与握手说明。

普通界面、功能组合和宿主来源先保存草稿，再发布生效。同空间智能体名称忽略首尾空白及大小写后唯一，重名返回409；界面隐藏主键但内部引用、public_id和发布ID保留。local会话固定创建时的发布界面和业务版本；迁移前旧身份会话仅管理只读。用户授权、渠道即时停用及宿主撤销单独保存，发布或回滚不会取消。关闭全部渠道时公开应用配置也不可读取。

独立Chat不要求Console登录。首次匿名会话返回一次专用`recovery_secret`，浏览器按应用保存在localStorage，用它与session ID恢复并轮换内存访问token；丢失恢复凭据则新建访客会话，裸ID不能取回历史。匿名主体不建Membership，不能访问管理API、其他应用或Key会话。iframe恢复由宿主后端保存的visitor/session关联决定；已经退出的session不能续活。恢复只读历史快照及后续事件，不重发已受理消息。

当前本地宿主样例是项目根的 `examples/iframe_host.py`，默认仅监听 `http://127.0.0.1:5175`。先在应用中发布该准确Origin，并由具备该空间/应用权限的用户创建Key；只在宿主服务环境设置`INTENT_HOST_KEY`、`INTENT_HOST_AGENT`、`INTENT_HOST_PUBLIC_ID`，可选`INTENT_HOST_PLATFORM`、`INTENT_HOST_API`、`INTENT_HOST_ORIGIN`，然后运行 `backend/.venv/Scripts/python.exe examples/iframe_host.py`。不要把Key值写入命令示例、URL或日志。

宿主后端生成visitor_id，通过HttpOnly第一方Cookie关联内存会话，并保存平台换票返回的session ID；浏览器只提交握手nonce。退出先调用平台Key撤销接口，再清Cookie/映射并发送HOST_LOGOUT。样例内存映射在服务重启后丢失，真实宿主需使用自己的会话存储；本样例不证明正式宿主或银行身份验收。旧 `/console/dev-host.html` 仅作历史夹具参考，不用于local身份换票。

运行日志按会话筛选和分页，标题取对话摘要且可重复；详情逐轮展示输入、最终输出、状态、耗时及必要补参/确认，再按需展开实体、动力、策划与执行阶段。管理员只读投影允许查看空间内旧身份会话，但不放宽用户runtime RLS。升级前未记录的补参/确认原文无法还原，页面空缺不得当作“用户未回复”。管理文件只下载该轮获准最终结果引用的文件。

### 目标纠偏 Demo 操作路径

1. 运行 `./scripts/dev.ps1 up`，打开 `http://127.0.0.1:5173/console/`，使用获准的本地账号。已有数据直接使用；旧身份发布先按上述接口创建local-v1版本。
2. 在“功能库”编辑直接或组合功能，依次完成“基本信息与执行→输入输出与映射→验证与发布”；必填参数有类型和示例提示。直接功能维护实体动作与执行配置，组合功能维护业务Skill、固定依赖及最终输出映射；先保存当前修订，再验证和发布。
3. 在“智能体”打开“三层意图演示助手”，检查20项基础功能及5项组合的选择、模型配置和三层编译预览。新建子集时只选择其所需功能；编译预览会分别列出显式功能及组合内部依赖。
4. 从“发布与嵌入”打开聊天。可依次体验“查询C001的所有账户余额，给我做资金概览”“查询A001的余额和状态”“客户C001的业务全景”。仅有姓名且同名时先选择客户；缺参数时补充；模拟写入必须核对对象与参数后确认，也可取消。
5. 用户页只展示最终答复和必要交互；空间管理员在“运行日志”按会话/智能体/状态筛选，查看轮次概览及折叠阶段。
6. 按上述服务器配置运行Key宿主样例。不同浏览器访客共用Key时会话仍隔离；宿主退出后该session不能通过重放票据或匿名恢复续活。

模拟功能包的权威定义在 `backend/src/intent_platform/demo/catalog.py`，组合规则在相邻 `composites.py`；含业务说明、输入输出、正反例、实体动作和状态要求。改接真实接口需要提供获准目标、鉴权安全引用、字段与错误/超时/幂等说明、对象授权及必要组合规则，不能只替换URL后直接认为已验收。

已有INT-019演示入口是 `/chat/404d67e6-fb6a-4c74-813f-a5e5ae139617`，同一标识用于 `/embed/`；当前访问前须已有local-v1发布。管理列表中带INT019名称的子集/扩展应用是保留的验证样本；日常演示请选择“三层意图演示助手”，具体入口以发布页为准。工单K002是先前验证确认后创建的模拟记录，不保证新初始化环境含有此数据；不要把演示数据当作银行数据。

INT-019当时模型开发结果为97/100、5规则5/5、10复合10/10，历史失败题见[当时记录](../project/records/INT-019-goal-correction.md)，不自动作为INT-021新候选的模型成绩。当前候选模型/浏览器/升级/离线证据由TASKS链接。发布配置固定本地选择，官网模型别名不保证远端权重永久不变，也不证明银行真实验收。

## 数据与恢复

PostgreSQL 分离管理员、迁移 owner 和运行 app 角色；运行角色无超级用户和 BYPASSRLS 权限。初始化 SQL 的口令仅供本地开发。API/Worker 使用 app，迁移使用 owner，不能交换。

开发数据保存在 `intent-local_intent_pgdata` volume。不要为解决迁移错误删除 volume，也不要把 `docker compose down -v` 当作日常重启。现有用户数据先备份和核对。

本地备份运行 `./scripts/backup-local.ps1 -Label manual`。脚本使用已有本地数据库管理员 `intent_admin` 执行 PostgreSQL `pg_dump`：迁移 owner 受 FORCE RLS 限制，不能用它导出全空间备份，也不能为备份关闭隔离或提升 API/Worker 权限。先将归档写在容器 `/tmp`，再复制到被 Git 忽略的 `.local/backups/`，避免 Windows PowerShell 二进制重定向损坏归档。备份可能包含会话和业务数据，应按受控数据保管；银行正式环境需另行批准专用备份身份。恢复先在新建隔离数据库验证，核对业务记录和权限，再决定是否替换目标数据；不要覆盖唯一数据副本。

生产发布前还需确认：备份位置与访问控制、恢复责任人、RTO/RPO、旧模型和工作流/功能版本保留期、迁移路径及恢复演练。当前没有这些真实确认，不宣称具备生产恢复保障。

本地断外网工程检查可运行 `./scripts/verify-offline-local.ps1`。先构建本次待验证的后端和 web 本地镜像；脚本只使用缓存，在新的 Docker internal 网络和新数据库卷中冷启动，读取三条入口和本地资源，再执行一条合成请求及重复消息/事件恢复检查。它不修改当前演示库，不发布端口；成功或失败均先保存 `output/offline/` 报告和必要排错日志，再在 `finally` 中对本轮独立 Compose 项目执行 `down --volumes`，删除测试容器、网络和卷，清理失败记录残留并返回非零。相同名称的既有资源不能覆盖；报告不是正式部署或真实业务验收。

测试收尾遵守 [测试环境清理](../project/TESTING.md#测试环境清理)：清理前核对 Compose 项目标签、挂载和网络使用者，确认全部属于本轮且没有共享；不得只按名称前缀删除。主项目 `intent-local`、`intent-local_intent_pgdata`、正式数据、归属不明资源、共享镜像、备份及历史报告均不在清理范围。禁止用全局 `prune` 代替定向清理。该收尾已获用户授权，无需重复询问；仅用户明确要求保留现场时例外，并记录保留对象与原因。主服务的日常 `stop` 与测试结束即删资源分别适用，不得混用。

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

银行正式宿主及浏览器策略、行内模型与硬件、20项真实功能、封存评测和内网离线冷启动需要独立验收。官网DeepSeek配合模拟数据的开发结果单独记录，实际测试事实见 [项目任务](../project/TASKS.md) 引用的证据记录。


## 双端体验与空间归档（INT-022）

管理主页进入智能体业务配置；顶部可以保存、试聊和发布，外观位于单独页签。新功能/模型从当前智能体创建后返回选择，未保存的业务输入保留。运行日志默认展示最终答复，左侧选择会话，顶部选择问答轮次，可直接切到阶段；由智能体入口进入会自动限定该智能体。

平台管理员从“平台管理→空间目录”创建空间、分配管理员、归档及恢复。空间管理员从空间选择器进入“空间设置→成员”，按姓名/账号搜索、修改多个角色、查看有效权限。移出空间与停用平台账号是两种操作；角色修改不会把已移出成员重新加入。需要重新加入时使用添加成员流程。平台管理员身份本身不授予空间业务内容读取。

0014迁移为旧空间设置active、revision=1、access_epoch=0，既有运行会话保持相同代次，不主动失效。归档增加代次并拒绝公开应用、票据、恢复、运行读取/文件/SSE及新的接入/发送。管理员仅保留获准历史查询。已发送业务记录真实结果，不把归档作为撤回；不继续后续步骤或归档后的对账外呼。恢复仍用增加后的代次，旧票据、运行会话与恢复凭据永久失效；账号与Key在其他空间不受影响。

本地更新前先运行备份，再构建镜像、执行迁移、更新API/Worker/接入Worker与web。恢复演练只能在独立库执行，不能用删除主卷解决问题。当前迁移、升级恢复与双端证据由TASKS的INT-022记录统一索引。前端依赖要求Node20.19+或22.12+；当前Docker22.16可用，按锁文件npm ci，不绕过peer依赖。
