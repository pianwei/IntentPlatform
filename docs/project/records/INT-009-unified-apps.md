# INT-009 统一应用发布实施与验证

日期：2026-09-22。代码为本地main工作区；尚无提交、远程或正式发布。本记录不替代真实模型/业务/身份/宿主验收。

## 授权与验收对象

用户明确授权实施统一平台配置与Dify式聊天发布。界面统一为`/console/`、`/chat/{public_id}`、`/embed/{public_id}`；服务端管理与运行权限继续分开。应用配置随AgentRelease发布，旧会话固定release，撤权及渠道关闭独立生效。

本次涵盖路由/权限/版本正确性、应用配置与单入口、可信续期/历史恢复、图标及结果文件。领域/实体、完整导入映射、正式评测和治理观测由INT-010～013分解跟进，真实接入及产品验收继续由INT-001～008记录。

## 初始状态与保护

- 进入时本地PostgreSQL17.6、API、Worker和双Web入口正在运行；Git为main且全部工程尚未提交。
- 在修改演示数据库前执行`./scripts/backup-local.ps1 -Label before-unified-apps`成功，归档位于`.local/backups/intent-20260922-152146-212-before-unified-apps.dump`。仅生成本地受控备份；未覆盖或删除数据库。
- 演示库升级前只读计数：Alembic `0001`，1个agent、2个release、4个conversation、4个run、4个call；没有读取或记录令牌值。
- Superdesign CLI预检`npx --yes @superdesign/cli@latest`因npm ECONNRESET失败；未上传源代码、未创建外部设计项目。按已确认布局直接实现，实际页面视觉检查另行记录。

## 网关验证

实际命令：`docker --config .local/docker run --rm -v D:/pianwei/work/intent/infra/nginx.conf:/etc/nginx/conf.d/default.conf:ro nginx:1.28.0-alpine nginx -t`。

结果：退出码0，语法及配置检查通过。仅证明语法，不等同页面/后端联调。后续最终配置增加了chat/embed策略子请求与缺失CSP时默认拒绝策略，并再次通过同一语法检查。

## 独立审查关注点

质量执行者审视了公开元数据、nginx auth_request状态码、父身份撤销迁移、旧management令牌会话兼容、确认过期和续期竞态。发现项均已修复并进入下述后端回归。额外覆盖已发送调用先转UNKNOWN再对账、当前授权撤销、畸形Origin默认拒绝、管理身份不能读取新运行会话、结果文件重新鉴权。

## 软件、迁移、浏览器与集成结果

### 软件及迁移

- 后端：受限PostgreSQL `intent_test` 中完整pytest **58 passed，10.53秒**；1条上游AnyIO弃用警告。覆盖编译发布、决定一致性、权限、会话与续期、文件、连接器和故障窗口。此结果对应INT-009完成时的软件，不包含后续INT-010～013新增代码。
- 前端：Node 22.16.0 Alpine容器在`frontend/`运行`npm test`、`npm run build`、`npm run build:development`，**36 passed，两种构建成功**。正式构建不包含开发宿主，资源分别为`/console/assets/`与`/runtime/assets/`。仅有第三方zod注释位置警告。
- 独立升级：在预先配置owner/app角色且初始为空的`intent_upgrade_test`中运行`backend/scripts/verify_upgrade.py`，真实0001→0002通过。旧快照未改写；旧ACL保留，正常过期与明确退出分开，父身份已撤销的历史会话不可重新领取。脚本拒绝覆盖已有升级证据库。
- 集成：`./scripts/dev.ps1 up`成功重建API、Worker及Web；演示库迁移至0002。升级后、浏览器新增夹具前，agent/release/conversation/run/call数量仍为**1/2/4/4/4**。PostgreSQL保留端口54329，应用唯一宿主入口5173；API8000只在Compose网络暴露。

### 浏览器与网关

使用项目锁定Playwright CLI、官方`mcr.microsoft.com/playwright:v1.63.0-noble`容器，Chromium实际版本153.0.8010.12，1440×1000视口。CLI会话`unified`，本地QA容器`intent-browser-qa-unified`连接Web网络空间；快照/日志在`output/playwright/.playwright-cli/`。这是实际浏览器配合真实本地数据库和Worker，模型与业务仍为开发夹具。

