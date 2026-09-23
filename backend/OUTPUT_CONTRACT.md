# 受控业务结果契约（E1，本地工程）

## 适用与版本

新发布的非夹具 `FunctionVersion` 必须在功能草稿中提供 `output_schema` 与 `presentation.field_map`，通过当前草稿修订的验证和探测后才能发布。它们随功能版本和 `AgentRelease` 固定；已发布的旧快照不改写。缺少 `output_schema` 的旧版本仍按其原有结果展示协议读取；新增发布不能借此绕过门禁。

`output_schema` 是有限展示契约，不是通用 JSON Schema。根为 `type: object`、`properties`、`required`、`additionalProperties: false`。最多 40 个字段；支持有明确 `maxLength ≤ 16000` 的字符串、整数、有限数字、布尔值、单层对象及有明确 `maxItems ≤ 100` 的数组（可含有限对象行）。投影后的 JSON 最多 256000 字节。未知关键字、无限制字符串/数组、深层结构与类型不符均拒绝。接口返回的额外字段不会进入展示结果。

`presentation.field_map` 为“展示标签 → 顶层输出字段名”，必须恰好覆盖 `output_schema.properties` 且每字段只映射一次。`result_bindings` 仅可引用已获准显示的顶层对象数组；行的 ID 与标签必须是必需的受控字段。`files` 不能作为普通展示字段；仅 `presentation.files_field: "files"` 可允许已有的内联文件协议，文件经大小、类型、名称与 Base64 验证后保存并生成受权下载入口。至少需要一个展示字段或显式文件边界。

## 来源与人工确认

OpenAPI 3.0.x 的单个明确 2xx `application/json` 对象响应可生成有限草稿，并为缺失的字符串和列表上限填写保守值。来源定位及建议值须由维护者在来源审查中确认。无响应 Schema、多成功形态、不支持的关键字或含文件的复杂形态保留为**可选择但不可发布**的草稿：先人工填写输出 Schema、字段映射和文件边界，再确认和探测。导入报告中的 `output_review_required` 是待确认提示，不代表接口已通过响应契约验证。

## 探测、执行与错误

成功探测只返回获准字段及文件元数据，不返回原始未映射字段或文件字节；结果不符时验证失败，错误不引用原始业务值。成功执行先投影并校验，再写入 `Call.result`、`RESULT` 事件或 `result_files`。审计只记录状态和调用标识。

- 只读调用已确认业务成功，但输出不符：调用和运行状态为 `FAILED`，发出 `OUTPUT_CONTRACT_ERROR`；原始响应、文件均不留存。
- 写调用已确认业务成功，但输出不符：调用和运行状态保持 `SUCCEEDED`，发出 `RESULT_UNAVAILABLE` 与 `result_unavailable: true` 的空结果。该状态不触发业务重发。
- 下游返回 `FAILED` 或 `UNKNOWN`：原始失败体不进入账本或展示。`UNKNOWN` 保持对账策略，不当作业务失败，也不自动重发写入。

本契约的合成回环测试只证明本地边界；真实业务字段、文件授权及银行实例响应仍须在 R1/R2 核对。
