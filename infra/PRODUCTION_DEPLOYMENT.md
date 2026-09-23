# 正式模式部署配置（候选模板）

此目录的 `production.compose.yaml` 是独立正式入口，不叠加根目录带开发身份、固定模型和本地口令的 `compose.yaml`。当前仅完成本地配置与协议检查；内网目标环境、模型、SSO、业务接口、宿主、性能与恢复须在资源到位后单独验收。

## 输入与前置条件

- 由目标环境提供已经审查的后端和正式模式 Web 镜像标识、受限 PostgreSQL app 角色及独立迁移 owner 角色。此 Compose 不创建数据库、不设置数据库口令，也不构建开发镜像。
- 通过获准秘密管理渠道提供 `.env.production` 等只在目标环境存在的配置文件；它被 Git 忽略。`.env.production.example` 只列字段，不包含实际地址或口令。不要把填好值的文件、容器配置输出、票据、原始日志或数据库归档纳入源码基线。
- `INTENT_ALLOWED_HOSTS` 是模型、身份、对象授权和业务连接器的**出站目标**白名单，所有必需服务端点的主机必须在内；它不是浏览器传入 Host 的白名单。批准的 TLS 入口须单独限制外部 Host、提供证书和转发 `/console/`、`/chat/`、`/embed/` 与 `/api/`。
- `INTENT_CORS_ORIGINS` 使用精确 HTTPS 浏览器来源；可信 iframe 宿主还须经应用发布策略、身份委托、一次性票据和动态 CSP 检查。管理页仍不得嵌入。
- 当前服务端只实现 Bearer/显式服务密钥访问模型、身份内省和对象授权。若实际行内资源要求 mTLS 或其他认证协议，必须先实现对应适配并以真实环境验证；不能把本模板视为兼容证据。

## 受控检查顺序

在具备授权和目标环境值时，从项目根运行：

```powershell
docker compose --env-file .env.production -f infra/production.compose.yaml config --quiet
docker compose --env-file .env.production -f infra/production.compose.yaml run --rm preflight
```

Compose 对缺失字段直接失败。`preflight` 再检查正式配置、可信身份与成员目录、数据库双角色能连接且均无超级用户或 BYPASSRLS 权限；它只读，不迁移业务数据。后端 API 和 Worker 在正式模式也会独立执行静态启动校验。正式 Web 镜像应使用 `infra/frontend.Dockerfile` 默认 `build` 制作，且确认容器内不存在 `/usr/share/nginx/html/console/dev-host.html`；不得传入 `BUILD_SCRIPT=build:development`。

完成候选审核、备份与恢复准备和实际授权后，部署操作才可使用同一 Compose 文件执行。`migrate` 在 `preflight` 成功后执行，API、在线 Worker 与独立 `ingest-worker` 在迁移成功后启动；`web` 只指向已健康的 API。新上传在正式模式进入受 RLS 保护的数据库任务队列；确认数据库容量、原始材料保留周期、空间接入并发和队列上限后再准入。部署成功仍需核对实际入口、身份、授权、真实业务和日志，不自动成为 M1–M3 验收。

版本、镜像及配置应关联同一候选记录。旧模型、工作流和功能依赖必须保留或明确不可用；权限撤销不能因回滚复活。密钥轮换、正式备份身份、RTO/RPO、网络准入和恢复演练由内网责任人确认。
