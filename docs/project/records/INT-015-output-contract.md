# INT-015 E1 受控业务结果契约证据

日期：2026-09-23。等级 C，涉及功能发布快照、接入来源、运行账本和双前端共享契约。仅记录本地工程与合成服务检查；银行真实业务响应和字段确认仍待 R1。

## 范围与兼容性

- 新发布的非夹具功能必须明确有限 `output_schema`、展示字段映射及可选的 `files_field`。旧不可变功能版本不追溯改写，并按原快照路径读取。
- OpenAPI 3.0 的单一显式 JSON 成功响应可提出受限草稿；无法安全推断时保留操作并提示人工确认，不把推断当业务确认。
- 正常返回只允许批准字段和经服务端检查的文件进入账本、事件和下载。输出不符时，只读调用给结果错误；已确认成功的写操作保持业务成功事实，标记结果不可展示。

## 已执行检查

| 对象 | 实际入口和环境 | 结果 |
|---|---|---|
| Console / Agent Web 共享契约和界面 | 项目根 `./scripts/verify-local.ps1 frontend`，Docker Linux Node、锁定依赖 | 77 passed / 1 既有 skip；TypeScript 检查和两套正式构建通过。新增输出映射及不可展示结果组件用例在内。 |
| 后端完整软件套件 | `backend` 目录 `./.venv/Scripts/python.exe -m pytest tests -q --tb=short`；本地受限 PostgreSQL 测试库 | 353 passed / 3 既有独立进程用例 skipped / 1 既有 Starlette 弃用提示，耗时 244.73 秒，无失败。 |
| 结果异常与相邻路由 | 后端 `test_http_socket_and_recovery.py`、`test_output_contract.py` 定向；领域、评测与影子组合 | 结果专项 18 passed，领域/评测/影子组合 88 passed。覆盖缺字段、类型不符、过大列表、混合文件、只读失败、确认写入不重发和旧不可变版本。 |
| 静态 OpenAPI 契约 | 使用当前工作树 `backend/src` 中的 FastAPI 应用重新生成 `backend/openapi.json` 并检查差异 | 仅新增 Definition/Presentation 输出字段的 23 行契约变化；未使用虚拟环境中旧安装副本。 |

投影在持久化前执行：不合契约的原始业务字段不进入 `Call.result`、事件或授权下载。写调用的已确认成功事实保留，结果不可展示时无业务重发；旧发布不受新发布门禁追溯影响。E2 仍需在共享契约上做接入任务迁移与前端轮询。本地合成回环不计真实业务联调、真实身份或银行环境验收。
