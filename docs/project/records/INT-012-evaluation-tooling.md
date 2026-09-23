# INT-012 评测工具工程证据

日期：2026-09-22。执行者：AI质量执行者，主协调者归并。当前为本地未提交代码；真实模型、封存金标和20功能数据缺失，以下结果均不代表业务验收通过。

## 012.1～012.2首批产出

严格数据契约、纯评分器、正式运行API客户端、只读证据投影和CLI归档已经实现；依据[评测契约](../../../backend/EVALUATION_CONTRACT.md)。调用经过正常运行会话、补参、确认、取消和事件接口，模型提议不会直接计作完成业务。身份令牌由显式环境变量绑定到真实用户和空间，命令行参数与归档不保存令牌值。

开发和封存数据有不同用途；封存执行与允许副作用分别显式启用。确认只来自测试步骤本身，不从金标推导；UNKNOWN和提交超时不自动重发。实体选择按显式、唯一的显示标签解析本次opaque选项，重复标签拒绝。`runtime-observation-v1`把动态运行定位留在原始证据，评分使用固定的语义投影。

评分保留缺失/失败原始分母，分别报告S1 N/A/C、参数、语义、确认、业务和展示、逐功能、S2和S3。无样本返回未定义，未知安全证据不默认填0。报告始终`acceptance_evidence=false`。候选对比使用独立已发布release；当前工具不会自动构造本体消融版本，也不会自动发布、回滚或批准验收。

CLI为`backend/scripts/evaluate.py`，提供离线`score`与授权`execute`。先独占保留新的trial目录，再原子生成`trial.json`，拒绝覆盖已有或中断目录。归档包含数据、观测、重算报告、脱敏轨迹、运行证据及gaps。完整字段、命令参数和权限子集限制见契约。

## 已执行验证

在`backend/`运行`./.venv/Scripts/python.exe -m pytest tests/test_evaluation_scoring.py tests/test_evaluation_runner.py -q`：**82 passed，0.30秒**（40项评分、42项执行器/CLI/投影）。`./.venv/Scripts/python.exe scripts/evaluate.py --help`可运行。

本批是纯Python和受控HTTPX MockTransport层，覆盖版本/用途、真实subject绑定拒绝、正常S2和负确认、唯一标签选择、超时及UNKNOWN不重提、归档路径/原子失败/脱敏。未连接数据库或真实模型，不能用82项软件通过声称82项业务场景通过。

此前首版评分器39项通过；增加未知安全证据、标签选择和正常授权边界契约后，以本批82项作为首批冻结结果。后续PostgreSQL运行接口和Console验证在本记录追加，不覆盖历史层次。

## PostgreSQL 执行与归档复核

首批冻结后新增 7 项实际 PostgreSQL、Runtime API、Worker 和回环 HTTP 验证：S1、普通补参 S3、实体选择 S3、空授权 S2、肯定/否定确认及实际调用审计版本、写超时 UNKNOWN。UNKNOWN 场景下游只收到一次写请求，后续步骤保持 `not_run`。载荷和业务服务均为合成夹具。

归档复核发现脱敏器把 `report.s2.authorization` 评分组当作 Authorization 秘密误删。已修复为精确头部处理并保留严格数值评分结构；Bearer、Basic、结构化凭据及已知令牌值仍脱敏，新增 4 项回归和三模型重载检查。修复后分别执行：

- 纯评分/受控 HTTP/CLI/投影：**86 passed, 7 deselected / 1.89s**。
- PostgreSQL/Runtime/Worker/回环 HTTP：**7 passed, 46 deselected / 8.38s**，一个 Starlette 依赖弃用警告。

这两次专项不是一次完整后端运行，不能直接与 INT-010 的历史 184 项相加声称全量通过。有效导出为 [pg-entity-b62b131a73/trial.json](../../../output/evaluations/pg-entity-b62b131a73/trial.json)，77,703 bytes；Dataset、ObservationBatch、EvaluationReport 严格重载均通过。文件明确为 `synthetic` / `fixture`，保留两步安全证据未观测及浏览器展示缺口，`acceptance_evidence=false`。

第一次错误归档 `output/evaluations/pg-entity-86939f853f/trial.json` 留存，不能作为有效评分报告；修复后的输出使用新目录，没有覆盖原试验。

## 尚待验证及交付边界

Console归档查看已实现；没有评测归档持久化管理API，首批通过本地文件交付，不宣称已入库。完整本体消融执行、业务复核的封存评测、实际业务结果的浏览器展示证据、20功能及硬件性能仍需独立材料和验证。任务状态只在[TASKS](../TASKS.md)。

