# INT-014 E0 工程基线与连接器安全证据

日期：2026-09-23。等级 C。本记录只覆盖本地工程、合成配置及回环协议，不计真实银行接口、模型、身份、宿主或正式部署验收。验证对象为首个本地 Git 基线 `8bc4644` 的源码与锁定依赖。

## 产出和边界

- `backend/src/intent_platform/network.py` 在流式读取时限制原始与解压后响应；Dify 未识别状态修正为 UNKNOWN，HiAgent 继续按显式集合判定，HTTP-v2 拒绝 `Accept-Encoding` 等传输保留头。具体有限协议见 `backend/CONNECTOR_TRANSPORT_CONTRACT.md`。
- 独立 `infra/production.compose.yaml`、`.env.production.example`、`backend/src/intent_platform/production_preflight.py` 和部署说明。正式配置缺字段、开发身份/模型、开发数据库默认值或不可信目录均拒绝；只读预检还检查数据库双角色。正式 Compose 不继承开发 Compose，数据库、镜像、TLS 入口及秘密由目标环境提供。
- `.gitignore` 排除 `.env.*` 实值、`.local/`、`output/`、`backend/build/`、egg-info、前端生成物和原始 Word 方案文件；原文件保留在工作区。源码暂存清单必须由协调者复核后再提交。

## 实际验证

| 范围 | 入口及环境 | 结果 |
|---|---|---|
| 连接器传输与相邻安全边界 | 回环 HTTP 服务；`backend/tests/test_connector_transport.py` 等定向套件 | 64/64 通过。覆盖 HTTP 错误、分块、压缩及解压超限、重定向、Dify/HiAgent 成功/失败/进行中/未知/状态查询、写超时单次发送。均为合成响应。 |
| 正式模式配置 | `backend/tests/test_production_config.py` 等定向检查，合成非本地配置 | 25 项通过；完整正式配置可解析，缺字段与开发默认值被拒绝，错误消息不打印模拟秘密。只读角色预检的逻辑用例通过，未连接目标数据库。 |
| 完整本地软件入口 | 项目根运行 `./scripts/verify-local.ps1 all`；Docker PostgreSQL 受限测试库，锁定前端依赖 | 后端 **339 passed / 3 skipped / 1 warning**；前端 **75 passed / 1 skipped**，Console 与 Agent Web 类型检查及正式模式构建均通过。后端 warning 是现有 Starlette/AnyIO 弃用提示；三项 skip 为需独立进程故障脚本的场景，前端一项 skip 为已有跳过用例。 |
| 正式前端开发入口排除 | 检查正式构建的 `frontend/dist/console/dev-host.html` | 文件不存在；前端构建产物仍需与后续候选镜像和 Git 版本一一对应。 |
| 正式 Compose 解析与 Git 排除 | 合成配置运行 Compose `config --quiet`；空模板复核失败；`git check-ignore` 检查本地值、运行输出与生成物 | 配置可解析且缺配拒绝；原始运行数据及本地凭据不进入首个源码暂存清单。未执行真实目标环境启动。 |

起初 Docker 引擎停止，第一次全量数据库测试因 54329 不可达中止；启动 Docker Desktop 后重新运行上述完整入口通过。该初次失败是环境不可用，不记录为代码通过。E3 需在最终候选上另跑独立三进程故障、升级/恢复、离线冷启与合成负载；正式 Dify、HiAgent、行内模型与目标环境保持待验证。
