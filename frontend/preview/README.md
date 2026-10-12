# INT-021 交互预览

这是独立审核原型，不是正式 Console 或身份功能的实现。全部样例内置，保存、测试、发布及账号操作只改变当前 React 页面状态；刷新重置，不连接业务 API、模型或数据库。请勿输入真实密码、Key 或业务数据。

用户已审核本轮三页并授权后续正式实现；此目录作为当时预览保留，不替代正式Console。当前账号、匿名Chat、宿主Key和恢复说明见[双前端README](../README.md)，整体状态与证据由TASKS维护。

## 查看与构建

- 独立构建：在 frontend 执行 `npm run build:preview`，输出 `dist/preview`。
- 将该目录作为 `/console/design-preview/` 静态目录提供；主 Console 和 Agent Web 不导入本预览，也不在 `build` / `build:development` 中发布预览。
- 三个可直接打开的页面：`#agents`、`#logs`、`#functions`。功能库点击“新建功能”或账户余额行的“编辑”进入分步表单；其他行仅展示说明，不错误打开别的功能。
- 审核路径：智能体卡片/筛选/头像 → 日志会话/轮次/三层展开/异常 → 功能分步/校验/试运行/发布预览。个人中心包括资料、权限、密码与Key交互外观，均未接入真实认证。已保存草稿按空间及新建/余额编辑分别保留于内存，页面刷新后清除。
- 当前主服务的静态部署与重建后恢复命令见[本地运行手册](../../docs/operations/LOCAL_RUNBOOK.md#int-021-独立界面审核预览)。

## 参考与复用

- [shadcn-admin](https://github.com/satnaing/shadcn-admin)：侧栏、列表、账号菜单的信息组织和克制的后台视觉。
- [Ant Design 数据录入](https://ant.design/docs/spec/data-entry/)：标签、必填项、样例、就近反馈。
- [Langfuse Sessions](https://langfuse.com/docs/observability/features/sessions)：会话组织多个问答运行，概览之后查看细节。

本包自主编写 React/CSS，未复制上述项目源代码。复用已有 React 与 lucide-react 图标依赖，其许可随原项目依赖保留。未下载或执行新的设计CLI；Superdesign预检提升权限执行被自动审批拒绝，未绕过。
