# INT-016 E2 文档接入后台任务证据

日期：2026-09-23。等级 C：数据库迁移、空间权限、任务租约与 Console 来源流程。所有本地样本均为合成内容；真实银行材料和来源负责人仍待 R1/R2。

## 契约与兼容性

新 Console 使用 `POST /api/v2/management/workspaces/{ws}/ingest-jobs/documents`、对应文档修订路径及 `openapi-imports` 路径。提交时携带本次请求的 `Idempotency-Key`，响应 `202 {job_id,status}`；随后读取任务状态直到成功或失败，再用既有读取 API 获取固定文档修订或导入记录。原有同步上传、修订和导入 POST 保留 201 行为。作业号不构成权限凭证，状态读取仍需空间维护权限。

## 已执行检查

| 对象 | 实际入口和环境 | 结果 |
|---|---|---|
| 后端完整软件测试 | 项目根 `./scripts/verify-local.ps1 backend`；独立受限 PostgreSQL 测试库，迁移 0006 | **361 passed / 3 既有 skipped / 1 既有弃用提示**；随后针对审查修补复跑接入及相邻权限测试 **13 passed**。覆盖跨空间、运行令牌拒绝、幂等、队列上限、租约过期与进程退出恢复、撤权、失败无修订、旧同步接口及慢解析时在线读取。最终候选完整回归由 INT-017 再执行。 |
| Console / Agent Web | 项目根 `./scripts/verify-local.ps1 frontend`；Docker Linux Node 及锁定依赖 | **81 passed / 1 既有 skipped**，类型检查和两套正式构建通过；诊断显示尾修后定向 **16 passed** 与类型检查通过。覆盖上传后按固定修订读取、OpenAPI 任务结果、运行中轮询及失败诊断。最终候选完整回归由 INT-017 再执行。 |
| 本地 Compose 实际任务 | `./scripts/dev.ps1 up` 后 `python scripts/verify_ingest_local.py` | 迁移 0006、API、在线 Worker、接入 Worker 和统一前端同时启动；合成文档与 OpenAPI 作业由 PENDING 到 SUCCEEDED，重复键复用原任务、冲突键 409、解析出 1 个受支持操作。此项是本地开发身份与合成数据，不计真实验收。 |
| 浏览器 Console | Playwright CLI 打开 `http://127.0.0.1:5173/console/`，开发维护者进入“来源文档”，上传 `.local/e2-browser-source.md` | 页面完成异步上传后选中 `e2-browser-source.md · r1`，可见固定文档 ID、修订和抽取文本；浏览器仅有 favicon 缺失 404。 |
| 配置和迁移 | `docker --config .local/docker compose --profile app config --quiet`、`nginx -t`、迁移后本地服务 | Compose 与 Nginx 配置有效，接入 Worker 独立启动；受限数据库的 FORCE RLS、空间拒绝和事务界限由后端测试验证。 |

产出：新增空间隔离接入任务、0006 迁移、独立 Worker、202/任务状态契约、Console 轮询和错误定位，保留原同步接口。验证：见上表。集成：本地 Compose 已迁移并完成 API 与浏览器实际路径。交付：本地工程可用；正式环境负载、长期运行与真实银行资源尚未验证。若空间所有成员均被撤销，FORCE RLS 下任务不会执行或可读，须恢复获准成员后下一轮清理才可标记失败。原始上传在任务表中保留以支持恢复，正式保留期和清理策略待数据治理确认。