后续只读复核发现更一般的归档缺陷：宽泛按字段名脱敏会误改合法 `token_count` 参数，可能使原来不匹配的金标/观测在归档中变成相同值；合法功能 ID 为 `credential_ref` 时也可能破坏版本映射类型。此问题已交由后端当前包修复，要求评分输入保真、写前严格重载及评分一致性检查；包含实际已知秘密且不能保真的输入应拒绝归档。上面的有效实体样本不触发该问题，但不能据该样本外推通用归档一致性。

该缺陷已在 INT-011 后端包中修复：Dataset/Observations/Report 原样保留业务键和值；实际已知秘密命中评分输入时拒绝归档；仅 wire 轨迹、原始证据和 gaps 使用字段脱敏。序列化后的实际内容再次严格加载三模型并重评分比较，评分变化不能落盘。新增 `sanitization` 声明未知秘密没有自动核验、输入材料需由提供方检查。4 项新复现回归及 runner 套件共 57 项通过；独立只读复核认可该调用链，完整候选回归由 INT-011 记录最终归并。

消融只读设计确认：当前候选比较尚非 V2.1 §11.5 联合消融；router-v2 输入含功能角色关系短卡，但未发送完整去重公共领域投影。后续使用新注册版本，同一不可变 release/获准子集的 A（说明与参数）、B（投影与语义校验）、C（去关系）影子对照，全部禁止业务派发；正常 B 端到端另报告。fixture 不证明真实模型收益，依赖新业务结果的 S3 不能伪作完整影子执行。

## Console 归档查看验证

归档查看页随 INT-010 前端包交付；该批新增定向 13 项、全部前端 49 项及正式/开发双构建通过。真实 `pg-entity-b62b131a73` 文件纳入字节保留测试。页面只读取用户选择的本地文件，不发起业务执行或把格式化后的 JSON 当作权威评分数据。

Chrome 153 实际浏览器导入有效归档，正确显示 development/fixture、1/1 用例、2 个未观测安全步骤、S1 零分母“未定义”、S3 完整正确 0/1，以及 `acceptance_evidence=false`。原样下载为 `output/playwright/evaluation-downloaded-trial.json`，与输入均为 77,703 bytes，直接字节比较相等。导入旧损坏归档后显示 S2 authorization 结构错误并移除下载按钮；重新导入有效文件恢复展示。

截图：[归档查看](../../../output/playwright/evaluation-archive-view.png)、[指标与分母](../../../output/playwright/evaluation-archive-metrics.png)。这里验证的是查看工具，不能据此消除原试验里 `browser_display_unobserved` 的业务展示缺口。

## 012.3完整模型投影与有限影子对照

已实现[影子契约](../../../backend/ABLATION_CONTRACT.md)。新领域release选择router-v3，模型请求包含全部授权候选及去重领域投影，冲突或超出有限预算明确拒绝；旧v1/v2实现、快照及会话不修改。默认关闭的`shadow-comparison`仅允许限定会话所有者兼有效publisher/admin，每空间最多一个比较，模型调用前后均重验权限和上下文。

A仅说明/参数；B为完整领域与确定性语义；C在实际输入及校验两处联合移除关系。三臂共享不可变release、模型配置、文本及获准历史，不创建Run/Call/Event、不调用业务连接器；可信resolver仍可发生受限只读查询。CLI`evaluate_shadow.py`归档为独立shadow.json，保留每臂原分母，不将影子指标混作正常端到端或S3。

新专项先21项通过；独立审查发现集合指标对同一功能的多次请求、功能消歧候选处理错误，修复为稳定功能ID集合去重，功能歧义取实际候选，实体/历史歧义取所选功能而非opaque选项。缺失候选不能和空金标误判成功，金标重复ID拒绝。审查复核关闭，最终专项 **29 passed / 10.63s**，完整后端 **296 passed / 3 skipped / 1已有告警 / 158.44s**。三项skip是专用真实进程故障套件，独立最终复跑3项通过见INT-013，未把skip计为pass。OpenAPI最终73路径/78操作/96schema，源码编译和CLI --help通过。

真实回环模型HTTP与受限PG测试核对每臂实际请求响应，B发送完整投影，A/C分别移除对应数据；影子之后账本仍为零。再由正式message进入真实Worker至SUCCEEDED，第四次模型请求与B逐值相等，正常Call/outbox/结果形成。旧v2会话在另发v3后仍成功并保持旧profile。两独立运行会话争用返回429，模型调用之间撤权后不再返回旧证据；全套还保留确认/绑定/UNKNOWN回归。

最终冻结源码已构建并集成本地镜像，影子开关仍false；无新增迁移，数据库仍0005。有限对照工具、合成真实协议测试和CLI归档校验不证明实际模型收益、统计显著性、稳定的外部resolver数据或银行验收。真实模型/金标/20功能及硬件报告继续待资源。
