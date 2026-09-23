# INT-003 / INT-002 / INT-005 本地工程验证记录

日期：2026-09-22；协调者：当前主 Agent。软件版本 0.1.0，代码为本地 main 工作区，尚无提交、远程、CI、正式部署或用户业务验收。

本记录覆盖本次新增工程的本地软件验证。它不是 M1 真实贯通或 M3 全量验收报告；真实20功能、模型、SSO、宿主、平台版本、硬件和封存评测仍缺材料。任务状态只在 [TASKS.md](../TASKS.md)。

## 产出与环境

- API/Worker：Python、FastAPI、Pydantic、HTTPX、SQLAlchemy、PostgreSQL，显式状态机、持久调用及事件、元模型编译和不可变发布。
- 双前端：React/TypeScript、独立 Console/Agent Web、assistant-ui、自定义 API、游标事件恢复和开发宿主。
- 依赖：后端 requirements + constraints，前端 package-lock；容器 Python 3.13.5、Node 22.16.0、PostgreSQL 17.6、Nginx 1.28.0。
- 环境：Windows 本地 Docker Desktop；API 8000，Console 5173，Agent Web 5174，数据库主机端口54329。
- 数据库：`intent` 为开发演示；`intent_test` 为集成测试；`intent_migration_test` 为新建迁移测试；`intent_restore_20260922` 为隔离恢复演练。不清空演示库。
- 固定模型与两个明确标识的夹具只用于工程验证；HTTP socket 测试使用实际回环网络和合成业务响应。

## 已执行的整套启动与浏览器操作

`docker --config .local/docker compose --profile app up --build -d --wait` 完成镜像构建、迁移、数据库/API健康等待及双前端启动。最终源代码增量的再构建与套件结果在本记录末尾归并。

浏览器为 Playwright 官方 `v1.63.0-noble` 镜像中的 Chromium 1243；CLI `0.1.21` 显式指定该可执行文件。浏览器容器共用项目 Web 容器网络，在 `127.0.0.1:5173/5174` 操作真实页面，不拦截 API 返回。

| 场景 | 实际结果 | 层次 |
|---|---|---|
| Console 登录与空间目录 | 开发身份进入 ws-dev，显示数据库中的两个已发布夹具 | 浏览器 + 真实 API/PostgreSQL；身份为开发夹具 |
| 功能验证与发布 | 当前映射通过选定验证，界面明确未调真实接口；发布生成 v2 | 浏览器 + 编译器/数据库 |
| AgentRelease 发布 | 界面发布组合，新独立会话使用 v2 功能快照 | 浏览器 + 发布契约 |
| 独立页登录与补参 | “查询资料”先返回 record_id 问题，填写后成功，旧问题禁用 | 浏览器 + Worker；模型和业务为夹具 |
| 结果追溯 | 展示输入值、功能来源/version、run/revision/sequence 和开发标记 | 浏览器 + 持久事件 |
| iframe 握手与查询 | 5173宿主→5174iframe，票据兑换后成功执行同一夹具 | 实际跨 Origin 浏览器；宿主与身份为开发模拟 |
| 宿主退出 | iframe 清除登录状态，回到握手等待页 | 浏览器 + 退出 API |
| Cookie/存储 | 浏览器 Cookie为空，宿主页 localStorage/sessionStorage为空 | 实际浏览器存储检查；不等于真实第三方Cookie禁用验收 |
| 浏览器错误 | 检查时控制台 0 errors / 0 warnings | 当前已操作的开发宿主页 |

截图：[Console](../../../output/playwright/console.png)、[独立页补参结果](../../../output/playwright/agent-result.png)、[iframe结果](../../../output/playwright/iframe-result.png)。截图中的业务数据明确为开发夹具，不是银行数据。

## 代码审查及修复证据

独立审查覆盖身份、配置、DB、模型、迁移、网络、元模型、连接器、服务，以及完整 API/Worker 调用链。共发现并修复：跨空间/目标凭据、目标端口/路径、异步状态误判、显式失败映射、整数 Python/RDF 一致性、畸形模型返回、未派发调用的改口取消、宿主撤销、对账租约恢复、多动作确认、独立调用聚合、状态查询凭据绑定。

审查者用纯逻辑及进程内假网络/Session复现并复核全部已报问题；未把此证据称为真实接口测试。实现者另外将关键边界加入自动测试。最终套件见末尾。

