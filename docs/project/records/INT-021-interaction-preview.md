# INT-021 第一包：交互预览记录

日期：2026-09-28。范围：用户批准的“先预览、审核后正式集成”；整体INT-021为D类，本包独立预览为B类。基于当前未提交工作区，不虚构新提交或正式版本。

后续事实：用户已审核这三页预览并明确要求继续全部后续实现。以下记录保持第一包当时的范围与结果；其中“未实现账号/未修改后端”等陈述不代表后续正式源码状态，后续实现及完整候选证据见TASKS的INT-021。

## 产出与边界

- 独立React/TypeScript预览：`frontend/preview/`；入口 `http://127.0.0.1:5173/console/design-preview/`。三页分别为智能体、会话日志、功能库中的新建/余额示例编辑；头像打开个人中心。`frontend/tests/design-preview.test.tsx`为定向测试。
- 参考shadcn-admin的信息结构与视觉、Ant Design的数据录入规范、Langfuse会话组织；自主编写组件和样式，未复制第三方源码。已有React/lucide依赖继续使用，没有新增运行依赖。
- 会话按标题、智能体名称、轮次和状态组织；顶部快捷切换最终问答与阶段详情。参数表、就近必填提示、步骤切换、草稿保存恢复、未保存提醒和发布前验证均可操作。
- 全部为合成界面样例，草稿只存当前页面内存，刷新即清除。账户余额是完整编辑样例，其他展示行只查看说明；组合步骤、账号密码和Key展示外观及交互，不产生正式版本、凭据或业务调用。
- 正式Console、Agent Web、后端契约、数据库和运行模型没有由本包改造。此次没有实现真实本地登录、匿名Chat、Key换票、身份迁移及示例宿主；必须在界面审核后继续。旧未提交业务改动原样保留。

## 验证事实

| 检查 | 结果 |
|---|---|
| 预览定向测试 | 最终11/11通过，28.43秒；覆盖搜索、轮次、错误阶段、空态、处理过程快捷入口、表单校验、草稿恢复、跨步骤选择、未保存提醒、编辑对象及个人中心 |
| TypeScript与独立构建 | 通过；最终CSS窄屏修复后再次类型检查和独立构建 |
| 正式和开发双应用构建 | `npm run build`、`npm run build:development`均通过，Console/Agent Web四项制品产出；未修改双应用源码，预览不进入这些构建。已有zod PURE注释告警不阻断构建 |
| 桌面浏览器 | 已安装Playwright CLI + msedge，1440×960；首页、日志最终答复/阶段切换、必填反馈、步骤、模拟验证发布、头像个人中心、Tab与Escape通过 |
| 窄屏浏览器 | 390×844，日志列表/详情/无结果及清除筛选、功能步骤与草稿恢复检查；参数表保持局部横向滚动，最终页面宽度390/视口390，隐藏侧栏不可聚焦 |
| 请求与控制台 | 更新后每次加载仅3个静态资源请求（两次构建复读累计6个），业务/模型/账号API请求为0，控制台0错误0警告 |
| 主服务保护 | 主5容器ID及启动时间与修改前相同；健康、正式Console、独立预览均HTTP200；只新增nginx预览子目录，不重启或替换主服务 |
| 规范检查 | 文档代理检查12份文件的97条本地引用有效，19个Demo与26个DEC编号无重复，4个项目Skill格式校验通过；协调者补入实际命令及本轮证据 |
| 未执行范围 | 本包不运行后端/迁移/模型完整开发集或真实身份验收，因为没有变更这些实现。它们仍是INT-021整体候选的必需检查，不能用预览通过替代 |

实际命令在项目根执行，依赖为原已缓存的Node镜像及Linux node_modules，无联网安装：

```powershell
docker --config .local/docker run --name intent-int021-preview-check --rm --network none -v D:/pianwei/work/intent/frontend:/work -w /work node:22.16.0-alpine sh -c 'npm test -- tests/design-preview.test.tsx && npm run build:preview'
# 双应用构建在同类临时容器内依次执行 npm run build 与 npm run build:development。
# 最后仅CSS修复后再次执行：
docker --config .local/docker run --name intent-int021-preview-build --rm --network none -v D:/pianwei/work/intent/frontend:/work -w /work node:22.16.0-alpine npm run build:preview
```

保存证据：`output/INT-021/`的主服务检查和请求记录，以及`output/playwright/INT-021-*.png`。关键截图包括agents-desktop、logs-desktop、log-stages、function-step1/2/3、profile、logs-mobile、log-detail-mobile、empty-mobile、function-mobile。截图只含合成数据。

## 原失败及修复保留

- 初次测试8项通过；后续新增覆盖增至9项和11项。中途TypeScript因测试中误用`ByRoleOptions.exact`失败，删除错误选项后通过。一次新增窄屏测试误写`.log-workspace`导致1/11失败，改为真实`.log-workbench`后11/11通过；不把测试作者错误算产品通过。
- 独立复核发现草稿未保留、下拉选项跨步骤重置、所有功能编辑同一示例、窄屏无结果空白和模拟阶段与问题不一致。已分别修复；非编辑样例明确查看说明，不打开错误对象。
- 首次浏览器favicon404，补无请求的data图标后刷新为0错误。一次旧DOM引用失效，重新读取快照后继续。初次移动截图捕获侧栏动画，最终检查等待稳定布局。
- 移动参数表中的绝对定位无障碍标签导致页面溢出；为参数表建立定位容器，并让关闭的移动侧栏不可聚焦，重新构建后复验。
- Superdesign预检先因npm缓存权限失败；提升权限下载/执行第三方CLI被自动审批拒绝（未经核实外部代码及供应链/工作区副作用风险）。未重试或绕过，改用已安装React/lucide本地制作预览。没有Superdesign画布产出。

## 集成、文档与交付

静态文件复制到现有Web容器 `/usr/share/nginx/html/console/design-preview/`，主入口保持原样。重建Web容器后需按LOCAL_RUNBOOK重新复制预览；它不是正式构建的一部分。

方案入口/新基线、AGENTS、BASELINE、REQUIREMENTS、DECISIONS、ROADMAP、TASKS、TESTING、LOCAL_RUNBOOK与四个项目Skills同步。Skills记录本地账号、匿名公开渠道、用户Key、投影与恢复的后续工程约束，不保存动态状态。当前没有真实接口变更，OpenAPI和运行契约无需虚构更新；正式实现时再同步。

本轮容器均使用`--rm --network none`，没有创建独立网络或卷，不清理共享镜像/主卷。最终再次确认无本轮构建/测试容器残留。证据和主服务保留。未提交Git、未正式发版、未宣称银行验收通过。具体审核状态只维护于TASKS。