| 操作 | 实际结果 |
|---|---|
| 单入口及刷新 | `/console/`、管理深链、两个已发布`/chat/{id}`及`/embed/{id}`可加载；独立资源前缀正常。未知应用403，直接`/runtime/index.html`404 |
| 通过Console创建第二个应用 | 配置标题、欢迎语、建议问题、主题、两个既有夹具功能、Web/iframe与精确来源，保存并发布；新应用历史初始为空，不混入旧应用两条可领取历史 |
| 补参和真实本地Worker链路 | 建议问题缺少`record_id`时出现补参，输入后只读夹具成功，结果卡片显示；这不计真实银行功能 |
| 刷新恢复 | 刷新后重新认证恢复同一会话与结果；对应应用run/call计数仍为1/1，没有重提业务 |
| 跨站iframe | 宿主`http://localhost:5173`嵌入`http://127.0.0.1:5173`；可信一次性票据握手成功，嵌入模式无会话侧栏 |
| 禁止第三方Cookie | CDP实际阻断并用临时Cookie探针核对后，iframe握手及`record_id=iframe-001`只读查询成功，见下述测试前提 |
| iframe令牌过期 | 仅将该夹具会话当前令牌设为过期，轮询401后重新握手恢复原结果；两次已执行查询的run/call仍为2/2，没有重复调用 |
| 草稿与发布 | r2草稿改变标题/欢迎语后，公开页面仍为v1；发布v2后旧会话标题仍为v1，新会话采用v2 |
| 回滚与即时停用 | Console停用Web后chat403、embed200；切回v1后chat仍403。随后恢复v2并明确启用Web，等待保存完成后chat200 |
| CSP | 管理页及独立聊天`frame-ancestors 'none'`；嵌入页仅包含当前发布的精确localhost/127.0.0.1宿主。未知应用失败策略拒绝嵌入 |
| 宿主退出 | 通知iframe退出后原结果清除，显示“已退出；重新连接将建立新运行会话”；重新连接获得v2新会话，不恢复已退出会话 |
| 本地API文档 | `/docs`在CSP下加载自托管`/docs/app.js`，渲染49个操作与55个共享类型，页面错误区域为空 |
| 实际Worker停止/恢复 | `docker --config .local/docker compose --profile app stop worker`后，通过限定运行令牌提交只读夹具：状态RECEIVED、0个call；`compose --profile app start worker`后变为SUCCEEDED、恰好1个call，5个事件按同一游标重复读取完全一致 |

第三方Cookie检查初次只添加`--test-third-party-cookie-phaseout`时，Cookie探针在iframe仍可读，因此**没有计为通过**。随后使用该Chromium实际支持的CDP `Network.setCookieControls({enableThirdPartyCookieRestriction:true})`并刷新宿主页；相同`SameSite=None; Secure`临时Cookie在第一方页面可读、iframe不可读，返回`firstPartyProbeVisible=true / thirdPartyProbeVisible=false`。探针随后删除。该已证实阻断条件下完成iframe查询和续期。接口及刷新要求与安装的Playwright CDP类型和[Chromium测试说明](https://www.chromium.org/Home/chromium-privacy/privacy-sandbox/third-party-cookie-phaseout/)核对。

视觉证据：`output/playwright/unified-console-design.png`、`unified-console-published.png`、`unified-chat-restored.png`、`unified-iframe-blocked-cookies.png`，均已查看。Console为三页配置，聊天为侧栏/标题/消息/固定输入区；iframe收起侧栏。两个自动化等待因预期提示文字不准确超时，经实际快照修正定位后通过；非应用故障。渠道恢复第一次HTTP探针早于异步保存完成返回403，等待UI“打开应用”出现后返回200；不将未完成请求时的结果误写为恢复成功。

应用夹具标识：原应用`f6121f6b-c6f3-4262-b940-109ecdf9c547`；新应用`5c63ab62-7435-418d-94e6-f3e03253255d`。新应用v1=`release_dacbe7921db44e529bae49ca8a20602a`，v2=`release_33b8c600cdad4428b03f110509bccab8`。演示数据保留供复查，未生成秘密值清单。

实际Worker重启探针run=`run_39b570ddc3c9424a8ec61cccb8190d6f`，仅开发只读夹具。该次命令最后清理会话时误用不存在的`/api/v2/auth/logout`返回404，故整条命令退出1；核心停止/恢复断言已输出成功。随后使用正确`/api/v2/runtime/sessions/logout`清理，返回`revoked=true`、退出0。此验证证明持久化队列与事件经过真实进程停止后恢复；**未在下游成功而本地未记账的精确时间点杀进程**，该窗口当前仍由数据库/网络注入测试覆盖，INT-013保留精确故障时点进程验证。

### 交付结论

INT-009本地工程已集成和验证，可从`http://127.0.0.1:5173/console/`配置应用、打开链接或生成嵌入代码；默认无需5174服务。无Git提交、无正式发版或内网部署。上述软件证据不替代完整S1/S2/S3、真实用户宿主或产品M1～M3验收；后续公共能力修改须重新执行受影响回归。

## 真实资源边界

真实银行SSO与注销协议、内网模型、三类平台版本、业务数据权限服务、真实20项功能、外围宿主、硬件和封存金标尚未提供。相关检查保持待验证；开发夹具不计入真实功能数。
