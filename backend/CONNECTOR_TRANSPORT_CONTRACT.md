# E0 连接器传输与远端状态边界

本文件说明本地工程实现；Dify、HiAgent 的具体内网实例版本及字段仍需真实联调确认。回环测试响应均为合成数据，不计真实功能。

`request_json` 与 `request_json_parts` 共用受控 HTTP 传输：先检查目标主机并固定 DNS 解析结果，禁止重定向；响应按网络块读取，**解码后 JSON 最多 5,000,000 字节**。gzip、zlib 包裹或原始 deflate 在读取期间限额解码；原始网络字节也限于 5,000,000 字节加 64 KiB 压缩封装余量。超限、未知压缩格式或损坏数据均拒绝，不会先缓存完整响应。HTTP-v2 明确审核的业务错误码只能读取同样受限的 JSON 错误正文；未审核错误码仍抛出 HTTP 状态异常。

Dify 连接器 v1 的已知成功状态 `succeeded`、已知失败状态 `failed`／`stopped`／`partial-succeeded` 保持明确映射；`running`、`paused`、`pending`、字段缺失及其他未知状态均为 `UNKNOWN`。此前 v1 将部分未知状态错记为 `FAILED`，本次作为安全缺陷修复应用于旧会话后续调用，**不改写**已有 FunctionVersion、AgentRelease 或调用账本。写操作一旦发送，网络超时和未知结果不能由连接器自动重发。若配置了已授权的状态查询端点，可通过原 operation ID 查询；无查询能力时保持 UNKNOWN 待人工对账。

HiAgent 继续使用已审核的请求、结果和状态字段映射：仅 `success_values` 属成功、`failure_values` 属失败，其他状态保持 UNKNOWN。Dify 和 HiAgent 的回环成功、失败、进行中、未知、超时、状态查询证据见 `tests/test_connector_transport.py`；这些测试验证适配边界，不证明任何行内实例兼容。
