# INT-033 功能答复模式与结果重复修复记录

日期：2026-10-12。范围是访前准备与客户列表答复的后端投影、模型汇总选择、功能展示契约和结果组件；没有修改规划策略、访问权限、业务调用协议或数据库结构。

## 问题与修复

- 访前一页纸的组合结果按 `{brief:{..., markdown:...}}` 保存。旧展示把完整对象交给 Markdown 组件，最终将 JSON 包装层打印成普通文本。现在复合功能从固定输出Schema确定唯一获准的 `brief.markdown` 字符串，只展示该字段；资料来源和缺失材料也写入草稿正文。缺失或非法路径不会回退到JSON。
- 客户筛选列表同时由模型总结，并作为结构化结果组件发送；当总结用了改写措辞时，按全文相等判断无法识别重复。现在系统依据发布版本的答复策略分路：数组查询跳过冗余模型总结，以表格显示通过Schema批准的结果；模型总结模式则只保留总结，不再附同一业务结果卡片。
- 功能配置增加三种答复路径：结构化展示、模型归纳、指定文本/Markdown字段。内容路径必须在该功能发布输出Schema中解析为字符串。旧不可变功能版本按原组件和Schema作有限推断，不改写历史快照；编辑时也保留推断出的原显示方式。

接口输出依旧经过现有有限Schema投影；该实现不是把原始API响应原封不动展示。即使选择Markdown，也只能展示已批准的字符串字段。

## 验证

在 `backend` 目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_output_contract.py tests/test_result_presentation.py tests/test_composite_boundaries.py -q
```

结果：**22 passed**。覆盖嵌套内容路径、非法路径、结构化对象列表、模型摘要与结果组件互斥、组合Markdown投影，以及智驭官网模型白名单对旧展示定义的兼容与篡改拒绝。

在 `frontend` 目录运行 `node .\node_modules\typescript\bin\tsc --noEmit`：通过。`git diff --check`：通过。

前端 Vitest 两个定向文件未能运行：`npm test -- --run tests/output-mode.test.tsx tests/result-card.test.tsx` 在该工作区找不到 Vitest 命令；直接运行 `node .\node_modules\vitest\vitest.mjs run ...` 后，启动被当前Windows `node_modules` 中缺少 `@rollup/rollup-win32-x64-msvc` 阻断，而目录里只有Linux Rollup原生包。本轮没有联网安装、改锁文件或替换共享依赖。现有 Node TypeScript 类型检查通过，但不把前端行为测试记为通过。

本轮没有创建独立Docker容器、网络、卷或数据库，因此没有测试资源需要清理。未重启现有服务、未进行真实模型调用、浏览器验证或Git推送；相关旧发布数据库集成路径未执行。

## 项目记录同步

更新了 `AGENTS.md`、`docs/project/BASELINE.md`、`REQUIREMENTS.md`、`TESTING.md`、`backend/CONTRACT.md`、`backend/OUTPUT_CONTRACT.md`、`frontend/README.md` 及任务/质量/交付 Skills。Skills仅追加稳定的结果展示和旧快照验证规则，不记录动态进度。

## 2026-10-12 本地容器刷新与交付

用户随后授权重启本地Docker服务并推送`main`。在当前Windows依赖目录缺少Rollup原生包的情况下，按TESTING登记命令在缓存的`node:22.16.0-alpine`中运行两个前端定向文件：**18/18通过**；容器使用`--rm --network none`，日志保存在忽略目录`output/INT-033-20261012/frontend-vitest.log`，容器已自动删除。

分别重建开发模式前端镜像和共享后端镜像，前者内置TypeScript检查及Console/Agent Web双构建均通过。随后仅重建`api`、`worker`、`ingest-worker`和`web`；未启动`migrate`、未执行数据库迁移，未操作PostgreSQL或`intent-local_intent_pgdata`。所有应用服务健康就绪；本地`/health`、`/console/`及现有`/chat/5d019540-83ca-489f-bc38-441dc0a64db3`入口返回HTTP 200。

已在推送前确认`main`与`origin/main`同步，代码及相关文档已提交并推送至`origin/main`。本地开发服务更新不代表生产部署或银行真实验收。该跟进只更新本记录、TASKS及基线中的Git事实；稳定Skills规则在本任务实现时已同步，本次没有新增Skill内容。
