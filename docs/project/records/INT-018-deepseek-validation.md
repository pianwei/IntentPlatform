# INT-018 DeepSeek 官网模型开发验证

日期：2026-09-23。对象：本地开发环境、`ws-dev` 的 20 项完整模拟功能智能体。用户提供官网 API 密钥，并确认使用当前 V4.1 Flash 非思考模式。凭据值不进入源码、命令、文档或报告；在 Console 保存为服务端加密的模型配置版本。正式银行内网模型、真实功能、身份和宿主不属于本记录的通过范围。

## 已执行的初次验证

- 后端新增只在 `development` 显式开启的精确 DeepSeek ModelPort 例外；普通业务连接器和正式环境继续限制私网。运行请求要求 AgentRelease 恰好包含已登记的 20 项模拟功能。容器 DNS 将官网解析到 RFC 2544 测试网段；保留固定地址连接、官网 SNI/证书校验与禁止重定向后，只为该开发模型允许该网段。未认证 TLS 探针收到 HTTP 401，随后真实模型连接与 JSON 结构化输出测试在 Console **通过，925.58 ms**。
- Console 保存模型名 `deepseek-flash`、版本标签 `DeepSeek-V4.1-Flash`、`thinking_mode=disabled`，并将该版本发布为模拟智能体 release v2：`release_5816d3f0b079420bb37f600bfe966562`。官方名称是滚动别名，标签不证明权重永久固定。请求使用 `thinking.type=disabled` 和 JSON 响应模式；单元测试检查请求体，真实连接测试证明端到端协议可用。
- 首条无 Skill 复合请求“比较 T001 交易详情和 A001 账户状态”通过正常 Runtime API，经真实模型计划，执行 2 项模拟查询并成功返回；运行编号 `run_5aeb4aaf986c4e3ca067e9e5fbaa3a18`，约 5049 ms。证据为被 Git 忽略的 `output/deepseek/smoke-20260923.json`。
- 初次 10 条模拟复合问题：**9/10** 成功；全部通过运行 API 创建独立会话，9 条成功项的管理轨迹显示 `plan_source=model` 且步骤名称与预期匹配。原始报告为 `output/deepseek/composite-20260923.json`，失败保留在分母。失败项“查询 A001 的余额和状态”在后端计划校验阶段停止，状态 `FAILED/ERROR`、零业务步骤，运行编号 `run_0762a72366c940779c61aca1b13a84ec`。一次仅含模拟数据的诊断调用观察到模型把参数引用写成字符串 `"{$input:account_id}"`，而契约要求 JSON 对象；一次修正又输出 `OUT_OF_SCOPE`。服务端拒绝了不合法计划，未执行错误调用。
- 全后端套件在受限 PostgreSQL 独立库 `intent_deepseek_0923b_test` 完成 **450 passed、3 skipped、1 既有弃用提示**。前端完整测试 **83 passed、1 skipped**，双正式构建通过；本地开发 Compose API、在线/接入 Worker、数据库和 Web 健康启动。起初测试容器继承开发公网开关导致 `test` 模式收集被拒绝，测试脚本已显式关闭该开关；修正后的独立全量运行通过。

## 修订与独立复测

