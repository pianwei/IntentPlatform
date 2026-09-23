import { FunctionEditor } from "./FunctionEditor";
import { DomainPage } from "./DomainPage";
import { EvaluationArchiveViewer } from "./EvaluationArchiveViewer";
import { DocumentLibrary } from "./DocumentLibrary";
import { AgentEditor } from "./AgentEditor";
import { GovernancePage } from "./GovernancePage";
export { AgentEditor } from "./AgentEditor";
import { appPath } from "../../shared/application";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Blocks,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  FileText,
  FolderOpen,
  FlaskConical,
  LayoutGrid,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Badge, Busy, Empty, Login, Notice } from "../../shared/ui";
import type {
  Agent,
  Capability,
  FunctionDefinition,
  Identity,
  Release,
  Validation,
  Workspace,
} from "../../shared/types";
import { DEVELOPMENT_UI } from "../../shared/environment";
import { EvaluationPage } from "./EvaluationPage";
import { BusinessPlanningPage } from "./BusinessPlanningPage";
import { ModelSettingsPage } from "./ModelSettingsPage";
import { RunTracePage } from "./RunTracePage";

const statusLabels: Record<string, string> = {
  draft: "待完善",
  validated: "已验证",
  published: "已发布",
  disabled: "已停用",
};
type Page = "functions" | "documents" | "domains" | "planning" | "models" | "runs" | "agents" | "evaluations" | "audit" | "readiness" | "governance";

