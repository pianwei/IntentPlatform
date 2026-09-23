# INT-011 文档来源与 HTTP 导入工程证据

日期：2026-09-22。本地开发包，尚未正式发布；实现及验证最终状态由主协调者维护在 [TASKS.md](../TASKS.md)。

## 契约与实现

[IMPORT_CONTRACT.md](../../../backend/IMPORT_CONTRACT.md) 定义不可变文档修订及章节、OpenAPI 3.0.x JSON/YAML 有限导入、HTTP connector v2 的 path/query/header/body 显式映射、来源候选和冲突确认。旧文档迁移为仅有抽取文本的 revision 1，不伪造原始二进制；新上传保留真实原文件。来源、确认理由和已验证状态绑定功能 revision，修改后重新验证。

HTTP v2 保留请求体存在性，显式业务失败 HTTP 码只允许声明支持的 400/404/409/422；401、403、5xx、连接故障和超时不能当作业务负例通过。原 HTTP v1 映射保留。引入的 PyYAML 6.0.2 已锁定，首次受限网络安装失败后，经授权使用官方源安装成功。

## 已执行的中间验证

- 解析、路径和危险头的首批定向 **29 项通过**。后续修复和最终完整回归待本记录追加，不能只据首批结果冻结候选。
- `backend/scripts/verify_import_upgrade.py` 在初始为空的 `intent_import_upgrade_test` 实际完成 **0003→0004**。旧文档、功能、功能版本、AgentRelease、会话、运行和调用的原字段逐值不变；旧原文件为 NULL，UNKNOWN、operation ID、revision 保留；新来源表 RLS 与不可变约束通过。未修改演示库。
- 通用评测归档一致性修复另见 [INT-012 记录](INT-012-evaluation-tooling.md)；新增 4 项复现及原 runner 共 57 项通过，完整组合结果以最终回归为准。

## 独立审查与修复范围

后端只读审查确认并交实现者处理：HTTP v1 服务端认证头可能覆盖 Host；404 负探测被误当传输异常；必需空 JSON 对象被省略；损坏 DOCX 修订返回 500；本地引用展开只有节点预算而缺少总文本字节预算。修复须分别验证公共头部边界、显式失败状态、请求体存在性、422 且不新增修订，以及展开/最终报告上限。来源 revision/quote/确认/发布失效链和旧来源兼容本轮未发现额外可达问题。

前端只读审查发现并交实现者处理：关闭后同空间重开另一功能时旧异步回调可能覆盖新草稿；来源引用与定义两份未保存输入可能互相刷新丢失；切回 HTTP v1 时遗留 v2 配置。修复要求按编辑器实例拒绝迟到响应，两个草稿的编辑顺序有明确门禁且可安全退出，版本降级不静默丢字段。另修正 INT-010 发现的 Chrome HTML pattern 兼容性。

## 集成准备与边界

本地浏览器输入 [import-public-apps.yaml](../../../output/playwright/import-public-apps.yaml) 仅描述本地公开应用配置接口，用于导入与请求映射验证，不是银行业务功能。临时 `.local/compose-http-demo.yaml` 仅批准本地 `api:8000/api/v2/runtime/apps` 的 HTTP 目标，不含凭据；当前仅准备，尚未应用到演示服务。浏览器、最终构建和演示库升级事实后续追加。

扫描件 OCR、OpenAPI 的全部复杂组合、真实银行接口映射与第 21 项扩展仍需真实材料和独立验证；有限不支持项明确诊断，不能静默忽略或计作通过。

## 后端冻结与演示库升级

- 后端完整套件实际执行 `backend/.venv/Scripts/python.exe -m pytest -q -x --tb=short`：**247 项通过，48.16 秒**，保留一条已有 Starlette/AnyIO 警告。最后归档元数据声明调整后，runner 与归档专项 **57 项通过，8.07 秒**；解析、来源和归档定向 **52 项通过**。这些是不同命令的结果，不相加为一次全量。
- 编译检查及 `pip check` 通过；冻结接口为 65 个路径、69 个操作、82 个 Schema。上述后端审查问题已修复并有对应回归。
- 升级前保存镜像标签 `intent-backend:domain-baseline`，执行 `./scripts/backup-local.ps1 -Label before-source-import`，归档为 `.local/backups/intent-20260922-173600-458-before-source-import.dump`。
- 执行 `docker --config .local/docker compose --profile app build migrate`，随后 `docker --config .local/docker compose --profile app up -d --wait api worker`。演示库实际为 **0004**，统一入口 `/health` 返回 PostgreSQL/development/fixture；升级前后 agents=2、releases=4、conversations=8、runs=7、calls=7，数量保留。
- 前端最终修复另包括新增 HTTP 字段的来源确认列表、坏映射 JSON 的明确错误和输入保留、评测归档清理范围说明；前端构建与浏览器证据待下一节追加。INT-013 的后续后端开发不混入本次已构建镜像。