- 首次修订发布为 v3 `release_d5e1a15a1de44c6599daef4ac424ca57`，固定 `planner-v2`。原失败题定向复测 1/1 通过；完整复测却仅 **7/10** 通过，另一次逐功能抽样为 **14/20**。报告分别为 `output/deepseek/balance-planner-v2-20260923.json`、`composite-planner-v2-20260923.json` 和 `coverage-planner-v2-20260923.json`。失败均保留：模型误判已授权能力为域外、结果数组引用用方括号、把直接给出的两账户误当 `foreach`；单功能题还曾错选未匹配的全景 Skill，交易方向的中文值未被原输入 Schema 限制而在模拟执行中失败。没有通过降低断言把这些记录改为成功。
- 第二次修订只作用于新发布的 `planner-v3`：只将触发词匹配的 Skill 提供给模型；说明点分数组索引、直接标识和双独立步骤；精确命中获准功能名而首次误判域外时允许一次纠偏，真正域外问题仍拒绝。模拟交易流水的 `direction` 输入契约新增 `IN/OUT` 枚举及中文说明，经显式开发种子核对发布为新的功能版本，原版本不被改写。发布 v4 `release_ddd7b8818c9d47ebb2af14e900001d10` 继续固定同一服务端加密模型配置。数据库读取确认 v1/v2 为 `planner-v1`、v3 为 `planner-v2`、v4 为 `planner-v3`，各版本均含 20 项模拟功能。
- v4 的 **10/10 合成复合问题**通过，报告 `output/deepseek/composite-planner-v3-20260923.json`；**20/20 逐功能抽样**达到预期，前 18 项成功，第 19、20 项停在 `WAITING_CONFIRMATION`，报告 `output/deepseek/coverage-planner-v3-20260923.json`。两项模拟写入运行 `run_c9f44697b46e406d9182c648d1bcc519` 与 `run_a39a518c5c264c95917935220c48432b` 在本地数据库 `calls` 表合计 **0** 条调用，证明确认前未执行。20/20 是每功能 1 条开发题的抽样诊断，不是已完成每功能 5 条模型问法或封存评测。
- 另补跑五条已发布业务 Skill 的规则优先链路，**5/5** 达到预期，报告 `output/deepseek/skills-planner-v3-20260923.json`。客户资金概览、账户收支分析、客户贷款概览、客户业务全景均完成，异常账户核查停在确认前；该轮 `run_3a7d4f1b9f804bca9fda965befd36637` 只有 1 次账户状态只读调用、**0 次写调用**。规则步骤由已发布 Skill 确定，官网模型用于成功链路的结果汇总；不把它描述为模型自行规划成功。
- 实际浏览器在独立用户页 `/chat/404d67e6-fb6a-4c74-813f-a5e5ae139617` 以限定应用的开发运行身份完成“比较 T001 交易详情和 A001 账户状态”：运行 `run_8bfa3f8ac5174229ba3607b894045f3c` 返回两张模拟结果卡和模型答复，收起的处理记录可展开两步名称、获准输入输出及 **3943 ms** 整轮耗时；页面无管理导航。Console “运行日志”可对同轮查看实体层对象与来源、动力层获准动作、策划层模型计划和执行结果；未显示模型隐藏推理或凭据。浏览器页面已保留供查看。
- 本轮最终软件验证的结果见下节；原首轮 9/10、v3 的 7/10 与 14/20 均保持为真实历史证据。上述实测只涉及官网模型和模拟业务，无法推断银行实际模型质量或业务准确率。

## 最终软件验证

最终后端全量套件在独立库 `intent_deepseek_release_0923_test` 通过 **456 passed、3 skipped、1 条既有 Starlette 弃用提示**，命令为 `./scripts/verify-local.ps1 backend -TestDatabase intent_deepseek_release_0923_test`。修订前一轮在 `intent_deepseek_final_0923_test` 为 **453 passed、3 skipped**；三项新增定向回归也曾单独 **3 passed**。前端完整测试 **83 passed、1 skipped**及双正式构建为本次前端变更后的实际结果。最终 Compose 重建后 API、在线 Worker、接入 Worker、PostgreSQL 和 Web 均达到健康状态；`git diff --check` 无补丁空白错误。测试未生成项目禁止的检验。

## 验收边界

本记录证明官网真实模型调用和模拟业务链的开发行为；它不含银行批准的内网模型、20 项真实功能或封存 S1/S2/S3 金标。完整 Agent 质量和性能验收继续待真实资源与冻结口径，不能用上述 10 条开发题替代。真实模型别名如后续更新，需登记实际测试日期与配置并重新评测。