export function ConsoleApp() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [expired, setExpired] = useState(false);
  const api = useMemo(
    () =>
      new ApiClient("management", () => {
        setIdentity(null);
        setExpired(true);
      }),
    [],
  );
  if (!identity)
    return (
      <>
        {expired && <Notice kind="error">会话已失效，请重新连接。</Notice>}
        <Login
          api={api}
          onLogin={(value) => {
            setIdentity(value);
            setExpired(false);
          }}
        />
      </>
    );
  return (
    <ConsoleWorkspace
      key={identity.user_id}
      api={api}
      identity={identity}
      onLogout={async () => {
        try {
          await api.post("/api/v2/runtime/sessions/logout");
        } finally {
          api.clear();
          setIdentity(null);
        }
      }}
    />
  );
}
export function ConsoleWorkspace({
  api,
  identity,
  onLogout,
}: {
  api: ApiClient;
  identity: Identity;
  onLogout: () => Promise<void>;
}) {
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState("");
  const [page, setPage] = useState<Page>("functions");
  const [functions, setFunctions] = useState<Capability[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [audit, setAudit] = useState<unknown[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [search, setSearch] = useState("");
  const [editor, setEditorState] = useState<Capability | "new" | null>(null);
  const editorEpoch = useRef(0);
  const editorGeneration = editorEpoch.current;
  function setEditor(value: Capability | "new" | null) { ++editorEpoch.current; setEditorState(value); }
  const [agentEditor, setAgentEditor] = useState<Agent | "new" | null>(null);
  const [releases, setReleases] = useState<{
    agent: Agent;
    items: Release[];
  } | null>(null);
  const loadGeneration = useRef(0);
  const currentWorkspace = useRef(workspace);
  currentWorkspace.current = workspace;
  const activeSpace = spaces.find((item) => item.id === workspace);
  const canManage =
    activeSpace?.roles.some((role) =>
      ["admin", "maintainer", "publisher"].includes(role),
    ) ?? false;
  const reload = useCallback(async () => {
    if (!workspace || currentWorkspace.current !== workspace) return;
    const epoch = ++loadGeneration.current;
    setBusy(true);
    setError("");
    try {
      const [f, a] = await Promise.all([
        api.get<Capability[]>(`${management(workspace)}/functions`),
        api.get<Agent[]>(`${management(workspace)}/agents`),
      ]);
      if (
        epoch !== loadGeneration.current ||
        currentWorkspace.current !== workspace
      )
        return;
      setFunctions(f);
      setAgents(a);
    } catch (e) {
      if (
        epoch === loadGeneration.current &&
        currentWorkspace.current === workspace
      )
        setError(errorText(e));
    } finally {
      if (
        epoch === loadGeneration.current &&
        currentWorkspace.current === workspace
      )
        setBusy(false);
    }
  }, [api, workspace]);
  useEffect(() => {
    let active = true;
    api
      .get<Workspace[]>("/api/v2/management/workspaces")
      .then((items) => {
        if (active) {
          setSpaces(items);
          setWorkspace(items[0]?.id ?? "");
        }
      })
      .catch((e) => active && setError(errorText(e)));
    return () => {
      active = false;
    };
  }, [api]);
  useEffect(() => {
    setFunctions([]);
    setAgents([]);
    setEditor(null);
    setAgentEditor(null);
    setReleases(null);
    void reload();
  }, [reload]);
  useEffect(() => {
    let active = true;
    if (page === "audit" && workspace) {
      setAudit([]);
      api
        .get<unknown[]>(`${management(workspace)}/audit`)
        .then((items) => active && setAudit(items))
        .catch((e) => active && setError(errorText(e)));
    }
    return () => {
      active = false;
    };
  }, [api, page, workspace]);
  async function action(fn: () => Promise<unknown>, message: string) {
    setError("");
    setSuccess("");
    setBusy(true);
    try {
      await fn();
      await reload();
      setSuccess(message);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const filtered = functions.filter((item) =>
    `${item.name} ${item.description}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const titles: Record<Page, string> = {
    functions: "功能目录",
    domains: "领域管理",
    planning: "业务动作与 Skills",
    models: "模型配置",
    runs: "运行日志",
    documents: "来源文档",
    agents: "场景智能体",
    evaluations: "评测诊断",
    audit: "操作审计",
    readiness: "接入准备",
    governance: "空间治理",
  };
  return (
    <div className="console-layout">
      <aside className="sidebar">
        <a className="brand" href="/console/">
          <span className="brand-icon">i</span>
          <span>
            意图中台<small>INTENT PLATFORM</small>
          </span>
        </a>
        <div className="workspace-label">工作空间</div>
        <div className="workspace-switch">
          <FolderOpen size={18} />
          <select
            aria-label="切换工作空间"
            value={workspace}
            onChange={(e) => setWorkspace(e.target.value)}
          >
            {spaces.map((space) => (
              <option key={space.id} value={space.id}>
                {space.name}
              </option>
            ))}
          </select>
          <ChevronDown size={14} />
        </div>
        <div className="nav-label">管理工作台</div>
        <nav>
          {(
            [
              { key: "functions", icon: Blocks, label: "功能目录" },
              { key: "domains", icon: LayoutGrid, label: "领域管理" },
              { key: "planning", icon: Blocks, label: "业务动作与 Skills" },
              { key: "models", icon: Settings2, label: "模型配置" },
              { key: "runs", icon: Activity, label: "运行日志" },
              { key: "documents", icon: FileText, label: "来源文档" },
              { key: "agents", icon: Sparkles, label: "场景智能体" },
              { key: "evaluations", icon: FlaskConical, label: "评测诊断" },
              { key: "audit", icon: Activity, label: "操作审计" },
              { key: "governance", icon: Settings2, label: "空间治理" },
              { key: "readiness", icon: ShieldCheck, label: "接入准备" },
            ] as const
          ).map((item) => (
            <button
              key={item.key}
              className={page === item.key ? "active" : ""}
              onClick={() => setPage(item.key)}
            >
              <item.icon size={19} />
              {item.label}
              {page === item.key && <ChevronRight size={15} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="environment">
            <span className="status-dot" />
            {identity.fixture_mode ? "本地开发环境" : "内网工作环境"}
          </div>
          <p>
            权限边界由服务端校验
            <br />
            操作与发布保留审计记录
          </p>
          <button
            onClick={() => void onLogout().catch((e) => setError(errorText(e)))}
          >
            <LogOut size={16} />
            退出当前身份
          </button>
        </div>
      </aside>
      <div className="console-main">
        <header className="topbar">
          <div>
            工作台 <ChevronRight size={14} /> <strong>{titles[page]}</strong>
          </div>
          <div className="user-chip">
            <span className="avatar">{identity.user_id[0].toUpperCase()}</span>
            {identity.user_id}
            <Badge>{activeSpace?.roles.join(" · ") || "权限待确认"}</Badge>
          </div>
        </header>
        <main className="content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">WORKSPACE / {workspace || "未选择"}</div>
              <h1>{titles[page]}</h1>
              <p>
                {page === "functions"
                  ? "把业务能力接入空间，确认映射，再验证和发布。"
                  : page === "domains"
                    ? "维护领域概念、关系和单位，发布可供功能精确绑定的不可变版本。"
                    : page === "documents"
                      ? "保留接口与业务资料的原文修订、章节和原始文件，追溯每一条引用。"
                    : page === "planning"
                      ? "将实体、动作和有限业务规则组合为可验证的任务链。"
                    : page === "models"
                      ? "配置获准模型地址与明确版本，测试连接及结构化输出。"
                    : page === "runs"
                      ? "按三层决策与业务步骤查看结构化运行事实。"
                  : page === "agents"
                    ? "组合已发布的业务功能，形成可独立运行和嵌入的智能体。"
                    : page === "evaluations"
                      ? "使用自行提供的用例诊断已发布智能体的功能路由，保留全部结果与分母。"
                      : page === "audit"
                        ? "查看当前空间的真实操作记录与版本变更。"
                        : page === "governance"
                          ? "管理空间成员、获准引用与运行限额，查看每次执行尝试的实际耗时。"
                        : "明确真实接入与验收所需的材料、身份及环境。"}
              </p>
            </div>
            <button
              className="secondary"
              disabled={busy || !workspace}
              onClick={() => void reload()}
            >
              <RefreshCw size={16} />
              刷新
            </button>
          </div>
          {identity.fixture_mode && (
            <Notice>
              当前为本地开发。开发夹具用于验证接入与运行流程，不计入真实业务功能验收。
            </Notice>
          )}
          {error && <Notice kind="error">{error}</Notice>}
          {success && <Notice kind="success">{success}</Notice>}
          {page === "documents" && workspace && <DocumentLibrary key={workspace} api={api} workspace={workspace} writable={canManage} />}
          {page === "domains" && workspace && <DomainPage key={workspace} api={api} workspace={workspace} writable={canManage} publishable={!!activeSpace?.roles.some(r => ["admin", "publisher"].includes(r))} />}
          {page === "planning" && workspace && <BusinessPlanningPage key={workspace} api={api} workspace={workspace} writable={canManage} publishable={!!activeSpace?.roles.some(r => ["admin", "publisher"].includes(r))} admin={!!activeSpace?.roles.includes("admin")} development={DEVELOPMENT_UI} onSeeded={reload} />}
          {page === "models" && workspace && <ModelSettingsPage key={workspace} api={api} workspace={workspace} admin={!!activeSpace?.roles.includes("admin")} />}
          {page === "runs" && workspace && <RunTracePage key={workspace} api={api} workspace={workspace} admin={!!activeSpace?.roles.includes("admin")} />}
          {page === "governance" && workspace && <GovernancePage key={workspace} api={api} workspace={workspace} userId={identity.user_id} admin={!!activeSpace?.roles.includes("admin")} canManage={canManage} />}
          {page === "functions" && (
            <>
              <div className="section-tabs">
                <span className="selected">
                  全部功能 <b>{functions.length}</b>
                </span>
                <span>
                  已发布{" "}
                  {functions.filter((f) => f.status === "published").length}
                </span>
                <span>
                  待完善 {functions.filter((f) => f.status === "draft").length}
                </span>
              </div>
              <section className="panel">
                <div className="table-toolbar">
                  <div className="search-input">
                    <Search size={17} />
                    <input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="搜索功能名称、业务描述"
                      aria-label="搜索功能"
                    />
                  </div>
                  <button
                    className="primary"
                    disabled={!canManage || !workspace}
                    onClick={() => setEditor("new")}
                  >
                    <Plus size={17} />
                    接入新功能
                  </button>
                </div>
                {busy && functions.length === 0 ? (
                  <Busy />
                ) : filtered.length === 0 ? (
                  <Empty
                    title={
                      functions.length
                        ? "没有匹配的功能"
                        : "从第一个真实业务功能开始"
                    }
                  >
                    接入 HTTP、Dify 或 HiAgent 接口及说明，建立语义和执行映射。
                  </Empty>
                ) : (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>功能名称 / 描述</th>
                          <th>接入方式</th>
                          <th>运行边界</th>
                          <th>状态</th>
                          <th>版本</th>
                          <th className="right">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filtered.map((item) => (
                          <tr key={item.id}>
                            <td>
                              <div className="table-title">
                                <span className="table-icon">
                                  <Blocks size={18} />
                                </span>
                                <div>
                                  <strong>{item.name}</strong>
                                  <p>{item.description || "暂无描述"}</p>
                                  {item.is_fixture && (
                                    <Badge tone="warning">开发夹具</Badge>
                                  )}
                                </div>
                              </div>
                            </td>
                            <td>
                              <Badge>
                                {item.definition.connector.kind.toUpperCase()}
                              </Badge>
                            </td>
                            <td>
                              {item.definition.effect === "WRITE"
                                ? "写操作 · 需确认"
                                : "只读查询"}
                            </td>
                            <td>
                              <Badge
                                tone={
                                  item.status === "published"
                                    ? "success"
                                    : item.status === "validated"
                                      ? "info"
                                      : "neutral"
                                }
                              >
                                {statusLabels[item.status] || item.status}
                              </Badge>
                            </td>
                            <td>
                              {item.latest_version
                                ? `v${item.latest_version}`
                                : "未发布"}
                              <small className="block muted">
                                草稿 r{item.revision}
                              </small>
                            </td>
                            <td className="right">
                              <button
                                className="text-button"
                                onClick={() => setEditor(item)}
                              >
                                {canManage ? "管理映射" : "查看"}
                                <ChevronRight size={14} />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <footer className="table-footer">
                  <span>{filtered.length} 个功能 · 数据来自当前空间</span>
                  <span>发布以最新验证结果为准</span>
                </footer>
              </section>
              <div className="help-strip">
                <div className="help-icon">
                  <FileText size={24} />
                </div>
                <div>
                  <h3>让业务定义成为可验证的能力</h3>
                  <p>接口说明 → 语义与动力映射 → 契约验证 → 不可变发布版本</p>
                </div>
                <button
                  className="text-button"
                  onClick={() => setPage("readiness")}
                >
                  查看接入准备
                  <ArrowUpRight size={16} />
                </button>
              </div>
            </>
          )}
          {page === "agents" && (
            <>
              <div className="table-toolbar">
                <span className="muted">{agents.length} 个智能体</span>
                <button
                  className="primary"
                  disabled={!canManage}
                  onClick={() => setAgentEditor("new")}
                >
                  <Plus size={17} />
                  创建智能体
                </button>
              </div>
              <div className="agent-grid">
                {agents.map((agent) => (
                  <section className="panel agent-tile" key={agent.id}>
                    <span className="agent-symbol">
                      <Sparkles size={24} />
                    </span>
                    <Badge
                      tone={agent.current_release_id ? "success" : "neutral"}
                    >
                      {agent.current_release_id ? "已有发布" : "草稿"}
                    </Badge>
                    <h2>{agent.name}</h2>
                    <p>
                      {agent.capability_ids?.length ?? 0} 个功能 ·{" "}
                      {agent.fixture_mode ? "开发夹具组合" : "业务功能组合"}
                    </p>
                    <code>{agent.id}</code>
                    <div className="agent-actions">
                      <button
                        className="secondary"
                        disabled={!canManage}
                        onClick={() => setAgentEditor(agent)}
                      >
                        编辑组合
                      </button>
                      <button
                        className="secondary"
                        onClick={() =>
                          void api
                            .get<Release[]>(
                              `${management(workspace)}/agents/${agent.id}/releases`,
                            )
                            .then((items) => setReleases({ agent, items }))
                            .catch((e) => setError(errorText(e)))
                        }
                      >
                        版本
                      </button>
                      <button
                        className="primary"
                        disabled={!canManage || busy}
                        onClick={() =>
                          void action(
                            () =>
                              api.post(
                                `${management(workspace)}/agents/${agent.id}/publish`,
                                { revision: agent.revision },
                              ),
                            "智能体发布成功；新会话使用新版本。",
                          )
                        }
                      >
                        发布
                      </button>
                    </div>
                    {agent.current_release_id && (
                      <a
                        className="text-button"
                        href={appPath(agent.public_id ?? agent.id)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        打开独立运行页 <ArrowUpRight size={15} />
                      </a>
                    )}
                  </section>
                ))}
              </div>
              {!agents.length && (
                <Empty title="还没有场景智能体">
                  先发布功能，再选择一组功能构建智能体。
                </Empty>
              )}
            </>
          )}
          {page === "evaluations" && (
            <>
            <EvaluationPage
              key={workspace}
              api={api}
              workspace={workspace}
              agents={agents}
              functions={functions}
              writable={
                activeSpace?.roles.some((role) =>
                  ["admin", "publisher"].includes(role),
                ) ?? false
              }
              fixtureMode={identity.fixture_mode}
            />
            <EvaluationArchiveViewer key={`archive-${workspace}`} />
            </>
          )}
          {page === "audit" && (
            <section className="panel audit-panel">
              {audit.length ? (
                <pre>{JSON.stringify(audit, null, 2)}</pre>
              ) : (
                <Empty title="暂无审计记录">
                  当前空间的接入、验证与发布操作将在此留痕。
                </Empty>
              )}
            </section>
          )}
          {page === "readiness" && (
            <div className="readiness-grid">
              {[
                {
                  title: "业务功能材料",
                  text: "真实接口、业务用途、负责人、参数与成功/失败示例。开发夹具不替代真实功能。",
                  icon: FileText,
                },
                {
                  title: "语义与执行边界",
                  text: "业务对象、动作、结果类型、字段来源、前置条件、只读或写操作、幂等与未知结果处置。",
                  icon: Settings2,
                },
                {
                  title: "身份与宿主",
                  text: "SSO/可信身份、空间角色、允许的宿主 Origin、退出与权限撤销策略。",
                  icon: ShieldCheck,
                },
                {
                  title: "真实验证环境",
                  text: "模型与硬件、真实测试 API、业务金标与封存评测集。当前开发诊断不等于验收。",
                  icon: Activity,
                },
              ].map((item) => (
                <section className="panel readiness-card" key={item.title}>
                  <item.icon size={24} />
                  <h2>{item.title}</h2>
                  <p>{item.text}</p>
                  <Badge tone="warning">按真实材料确认</Badge>
                </section>
              ))}
              {DEVELOPMENT_UI && (
                <section className="panel readiness-card dev-panel">
                  <Code2 size={24} />
                  <h2>本地流程验证夹具</h2>
                  <p>
                    显式创建两个开发功能及一个智能体，便于验证发布、补参和确认。不计入真实20功能。
                  </p>
                  <button
                    className="secondary"
                    disabled={!canManage || busy}
                    onClick={() =>
                      void action(
                        () => api.post("/api/v2/dev/seed"),
                        "开发夹具已准备，可在功能目录与场景智能体中查看。",
                      )
                    }
                  >
                    准备两个开发夹具
                  </button>
                  <a
                    href="/console/dev-host.html"
                    className="text-button"
                    target="_blank"
                    rel="noreferrer"
                  >
                    打开 iframe 联调宿主 <ArrowUpRight size={15} />
                  </a>
                </section>
              )}
            </div>
          )}
        </main>
        <footer className="page-footer">
          意图中台 · 自助接入 / 本体映射 / 可信执行<span>Console</span>
        </footer>
      </div>
      {editor && (
        <FunctionEditor
          key={editor === "new" ? "new" : editor.id}
          api={api}
          workspace={workspace}
          item={editor === "new" ? null : editor}
          writable={canManage}
          publishable={!!activeSpace?.roles.some(role => ["admin", "publisher"].includes(role))}
          onClose={() => setEditor(null)}
          onSaved={async (item) => {
            if (editorEpoch.current !== editorGeneration || currentWorkspace.current !== workspace || item.workspace_id !== workspace) return;
            setEditor(item);
            await reload();
          }}
        />
      )}
      {agentEditor && (
        <AgentEditor
          api={api}
          workspace={workspace}
          item={agentEditor === "new" ? null : agentEditor}
          functions={functions}
          publishable={!!activeSpace?.roles.some(role => ["admin", "publisher"].includes(role))}
          onClose={() => setAgentEditor(null)}
          onSaved={async () => {
            await reload();
          }}
        />
      )}
      {releases && (
        <div className="modal-backdrop">
          <section className="modal">
            <div className="drawer-heading">
              <h2>{releases.agent.name} · 发布版本</h2>
              <button aria-label="关闭版本" onClick={() => setReleases(null)}>
                <X size={20} />
              </button>
            </div>
            {releases.items.length ? (
              releases.items.map((release) => (
                <div
                  className="release-row"
                  key={release.release_id || release.id}
                >
                  <div>
                    <strong>v{release.version}</strong>
                    <code>{release.release_id || release.id}</code>
                    <small>{release.capabilities.length} 个功能版本</small>
                  </div>
                  <button
                    className="secondary"
                    disabled={!canManage || busy}
                    onClick={() =>
                      void action(
                        () =>
                          api.post(
                            `${management(workspace)}/agents/${releases.agent.id}/rollback`,
                            { release_id: release.release_id || release.id },
                          ),
                        "默认发布版本已切换；既有会话保持原版本。",
                      )
                    }
                  >
                    用于新会话
                  </button>
                </div>
              ))
            ) : (
              <Empty title="暂无发布版本" />
            )}
          </section>
        </div>
      )}
    </div>
  );
}