## 本地备份恢复

执行 `./scripts/backup-local.ps1 -Label before-final-migration`，生成 PostgreSQL custom-format 归档，保存在被忽略的 `.local/backups/`。归档未上传、不放入交付公开目录。

创建新库 `intent_restore_20260922`，以 owner 用 `pg_restore --exit-on-error` 成功恢复。对比结果：

| 项 | 原库 | 恢复库 |
|---|---:|---:|
| functions | 2 | 2 |
| agent_releases | 2 | 2 |
| conversations | 3 | 3 |
| events | 19 | 19 |
| RLS policies | 11 | 11 |
| immutable triggers | 2 | 2 |

恢复库 `intent_app` 的 superuser/BYPASSRLS 均为 false；无身份可见功能0，`dev-admin` 可见2，`dev-other` 可见0。该演练验证本地样本归档与约束恢复，未覆盖生产容量、RTO/RPO或历史产品版本升级。

## 尚未完成的产品范围

- 真实20项盘点与分批接入，三类平台分别真实成功/失败/状态证据，全量/两个子集智能体、第21项及新增领域验收。
- 实际模型、正式SSO/委托协议、真实宿主、浏览器兼容和第三方Cookie禁用场景。
- 封存 S1/S2/S3、逐功能指标、本体消融、5个持续在途请求性能、内网离线冷启动、硬件与生产恢复。
- 文档接入支持可抽取文本，扫描件 OCR 后置。当前前端 Markdown 按文本、citation/file 按安全详情回退；专用文件下载/引用组件、复杂 OpenAPI 引用或参数形态需后续工作包补齐。
- 当前 Console 诊断是路由开发工具，不能称作完整 Agent 评测系统；开发金标与正式封存数据需独立管理和确认。
- 当前实体绑定支持显式参数与补参；可信业务实体解析、引用前一结果中的对象、完整领域配置包治理和文档映射建议仍需继续实现与真实数据验证，不能把JSON/RDF有限编译等同于完整领域本体平台。

## 最终集成验证

主协调者从项目根执行并取得以下结果：

| 实际入口 | 结果 |
|---|---|
| `./scripts/verify-local.ps1 backend` | 28 passed，4.35秒；真实独立PostgreSQL；1条上游Starlette/AnyIO弃用警告 |
| `./scripts/verify-local.ps1 frontend` | 9个测试文件、24 passed；TypeScript及production模式双构建成功 |
| `./scripts/dev.ps1 up` | 最终开发模式双前端、后端wheel安装、Alembic迁移、API/Worker/DB/Web启动成功 |
| 全新Alembic首迁移与安装包资源 | 实施者在intent_migration_test应用冻结77条DDL，版本0001；清空PYTHONPATH后安装包迁移成功；详见 [后端证据](../../../backend/VALIDATION.md) |
| 最终Console诊断浏览器 | 导入两条明确的开发测试用例，第二条故意错误预期；显示1/2、50.00%，两条结果完整保留。此数字是UI/分母检查，不是模型质量评分 |
| 最终iframe浏览器 | 新票据握手→查询→RESULT/完成→宿主退出，成功；新核心响应schema与前端兼容 |
| API本地文档 | `/docs`、`/openapi.json` HTTP 200；34个路径、44个schema，浏览器渲染方法和共享类型，只读取本地资源；默认favicon请求404不影响文档。最后文档展示变更单独HTTP/浏览器核对，不改变上述业务测试对象 |
| 后端重建后代理恢复 | 首次实际出现502，定位为Nginx缓存旧API容器地址；修复为Docker DNS动态解析。再次单独force-recreate API，两套代理health均返回200，无需重启Web |

最后一处仅影响API文档展示的变更以启动和HTTP检查验证，未无故重复全部业务测试。前端构建存在上游zod注释位置警告，不影响构建；后端弃用警告已保留，未删测试或压制错误。

代码与两套构建产物本地可用。正式模式静态制品位于 `frontend/dist/console`、`frontend/dist/agent-web`；正在运行的本地Compose使用显式开发模式镜像。API文档与OpenAPI在本机8000端口。没有Git提交、外部发布或正式银行上线。

结论：本次本地工程基线及所列软件/浏览器/迁移/恢复验证完成。M0真实材料及M1–M3产品退出条件未满足；剩余实现和真实资源条件见上文，不能把52个现有软件测试称为一期全量验收。