## 浏览器导入、发布与实际 HTTP 运行

Chrome 153、Playwright CLI 0.1.21，单入口 `http://127.0.0.1:5173`，API/Worker 使用上述冻结镜像。前端先以 62 项通过的构建验证正常链路；随后坏初始映射与类型边界尾修达到 65 项，浏览器发现的键序误判仍需尾修制品复核。不同构建证据不混称同一冻结候选。

1. 在 Console 上传 `import-public-apps.yaml`，得到 `doc_ed397811605940d1b02aa48da8a50623` r1；下载原件 886 字节，与上传文件直接逐字节相等。追加 `import-public-apps-v2.yaml` 为 r2，原 r1 仍可选择。
2. 功能接入向导明确选 r1，再由服务器解析为 `import_e2ac5fbc1fc94910bcffcf7044b8f1a0`。生成 GET 操作草稿 `fn_c3346cce7b0442bcb2953937e8d33b5c`；未审查时验证和发布按钮禁用。
3. 设置 `local:Application`、`local:ApplicationConfig`、显式失败 HTTP 404，保存功能 r2。对十项业务解释分别写入仅适用于该本地接口的理由，来源审查变为就绪。未创建虚构业务来源或真实金标。
4. 成功参数为已有演示应用 ID，失败参数为 `missing-fixture-app`。实际 HTTP 正探测 `SUCCEEDED`，负探测 `FAILED / http_status_code:404`；编译 Python/RDF 一致性及 6 个 conformance cases 通过。随后通过界面发布功能 v1，数据库固定 `document_refs` 为该文档 r1、失败映射为 `[404]`。
5. Console 创建并发布“HTTP文档联调 · 合成应用”，应用标识 `490b65ba-4974-4147-83cf-f14ddb13ecca`，只组合上述功能。独立聊天页以运行身份登录，建议问题实际经 Worker 调用 HTTP，结果卡展示成功。
6. 刷新并重新认证后恢复同一 `conv_d8f110054a7f4c41a1cdd2f81b70dc89`，仅一张结果卡；`run_b2c06367afc94cc3b68d89954bd6ce08` 保持 SUCCEEDED、同一条 call，没有新增运行或调用。截图 [HTTP聊天恢复](../../../output/playwright/import-http-chat-restored.png) 已查看。

临时目标授权 `.local/compose-http-demo.yaml` 已实际应用，仅批准自身公开应用路径。该接口是实际 HTTP 传输，返回内容、身份和路由模型均为本地开发材料，不算银行真实功能、真实模型或真实宿主验收。结束验证后需关闭测试应用/功能并还原默认目标配置，避免默认重启后留下看似可用的临时授权入口。

浏览器发现发布后页面错误提示“未保存修改”：保存响应和 PostgreSQL JSONB 重读的对象键序不同，而界面用字符串比较。数据库发布正确；已交尾修为对象键序无关、数组顺序敏感的比较，并保留真实未保存输入。错误状态截图 [发布后的误提示](../../../output/playwright/import-validation-published.png) 保留为缺陷证据，不作为最终界面通过截图。

## 尾修冻结与复核

- 非法初始 `http_mapping:{}`、失败状态为数字/对象/非法枚举及 body 非布尔值的修复后，前端 **65/65** 全测试、正式/开发双构建及类型检查通过。报告 `frontend/.cache/int011-frontend-tests.json`。
- JSON 键序修复后的新定向 **21/21**、类型检查通过，报告 `frontend/.cache/int011-tail-tests.json`；再实际构建 Docker web 的 development 双应用制品并更新单入口。正式构建留待后续公共治理包组合检查，不把上次构建冒记为最新执行。
- 最新 Chrome 实测：对象键重排没有 dirty；输入 `http_failure_statuses:42` 时抽屉仍存在、原 JSON 保留、显示明确诊断且保存禁用；修正后恢复。重新正负探测并发布功能 v2 后，dirty 仍为 false。最终截图 [验证与发布](../../../output/playwright/import-validation-final.png) 已查看。
- 浏览器自动化曾误选旧 session，并在 Node 执行上下文使用了不可用的 `structuredClone`，均为验证脚本错误；更正后上述步骤通过，不计为产品故障。
- 临时 HTTP 功能已通过 Console 停用，该合成应用的独立页渠道也已关闭；执行默认 Compose 恢复 API/Worker，临时目标授权不再启用。原两个演示应用公开配置均 200，临时应用 404；不可变文档、两份功能版本、应用发布、调用和截图全部保留。停用后的公开配置 404 为预期，非浏览器脚本异常。

本包交付为本地工程。功能的业务输入 Schema、复杂成功条件及展示映射仍使用受校验的高级 JSON；支持范围由有限契约约束。真实业务、真实宿主及复杂 OpenAPI 扩展不在本地合成链路中冒领通过。
