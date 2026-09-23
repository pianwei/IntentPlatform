# 后台文档接入契约（E2）

接入任务与在线路由使用同一 PostgreSQL，但由 `intent_platform.ingest_worker` 独立进程处理。现有 `/documents`、`/documents/{id}/versions` 和 `/openapi-imports` 同步接口继续返回原有 `201` 结构，供旧调用方兼容；Console 的新请求使用下列异步路径。

| 方法与路径（前缀 `/api/v2/management/workspaces/{ws}`） | 请求 | 返回 |
|---|---|---|
| `POST /ingest-jobs/documents` | multipart `file`，`Idempotency-Key` | `202 {job_id,status}` |
| `POST /ingest-jobs/documents/{document_id}/versions` | multipart `file`、`expected_revision`，`Idempotency-Key` | `202 {job_id,status}` |
| `POST /ingest-jobs/openapi-imports` | JSON `{document_id,revision}`，`Idempotency-Key` | `202 {job_id,status}` |
| `GET /ingest-jobs/{job_id}` | 管理令牌 | `{job_id,kind,status,created_at,updated_at,result,error}` |

`status` 只取 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`。成功结果只含 `{document_id,revision}` 或 `{import_id,document_id,revision}`，Console 再用既有 GET 获取正文或解析报告。失败结果只返回安全错误码和说明；不回送上传原文。无支持的 OpenAPI 操作返回 `OPENAPI_UNSUPPORTED` 和有限诊断；不建立导入记录。文档提取或分段失败不建立文档或新修订。由于旧同步端点仍按既有契约处理无效报告，新异步端点在这点上更严格。

新请求必须携带 8–128 字符简单值的 `Idempotency-Key`；同一空间、提交用户和键的重复相同请求返回原任务及其当前状态，不再次创建版本或导入。若内容、文件名、目标或修订号不同，返回 `409`。建议客户端每次用户提交生成一个 UUID，并在网络重试时沿用。上传仍受 `INTENT_MAX_UPLOAD_BYTES` 限制，队列满返回 `429` 与 `Retry-After`。

任务行含空间、提交用户、提交时管理令牌及原始上传内容。`ingest_jobs` 启用且强制 PostgreSQL RLS；API 和 Worker 在提交用户身份下访问。Worker 领取时及提交结果前重新检查身份和空间管理权限。每空间运行中的有效租约默认最多 2 个，排队默认最多 20 个，可由 `INTENT_INGEST_MAX_CONCURRENCY_PER_WORKSPACE` 和 `INTENT_INGEST_MAX_QUEUED_PER_WORKSPACE` 在服务端收紧。租约续期、过期重领和随机 attempt 标识共同阻止旧进程晚到的写入。文档修订在结果事务里再次比对 `expected_revision`，检查、版本建立、任务终态和审计同一事务提交。

任务按提交时的管理身份授权。身份或角色撤销后，即使解析已经开始，提交结果也会被拒绝；令牌到期后未完成的任务可见为授权失败。提交人的空间成员资格被撤销时，只要该空间仍有获准成员，Worker 就在其 RLS 身份下把待处理或运行中任务标记为 `AUTH_REVOKED`。若空间所有成员均被撤销，FORCE RLS 下没有可用身份清理任务，任务保留原状态但不可执行或读取；恢复一个获准成员后下一轮清理才会将其标记失败。原始上传 bytes 为恢复和精确幂等比较保存在受 RLS 保护的任务表，正式环境的备份、保留与清理周期须按数据治理要求确认。E2 的本地测试使用合成文档，不代表真实银行材料或实例兼容。
