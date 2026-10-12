import { useEffect, useState } from "react";
import { Activity, ArrowDownUp, ArrowRight, ArrowUpRight, Blocks, Bot, Check, ChevronDown, ChevronRight, ChevronsUpDown, Clock3, HelpCircle, KeyRound, LayoutGrid, List, LogOut, Menu, MessageSquare, MoreHorizontal, Plus, Search, Settings2, ShieldCheck, UsersRound, X } from "lucide-react";
import { agents, capabilityNames } from "./data";
import { Help, Modal, PageTitle, Status } from "./components";
import { Logs } from "./Logs";
import { PreviewFunctionEditor, type PreviewDraft } from "./FunctionEditor";
import { Profile } from "./Profile";

type Page = "agents" | "functions" | "logs";
const pages: Record<Page, string> = { agents: "智能体", functions: "功能库", logs: "运行日志" };
export function DesignPreview() {
  const [page, setPage] = useState<Page>(() => { const p = location.hash.slice(1); return p === "logs" || p === "functions" ? p : "agents"; });
  const [editing, setEditing] = useState(false);
  const [existing, setExisting] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, PreviewDraft>>({});
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [toast, setToast] = useState("");
  const [profile, setProfile] = useState(false);
  const [userMenu, setUserMenu] = useState(false);
  const [spaceMenu, setSpaceMenu] = useState(false);
  const [workspace, setWorkspace] = useState("业务协作空间");
  const [density, setDensity] = useState("舒适");
  const [mobileNav, setMobileNav] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("全部");
  const [grid, setGrid] = useState(true);
  const [dialog, setDialog] = useState<{ title: string; text: string; agent?: string } | null>(null);
  const [agentName, setAgentName] = useState("");
  useEffect(() => { if (!toast) return; const timeout = setTimeout(() => setToast(""), 3600); return () => clearTimeout(timeout); }, [toast]);
  useEffect(() => {
    const onUnload = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [dirty]);
  useEffect(() => {
    const onEscape = (e: KeyboardEvent) => { if (e.key === "Escape") { setUserMenu(false); setSpaceMenu(false); setMobileNav(false); } };
    document.addEventListener("keydown", onEscape); return () => document.removeEventListener("keydown", onEscape);
  }, []);
  function guarded(action: () => void) { if (dirty) setPending(() => action); else action(); }
  function navigate(next: Page) { guarded(() => { setPage(next); setEditing(false); setQuery(""); setMobileNav(false); history.replaceState(null, "", `#${next}`); }); }
  function edit(isExisting: boolean) { setEditing(true); setExisting(isExisting); setDirty(false); }
  const filtered = agents.filter(a => a.name.includes(query) && (filter === "全部" || a.state === filter));
  return <div className={`preview-app ${density === "紧凑" ? "compact" : ""}`}>
    {mobileNav && <button className="nav-backdrop" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
      <a className="brand" href="#agents" onClick={e => { e.preventDefault(); navigate("agents"); }}><span className="brand-symbol"><Blocks size={20} /></span><strong>意图中台</strong></a>
      <div className="workspace-control"><button className="workspace-switch" aria-expanded={spaceMenu} onClick={() => setSpaceMenu(!spaceMenu)}><span className="workspace-symbol">业</span><span>{workspace}</span><ChevronsUpDown size={14} /></button>
        {spaceMenu && <div className="popover space-popover">{["业务协作空间", "产品验证空间"].map(s => <button key={s} onClick={() => guarded(() => { setWorkspace(s); setSpaceMenu(false); setToast(`已切换至${s}（预览样例）`); })}>{s}{s === workspace && <Check size={14} />}</button>)}</div>}
      </div>
      <nav className="primary-nav" aria-label="主导航">{([["agents", Bot], ["functions", Blocks], ["logs", Activity]] as const).map(([key, Icon]) => <button key={key} aria-current={page === key ? "page" : undefined} className={page === key ? "active" : ""} onClick={() => navigate(key)}><Icon size={18} /><span>{pages[key]}</span>{key === "agents" && <small>3</small>}</button>)}</nav>
      <div className="nav-section-label">设置</div><nav className="settings-nav" aria-label="设置">{[["空间管理", UsersRound, "管理空间名称、管理员与成员。此入口将在正式版本中沿用已有空间管理能力。"], ["模型配置", Settings2, "维护模型连接与版本。当前已配置的模型保持不变，预览不访问模型服务。"], ["账号管理", ShieldCheck, "由平台管理员创建、停用账号和重置密码。空间管理员只管理本空间成员。"]].map(([label, Icon, text]) => { const I = Icon as typeof UsersRound; return <button key={String(label)} onClick={() => setDialog({ title: String(label), text: String(text) })}><I size={17} /><span>{String(label)}</span></button>; })}</nav>
      <div className="sidebar-bottom"><button onClick={() => setDialog({ title: "关于交互预览", text: "本预览仅使用模拟数据，所有保存、验证和发布仅发生在当前页面。请审核智能体首页、会话日志、分步功能编辑及个人中心；确认后再接入正式服务。" })}><HelpCircle size={16} /><span>使用帮助</span></button><span className="preview-label">交互预览 · 01</span></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-only" aria-label="展开导航" onClick={() => setMobileNav(true)}><Menu size={19} /></button><span>工作台</span><ChevronRight size={13} /><strong>{pages[page]}</strong>{editing && <><ChevronRight size={13} /><strong>{existing ? "编辑功能" : "新建功能"}</strong></>}</div><div className="account-control"><button className="avatar-button" aria-label="打开用户菜单" aria-expanded={userMenu} onClick={() => setUserMenu(!userMenu)}><span className="avatar">陈</span><ChevronDown size={13} /></button>{userMenu && <div className="popover user-popover"><div className="menu-identity"><strong>陈宁</strong><small>chen.ning</small></div><button onClick={() => { setProfile(true); setUserMenu(false); }}><UsersRound size={15} />个人中心</button><button onClick={() => { setProfile(true); setUserMenu(false); }}><KeyRound size={15} />账号与 API Key</button><hr /><button onClick={() => { setUserMenu(false); setDialog({ title: "退出登录", text: "正式版本将退出当前登录并清理会话凭据。当前为独立设计预览，没有登录真实账号。" }); }}><LogOut size={15} />退出登录</button></div>}</div></header>
      <main className={`page-content ${page === "logs" ? "log-content" : ""}`}>
        {page === "agents" && <><PageTitle title="智能体" help="选择功能、配置聊天界面，验证后发布。一个智能体拥有独立的聊天与嵌入入口。"><button className="button primary" onClick={() => { setAgentName(""); setDialog({ title: "新建智能体", text: "选择业务功能，组合为统一的问答助手。", agent: "new" }); }}><Plus size={16} />新建智能体</button></PageTitle>
          <div className="list-toolbar"><div className="filter-tabs">{["全部", "已发布", "草稿"].map((s, i) => <button key={s} className={filter === s ? "active" : ""} onClick={() => setFilter(s)}>{s}<span>{[3, 2, 1][i]}</span></button>)}</div><div className="toolbar-right"><label className="search-box"><Search size={16} /><input aria-label="搜索智能体" placeholder="搜索智能体…" value={query} onChange={e => setQuery(e.target.value)} /></label><div className="view-toggle"><button className={grid ? "active" : ""} aria-label="卡片视图" aria-pressed={grid} onClick={() => setGrid(true)}><LayoutGrid size={16} /></button><button className={!grid ? "active" : ""} aria-label="列表视图" aria-pressed={!grid} onClick={() => setGrid(false)}><List size={17} /></button></div></div></div>
          {!filtered.length && <div className="empty-state"><Search size={30} /><h3>没有找到智能体</h3><p>试试其他名称，或清除筛选。</p><button className="button" onClick={() => { setQuery(""); setFilter("全部"); }}>清除筛选</button></div>}
          <div className={grid ? "agent-grid" : "agent-list"}>{filtered.map(a => <article className="agent-card" key={a.name}><div className="agent-card-heading"><span className={`agent-icon ${a.color}`}><Bot size={24} /></span><Status value={a.state} /><button className="icon-button card-more" aria-label={`${a.name}更多操作`} onClick={() => setDialog({ title: a.name, text: "可在智能体详情中配置聊天界面、访问渠道、功能组合和发布版本。", agent: a.name })}><MoreHorizontal size={19} /></button></div><button className="agent-title" onClick={() => setDialog({ title: a.name, text: a.description, agent: a.name })}>{a.name}<ArrowUpRight size={16} /></button><p className="agent-description">{a.description}</p><div className="agent-tags">{a.tags.map(t => <span key={t}>{t}</span>)}</div><div className="agent-stats"><span><Blocks size={14} />{a.functions} 项功能</span><span><Clock3 size={14} />{a.time}</span></div><footer><button className="text-button" onClick={() => setDialog({ title: a.name, text: "功能组合、三层结构、聊天配置、测试与发布统一放在智能体详情。", agent: a.name })}>配置智能体<ArrowRight size={14} /></button><button className="text-button" onClick={() => setDialog({ title: `${a.name} · 聊天预览`, text: "您好，我可以帮助查询客户、账户、贷款和工单。试试问我：帮我看看张明名下账户与余额。" })}><MessageSquare size={14} />{a.state === "已发布" ? "打开应用" : "预览"}</button></footer></article>)}</div>
          <div className="collection-footer"><span>共 {filtered.length} 个智能体</span><span>按最近更新排序<ArrowDownUp size={13} /></span></div>
        </>}
        {page === "logs" && <Logs key={workspace} notice={setToast} />}
        {page === "functions" && (editing ? <PreviewFunctionEditor key={`${workspace}-${existing}`} existing={existing} initial={drafts[`${workspace}-${existing}`]} onSave={draft => setDrafts(current => ({ ...current, [`${workspace}-${existing}`]: draft }))} onBack={() => guarded(() => setEditing(false))} onDirty={setDirty} notice={setToast} /> : <><PageTitle title="功能库" help="维护直接调用与组合功能，发布后可供智能体选择。"><button className="button primary" onClick={() => edit(false)}><Plus size={16} />新建功能</button></PageTitle><div className="library-table"><table><thead><tr><th>功能名称</th><th>执行方式</th><th>业务实体</th><th>状态</th><th>操作</th></tr></thead><tbody>{capabilityNames.map((name, i) => <tr key={name}><td><Blocks size={16} /><strong>{name}</strong></td><td>直接调用</td><td>{i === 0 ? "客户" : i === 5 ? "贷款" : i === 6 ? "工单" : "账户"}</td><td><Status value="已发布" /></td><td><button className="text-button" onClick={() => name === "查询账户余额" ? edit(true) : setDialog({ title: name, text: "本次以账户余额功能演示完整编辑流程。此功能的现有配置保持不变，审核后将使用同一编辑模板。" })}>{name === "查询账户余额" ? "编辑" : "查看"}</button></td></tr>)}</tbody></table></div><div className="collection-footer"><span>7 项展示样例</span><button className="text-button" onClick={() => edit(true)}>查看账户余额配置<ArrowRight size={14} /></button></div></>)}
      </main>
    </div>
    {toast && <div className="toast" role="status"><Check size={17} /><span>{toast}</span><button aria-label="关闭提示" className="icon-button" onClick={() => setToast("")}><X size={14} /></button></div>}
    {profile && <Profile onClose={() => setProfile(false)} notice={setToast} density={density} setDensity={setDensity} />}
    {pending && <Modal title="保留当前修改？" onClose={() => setPending(null)}><div className="modal-body"><p>当前功能有未保存的修改，离开后会丢失。</p></div><footer className="modal-footer"><button className="button" onClick={() => setPending(null)}>继续编辑</button><button className="button primary" onClick={() => { const action = pending; setPending(null); setDirty(false); action(); }}>放弃修改并离开</button></footer></Modal>}
    {dialog && <Modal title={dialog.title} onClose={() => setDialog(null)}><div className="modal-body"><p>{dialog.text}</p>{dialog.agent === "new" ? <label className="field">智能体名称 <em>*</em><input value={agentName} onChange={e => setAgentName(e.target.value)} placeholder="例如：客户经理助手" /></label> : dialog.agent && <div className="agent-detail-options">{["功能组合", "三层结构", "聊天界面", "测试与发布"].map(t => <button key={t} onClick={() => setToast(`${t}将在正式集成中沿用既有能力`)}>{t}<ChevronRight size={16} /></button>)}</div>}</div><footer className="modal-footer"><button className="button" onClick={() => setDialog(null)}>关闭</button>{dialog.agent === "new" && <button className="button primary" disabled={!agentName.trim()} onClick={() => { if (agents.some(a => a.name === agentName.trim())) { setToast("此空间已存在同名智能体"); return; } setToast("新建交互预览完成，未创建正式智能体"); setDialog(null); }}>创建预览</button>}</footer></Modal>}
  </div>;
}
