# INT-010 领域语义与实体绑定工程证据

日期：2026-09-22。主协调者归并后端执行者的实际记录。代码位于本地未提交工作区；本记录不表示真实模型、银行实体服务或一期业务验收通过。

## 实现与兼容范围

后端已实现空间私有领域草稿及不可变版本、有限概念继承和关系方向、单位转换、日期规范化、三值前置条件、可信实体解析与显式选择，以及同会话成功结果的授权引用。共享定义见[领域与绑定契约](../../../backend/DOMAIN_CONTRACT.md)。

新功能显式选用 `finite-json-v2`，生成 Python 校验和 RDF/SHACL；旧版本仍使用原编译器。新旧功能可在同一发布中存在，旧快照不改写、不自动替换依赖。V2 只有明确的 `negated:false` 才允许继续执行；未知实体、条件或失效依赖不能授权业务调用。

实体标识来自服务器批准的版本化 resolver，不能直接使用模型或表单提议的业务 ID。选择使用本次问题的 opaque 选项；重复标签拒绝。同会话历史只引用仍获准的成功结果。派发、对账及读取问题再次验证权限，读取预算和上下文裁剪有明确上限；无法查询状态的 UNKNOWN 写操作不重放。

迁移 0003 增加领域版本及绑定、校验证据字段。模型版本和空间时区在新发布中固定。评测读取接口返回当前获准功能子集及实际同 revision 校验事实，不把模型提议当作执行证据。

## 已执行验证

| 操作 | 环境与结果 |
|---|---|
| 完整后端套件 | `backend/` 下 `./.venv/Scripts/python.exe -m pytest -q -x --tb=short`；受限 PostgreSQL `intent_test`，**184 passed / 32.17s**，一个既有 AnyIO 依赖警告。包含 102 平台测试和当时冻结的 82 评测测试 |
| 独立升级 | `./.venv/Scripts/python.exe scripts/verify_domain_upgrade.py`；初始为空的 `intent_domain_upgrade_test` 实际执行 0002→0003，通过。旧发布、会话、运行、调用值不变，UNKNOWN、revision=7、operation ID 保留，领域 RLS 与不可变触发器生效 |
| 静态与契约 | Python 源码编译通过；当前 OpenAPI 导出 56 条路径 |
| 本地镜像与运行库升级 | 先执行 `./scripts/backup-local.ps1 -Label before-domain-bindings`，再构建后端镜像并执行 `docker --config .local/docker compose --profile app up -d --wait api worker`；迁移成功，API 健康，Worker 启动 |
| 数据保留与单入口 | 演示库升级到 0003 前后均为 2 个智能体、4 个发布、8 个会话、7 个运行、7 个调用；5173 的 `/health` 返回 PostgreSQL 正常，OpenAPI 暴露领域与评测证据路由 |

升级前归档位于本机 `.local/backups/intent-20260922-164844-009-before-domain-bindings.dump`。未删除演示库或卷，未进行 Git 提交或生产发布。镜像内 `ZoneInfo('Asia/Shanghai')` 可读取时区资料。

用例包括编译正反例与 Python/SHACL 一致性、有限数值及转换溢出、授权解析和选择、历史来源与改口、跨空间拒绝、解析超时、撤权后禁止展示和调用、旧版本混合发布。实体服务使用真实回环 HTTP，载荷及身份为合成数据。完整后端细节见 [VALIDATION.md](../../../backend/VALIDATION.md)。

## 前端与浏览器集成

前端新增领域编辑与发布、精确版本选择、安全 resolver 引用、单一功能草稿、问题类型/标签/单位及白名单来源摘要。主动选择 v2 才启用领域绑定，旧功能不自动升级。定向 **13/13**、全部前端 **49/49** 通过；`npm run build` 与 `npm run build:development` 均成功，正式构建无开发宿主，开发构建包含开发宿主。测试报告为 `frontend/.cache/frontend-tests.json`；类型检查通过，构建只有既有 Zod 注释警告。

本地 web 镜像已重建并启动，Chrome 153 / Playwright CLI 实测：

1. 通过 Console 创建 `browser-fixture-domain`（名称“开发演示领域 · 合成资料”），标注合成来源，录入业务对象与结果概念，保存→校验→发布 v1，并查看只读发布记录。
2. 保存“合成草稿第二版，尚未发布。”后，v1 中的原描述仍保持不变。
3. 在现有功能草稿中临时选择 v2，可选择该领域精确 v1；空 resolver 目录明确显示服务端依赖。此处仅检查表单，未保存或改写原功能。

页面证据：[领域编辑与发布](../../../output/playwright/domain-published-version.png)。领域数据保留在本地演示库，非真实业务领域。两次定位超时来自验证脚本对已有值控件使用 `getByLabel` 精确匹配，改按实际 `textbox/combobox` 角色后完成操作，不计为产品失败。

浏览器另发现 HTML `pattern` 中裸连字符不兼容新版 Chrome 的 v 模式；服务器仍校验领域标识，但浏览器约束失效。已交由 INT-011 前端包修正，修复后的浏览器复核尚待执行。favicon 404 为既有非业务资源缺项。不得据 49 项组件测试声称已排除浏览器差异。

## 待验证范围

INT-011 浏览器复核已完成 Chrome pattern 修复：`local-domain_1` 实际 `checkValidity=true`，`Invalid Space` 为 false 且 patternMismatch=true；本次日志没有 pattern 解析异常，仅既有 favicon 404。没有保存额外领域。

真实模型语义质量、银行 resolver、20 功能评测、生产负载和本体消融仍待独立验证。实体选择/历史来源展示已有前端组件测试及后端实际回环 HTTP/PG 证据，真实银行多轮浏览器场景未执行。该实现不提供任意规则引擎、跨空间领域分发或任意相对日期解析。

后续公共契约或实现修改后按实际影响重跑；不同时间的专项数量不直接相加为一次全量结果。动态状态仅维护在 [TASKS.md](../TASKS.md)。
