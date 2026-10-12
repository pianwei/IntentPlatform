# INT-025 聊天入口访问修复

2026-10-09，B类局部修复；无身份、渠道权限、发布或模型策略变更。

## 复现与原因

用户反馈nginx 403。浏览器曾打开“HTTP文档联调 · 合成应用”，其发布chat=true，但实时chat/embed均false，空间正常。页面策略因此返回403，公开配置返回404；服务健康检查正常。这是已关闭应用仍显示“打开应用”的界面缺陷，不能通过恢复旧渠道规避。

“三层意图演示助手”仍为v9、local-v1且chat=true，页面与公开配置200。测试浏览器另有旧匿名恢复403；默认访客有效期7天。明确点击“放弃恢复，重新开始”后正常进入输入界面，刷新也可恢复新访问。

## 修复与验证

- 管理列表及智能体详情顶部、测试、发布页统一使用已发布渠道与实时开关，关闭时无公开链接、禁止试聊并说明“聊天已关闭”；草稿修改不影响当前入口。
- 仅对匿名恢复的两类已知403错误显示中文原因与明确新会话操作，不自动替换身份，不改变后端期限，不影响其他应用恢复凭据。
- Vitest六个文件49项通过：chat-entry-availability、goal-correction、console-boundaries、local-identity-ui、published-preview-version、application。覆盖关闭边界、草稿隔离、手动新会话、其他应用及不重发旧业务。
- TypeScript及正式/开发双应用构建通过。保留依赖注释和Console体积的既有构建警告，未视为构建失败。
- 主服务浏览器断言：关闭应用提示可见、链接数量0、试聊禁用；正常Demo链接及试聊可用。正常Chat输入界面和刷新恢复可用。HTTP复核正常Chat/API/health为200、关闭渠道仍403。
- 本次不发送模型或业务调用；未复跑INT-024所需模型评测，不宣称完成该项验收。

证据：`output/INT-025/frontend-final.log`、`production-build.log`、`web-build.log`、`browser-entries.txt`、`browser-chat.txt`、`http-after.json`。

## 本地集成与恢复

仅更新`intent-local-web-1`；API、Worker、接入Worker及PostgreSQL前后启动时间一致，见`main-services-before.txt`/`main-services-after.txt`。scope-v4后端修改未构建或部署，业务快照与渠道未改。

旧Web容器对应镜像已不在缓存，无法标记镜像备份；因此更新前保存实际静态制品及nginx配置到`.local/backups/INT025-web-before/`。如需恢复界面，可将其中`html/.`复制回Web的`/usr/share/nginx/html/`、将`default.conf`复制回`/etc/nginx/conf.d/default.conf`，检查`nginx -t`后重载；不操作数据库或发布记录。

独立测试与构建容器均使用`--rm --network none`，执行结束按INT025标签检查容器、网络、卷无残留。没有生成SHA256检验步骤或文件。

同步TASKS与LOCAL_RUNBOOK；接口/需求范围未变。交付及质量Skills的稳定约束仍适用，无动态状态写入Skills。

## 第二次截图反馈

同日截图显示旧身份发布英文错误。此时应用实时Chat已变为开启，故能够加载页面，但匿名会话创建被旧身份快照拒绝；这与第一轮网关403是两层问题。

数据库只读确认该HTTP样例唯一功能`disabled=true`；[INT-011历史记录](INT-011-source-import.md)说明临时接口出站授权已撤销。仅迁移身份无法恢复业务，本次未自动迁移或重新授权。前端改为中文解释，并移除对该错误无效的重连/放弃恢复按钮；其他可恢复错误保持原行为，用户端不增加管理入口。

三文件29项定向检查、类型检查、开发模式双应用构建通过。浏览器实测旧应用中文提示及无重连按钮、当前三层演示助手正常输入界面；仅更新Web。证据保存于`output/INT-025/legacy-release/`。本轮没有业务调用、后端或模型修改，未执行模型评测和数据库迁移；不把页面提示修复写成HTTP样例业务恢复。

旧Web镜像无法直接重新标记，更新前实际静态制品备份至`.local/backups/INT025-before-legacy-message/html/`；网关配置未修改。临时测试容器自动删除，未创建测试网络或卷。
