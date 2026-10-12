import { CapabilityPicker } from "./CapabilityPicker";
import { Modal, SearchSelect, Anchored, ActionMenu } from "./ModernUI";
import { Search, Plus, Play, Activity, History, Share2, FileText } from "lucide-react";
import { SuggestedQuestionsEditor, WebsiteOriginsEditor, VersionHistory, ShareApplication } from "./AgentConfigurationPanels";
import "./AgentConfigurationExperience.css";
type EditorDraft={current:Agent|null;tab:"interface"|"planning"|"access"|"publish"|"test";ui:RuntimeUI;name:string;selected:string[];planning:boolean;strategy:"legacy"|"three-layer-v1";selectedSkills:string[];modelProfileId:string;origins:string;channels:Channels;access:AccessPolicy;accessUsers:string};
const draftCache = new Map<string, EditorDraft>();
export function clearAgentDrafts(){draftCache.clear();}
import { AgentCompilationPreview } from "./AgentCompilationPreview";
import { EvaluationPage } from "./EvaluationPage";
import { EvaluationArchiveViewer } from "./EvaluationArchiveViewer";
import { useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, Palette, ShieldCheck, Upload, Workflow } from "lucide-react";
import { ApiClient, errorText } from "../../shared/api";
import { Badge, Notice } from "../../shared/ui";
import { ApplicationPreview } from "../../shared/ApplicationView";
import { canOpenPublishedChat, runtimeUI } from "../../shared/application";
import type { AccessPolicy, Agent, Capability, Channels, PublicApp, Release, RuntimeUI } from "../../shared/types";

import type { ModelProfile } from "./ModelSettingsPage";
import type { MemberDisplay, Paged } from "../../shared/workspaces";

type BusinessSkill = { id: string; key: string; definition: { name: string; description: string }; latest_version: number; disabled: boolean };

const lines = (value: string) => [...new Set(value.split("\n").map(s => s.trim()).filter(Boolean))];
export function AgentEditor({ api, workspace, item, functions, admin = false, publishable = true, onClose, onSaved, onDirtyChange, onCreateFunction, onCreateModel, onPreview, onLogs, returnSelection, initialTab, onTabChange, preserveMounted = false }: {
  api: ApiClient; workspace: string; item: Agent | null; functions: Capability[];
  publishable?: boolean; preserveMounted?:boolean; initialTab?:string;onTabChange?:(tab:string)=>void;
  admin?: boolean;
  onClose: () => void; onSaved: (saved?:Agent) => Promise<void>;
  onDirtyChange?: (v:boolean)=>void; onCreateFunction?:()=>void; onCreateModel?:()=>void; onPreview?:(a:Agent)=>void; onLogs?:(a:Agent)=>void; returnSelection?:{newFunctionId?:string;newModelId?:string}|null;
}) {
  const cacheKey=`${workspace}:${item?.id??"new"}`;
  const [remembered]=useState(()=>draftCache.get(cacheKey));
  const [current, setCurrent] = useState<Agent|null>(remembered?.current??item);
  const [tab, setTab] = useState<"interface" | "planning" | "access" | "publish" | "test">((remembered?.tab && ["interface","planning","access"].includes(remembered.tab) ? remembered.tab : ["interface","planning","access"].includes(initialTab??"") ? initialTab as "planning" : "planning"));
  const [ui, setUI] = useState<RuntimeUI>(remembered?.ui??runtimeUI(item));
  const [name, setName] = useState(remembered?.name??item?.name ?? "");
  const [selected, setSelected] = useState<string[]>(remembered?.selected??item?.capability_ids ?? []);
  const [planning] = useState(remembered?.planning??item?.planning_enabled ?? true);
  const [strategy] = useState<"legacy" | "three-layer-v1">(remembered?.strategy??item?.routing_strategy ?? (item ? "legacy" : "three-layer-v1"));
  const [selectedSkills, setSelectedSkills] = useState<string[]>(remembered?.selectedSkills??item?.business_skill_ids ?? []);
  const [modelProfileId, setModelProfileId] = useState(remembered?.modelProfileId??item?.model_profile_id ?? "");
  const [skills, setSkills] = useState<BusinessSkill[]>([]);
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [origins, setOrigins] = useState(remembered?.origins??item?.allowed_origins?.join("\n") ?? "");
  const [channels, setChannels] = useState<Channels>(remembered?.channels??item?.channels ?? { chat: true, embed: false });
  const [access, setAccess] = useState<AccessPolicy>(remembered?.access??item?.access ?? { access_mode: "workspace", allowed_users: [] });
  const [accessUsers, setAccessUsers] = useState(remembered?.accessUsers??item?.access?.allowed_users.join("\n") ?? "");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(!!remembered);
  useEffect(()=>{if(initialTab&&["interface","planning","access"].includes(initialTab))setTab(initialTab as "planning");else if(initialTab==="publish"||initialTab==="test"){setTab("planning");setDialog(initialTab==="publish"?"share":"reports");}},[initialTab]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [assetUrl, setAssetUrl] = useState<string>();
  const [published, setPublished] = useState<PublicApp | null>(null);
  const [releases, setReleases] = useState<Release[]>([]);
  const [revisionTick, setRevisionTick] = useState(0);
  const [previewEmbed, setPreviewEmbed] = useState(false);
  const [dialog,setDialog]=useState<"versions"|"share"|"reports"|null>(initialTab==="publish"?"share":initialTab==="test"?"reports":null);
  const [confirmAction,setConfirmAction]=useState<{title:string;message:string;detail?:ReactNode;run:()=>Promise<boolean>}|null>(null);
  const accessDirty=access.access_mode!==(current?.access?.access_mode??"workspace")||JSON.stringify(lines(accessUsers).sort())!==JSON.stringify([...(current?.access?.allowed_users??[])].sort());
  useEffect(()=>{onDirtyChange?.(dirty||accessDirty);},[dirty,accessDirty,onDirtyChange]);
  useEffect(()=>{draftCache.delete(cacheKey);},[cacheKey]);
  useEffect(()=>{if(returnSelection?.newFunctionId){setSelected(ids=>[...new Set([...ids,returnSelection.newFunctionId!])]);setDirty(true);}if(returnSelection?.newModelId){setModelProfileId(returnSelection.newModelId);setDirty(true);}},[returnSelection?.newFunctionId,returnSelection?.newModelId]);
  function stash(){if(preserveMounted)return;draftCache.set(cacheKey,{current,tab,ui,name,selected,planning,strategy,selectedSkills,modelProfileId,origins,channels,access,accessUsers});}
  const prefix = `/api/v2/management/workspaces/${encodeURIComponent(workspace)}`;
  const path = `${prefix}/agents/${current?.id}`;
  useEffect(() => {
    let active = true;
    Promise.all([
      api.get<BusinessSkill[]>(`${prefix}/business-skills`),
      api.get<ModelProfile[]>(`${prefix}/model-profiles`),
    ]).then(([skillItems, profileItems]) => {
      if (active) { setSkills(skillItems); setModels(profileItems); }
    }).catch(e => active && setError(errorText(e)));
    return () => { active = false; };
  }, [api, prefix, returnSelection?.newModelId]);
  useEffect(() => {
    if (ui.icon.kind !== "asset" || !ui.icon.value) { setAssetUrl(undefined); return; }
    let active = true, url = "";
    api.blob(`${prefix}/assets/${encodeURIComponent(ui.icon.value)}`).then(blob => {
      if (active) { url = URL.createObjectURL(blob); setAssetUrl(url); }
    }).catch(e => active && setError(errorText(e)));
    return () => { active = false; if (url) URL.revokeObjectURL(url); };
  }, [api, prefix, ui.icon.kind, ui.icon.value]);
  useEffect(() => {
    let active = true;
    setPublished(null);
    if (current?.id && publishable) api.get<Release[]>(`${path}/releases`).then(value => active && setReleases(value)).catch(e => active && setError(errorText(e)));
    if (current?.public_id && current.current_release_id) {
      api.get<PublicApp>(`/api/v2/runtime/apps/${current.public_id}`).then(value => active && setPublished(value)).catch(() => { /* Both channels disabled may hide the public configuration. */ });
    }
    return () => { active = false; };
  }, [api, path, current?.current_release_id, current?.public_id, revisionTick, publishable]);
  function edit(patch: Partial<RuntimeUI>) { setUI(value => ({ ...value, ...patch })); setDirty(true); }
  async function refreshSaved(saved?: Agent) {
    try { await onSaved(saved); }
    catch { setNotice(value => `${value} 操作已完成，列表未能刷新；请稍后刷新页面，不必重复提交。`); }
  }
  async function execute(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); return true; } catch (e) { setError(errorText(e)); return false; } finally { setBusy(false); }
  }
  async function save() {
    return execute(async () => {
      const savedUI = { ...ui, suggested_questions: ui.suggested_questions.map(q => q.trim()).filter(Boolean) };
      const body = { name, revision: current?.revision, capability_ids: selected, allowed_origins: lines(origins), suggested_questions: savedUI.suggested_questions, runtime_ui: savedUI, channels, planning_enabled: strategy === "three-layer-v1" || planning, routing_strategy: strategy, business_skill_ids: selectedSkills, model_profile_id: modelProfileId || null };
      const saved = current ? await api.patch<Agent>(`${path}/draft`, body) : await api.post<Agent>(`${prefix}/agents`, body);
      setCurrent(value => ({ ...(value ?? { name, workspace_id: workspace, suggested_questions: ui.suggested_questions, fixture_mode: false }), ...saved }));
      setUI(savedUI); setDirty(false); setNotice("草稿已保存；发布后生效。"); await refreshSaved(saved);
    });
  }
  async function publish() {
    return execute(async () => {
      const release = await api.post<Release>(`${path}/publish`, { revision: current?.revision });
      if (release.release_id) setCurrent(value => value ? { ...value, current_release_id: release.release_id, published_channels: { ...channels }, live_channels: value.live_channels ?? { ...channels } } : value);
      setRevisionTick(v => v + 1); setNotice("已发布。既有会话保持原发布版本；即时停用设置继续有效。");
      try { const all = await api.get<Agent[]>(`${prefix}/agents`); const updated = all.find(a => a.id === current?.id); if (updated) setCurrent(updated); }
      catch { setNotice(value => `${value} 发布已完成，但状态未能刷新；请稍后刷新，不必重复发布。`); }
      await refreshSaved();
    });
  }
  async function changeLive(next: Channels) {
    return execute(async () => {
      const updated = await api.put<Agent>(`${path}/channels`, next);
      setCurrent(value => value ? { ...value, ...updated, live_channels: next } : value);
      setRevisionTick(v => v + 1); setNotice("渠道即时开关已更新。"); await refreshSaved();
    });
  }
  async function copy(value: string, label: string) {
    return execute(async () => { await navigator.clipboard.writeText(value); setNotice(`${label}已复制。`); });
  }
  async function upload(file: File) {
    await execute(async () => {
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("仅支持 PNG、JPEG 或 WebP 图片。");
      const body = new FormData(); body.append("file", file);
      const asset = await api.request<{ id: string }>(`${prefix}/assets`, { method: "POST", body });
      edit({ icon: { kind: "asset", value: asset.id } }); setNotice("图标已上传，请保存草稿并发布后生效。");
    });
  }
  const chatAvailable = canOpenPublishedChat(current);
  const live = current?.live_channels ?? published?.channels ?? { chat: false, embed: false };
  const releasedChannels = current?.published_channels ?? published?.channels;
  function goTab(next: "planning" | "interface" | "access") { setTab(next); onTabChange?.(next); }
  function requestPublish() {
    setError("");
    setConfirmAction({ title: "发布智能体", message: "新会话使用本次配置，已有会话保持原发布版本。即时停用和撤权继续有效。", detail: <details><summary>本次发布 {selected.length} 项业务能力</summary><ul className="release-functions">{selected.map(id => <li key={id}>{functions.find(f => f.id === id)?.name ?? "不可用功能"}</li>)}</ul></details>, run: publish });
  }
  function draftChannel(channel: keyof Channels, enabled: boolean) {
    const change = () => { setChannels(old => ({ ...old, [channel]: enabled })); setDirty(true); };
    if (!enabled && live[channel] && releasedChannels?.[channel] && publishable) {
      setError("");
      setConfirmAction({ title: `关闭${channel === "chat" ? "公开聊天" : "iframe 嵌入"}`, message: "确认后立即停止此渠道访问，同时修改草稿。已发送业务保留真实结果。", run: async () => { const ok = await changeLive({ ...live, [channel]: false }); if (ok) change(); return ok; } });
    } else change();
  }
  const embedAvailable = !!current?.current_release_id && !!releasedChannels?.embed && live.embed;
  return <div className="application-editor agent-configuration" aria-label="智能体配置">
    <header className="application-editor-header">
      <button className="icon-button" aria-label="关闭智能体编辑" disabled={busy} onClick={onClose}><ArrowLeft size={20} /></button>
      <div className="agent-title"><h1>{current ? current.name : "创建智能体"}</h1><Badge tone={dirty ? "warning" : "neutral"}>{dirty ? "有未保存修改" : current?.current_release_id ? "已发布" : "草稿"}</Badge></div>
      <div className="agent-header-actions">
        <button className="button" disabled={busy || !current || !publishable} onClick={() => { setError(""); setDialog("versions"); }}><History size={15} />版本与变更</button>
        <button className="button" disabled={busy || !current?.current_release_id} onClick={() => { setError(""); setDialog("share"); }}><Share2 size={15} />分享与嵌入</button>
        <ActionMenu trigger={<button className="button" disabled={busy || !current}><FileText size={15} />更多</button>} items={[{label:"测试报告",onSelect:()=>setDialog("reports")}]} />
        <button className="button" disabled={busy || !chatAvailable} onClick={() => current && onPreview?.(current)}><Play size={14} />试聊已发布版本</button>
        <button className="button" disabled={busy || !name.trim() || !selected.length} onClick={() => void save()}>保存草稿</button>
        <button className="button primary" disabled={busy || dirty || !current || !publishable} onClick={requestPublish}>发布</button>
      </div>
    </header>
    {current?.current_release_id && !chatAvailable && <p className="draft-trial-note">聊天已关闭；可在<button className="text-button" onClick={() => goTab("access")}>访问控制</button>中调整。</p>}
    {dirty && current?.current_release_id && <p className="draft-trial-note">当前修改尚未发布，试聊使用已发布版本。</p>}
    <nav className="application-tabs" aria-label="智能体配置页">
      {([["planning", "业务能力", Workflow], ["interface", "对话界面", Palette], ["access", "访问控制", ShieldCheck]] as const).map(([key, label, Icon]) => <button key={key} aria-current={tab === key ? "page" : undefined} className={tab === key ? "active" : ""} onClick={() => goTab(key)}><Icon size={17} />{label}</button>)}
    </nav>
    <div className="application-editor-content"><fieldset className="application-editor-fields" disabled={busy}>
      {error && <Notice kind="error">{error}</Notice>}{notice && <Notice kind="success">{notice}</Notice>}
      {tab === "planning" && <div className="application-capability-workspace">
        <section className="application-settings agent-business-settings">
          <div className="agent-basic-fields"><label>智能体名称<input required value={name} onChange={e => { setName(e.target.value); edit({ title: e.target.value }); }} placeholder="给业务助手起一个名称" /></label>
          <label>模型配置<SearchSelect label="模型配置" value={modelProfileId} onChange={v => { setModelProfileId(v); setDirty(true); }} options={models.filter(m => !m.disabled && m.latest_version).map(m => ({ value: m.id, label: m.name, detail: `${m.latest?.config.model_version} · v${m.latest_version}` }))} placeholder="选择模型" footer={admin && onCreateModel && <button className="text-button" onClick={() => { stash(); onCreateModel(); }}><Plus size={14} />创建模型配置</button>} /></label></div>
          {!modelProfileId && <Notice>选择模型后可进行自然语言问答和自主规划。</Notice>}
          <CapabilityPicker api={api} disabled={busy} functions={functions} selected={selected} onChange={v => { setSelected(v); setDirty(true); }} onCreate={onCreateFunction ? () => { stash(); onCreateFunction(); } : undefined} />
          {!!selectedSkills.length && <details className="advanced"><summary>已有业务规则</summary><p className="muted small">保留旧版本配置；新规则通过组合功能维护。</p>{selectedSkills.map(id => <label className="checkbox" key={id}><input type="checkbox" checked onChange={() => { setSelectedSkills(selectedSkills.filter(v => v !== id)); setDirty(true); }} />{skills.find(skill => skill.id === id)?.definition.name ?? "历史业务规则"}</label>)}</details>}
        </section>
        <div><AgentCompilationPreview api={api} path={path} revision={current?.revision} dirty={dirty} functions={functions} />
          <details className="application-settings runtime-limits"><summary>运行限制 <span className="muted small">系统保护，不支持单独配置</span></summary><div className="limit-facts"><span><strong>8</strong> 个步骤</span><span><strong>5</strong> 个批量对象</span><span><strong>20</strong> 次调用</span><span><strong>3</strong> 个并发调用</span></div><p className="muted small">写操作最多一个，位于最后一步，明确确认后才执行。修改对象或参数需要重新确认。</p></details>
          {current && admin && onLogs && <button className="text-button" onClick={() => onLogs(current)}><Activity size={14} />查看运行日志</button>}
        </div>
      </div>}
      {tab === "interface" && <div className="application-design-grid">
        <section className="application-settings"><h2>对话界面</h2>
          <label>智能体名称<input required value={name} onChange={e => { setName(e.target.value); edit({ title: e.target.value }); }} /></label>
          <label>简介<textarea rows={2} value={ui.description} onChange={e => edit({ description: e.target.value })} /></label>
          <div className="form-row"><label>图标字符<input maxLength={16} value={ui.icon.kind === "emoji" ? ui.icon.value : ""} onChange={e => edit({ icon: { kind: "emoji", value: e.target.value } })} /></label><label>主题色<input type="color" value={ui.primary_color} onChange={e => edit({ primary_color: e.target.value })} /></label></div>
          <label className="upload-control"><Upload size={16} />上传本地图标<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} /></label>
          <label>欢迎语<textarea rows={2} value={ui.welcome_message} onChange={e => edit({ welcome_message: e.target.value })} /></label>
          <SuggestedQuestionsEditor value={ui.suggested_questions} onChange={value => edit({ suggested_questions: value })} />
          <label>输入框提示<input value={ui.input_placeholder} onChange={e => edit({ input_placeholder: e.target.value })} /></label>
          <label className="checkbox"><input type="checkbox" checked={ui.show_history} onChange={e => edit({ show_history: e.target.checked })} />显示历史会话侧栏</label>
        </section><div className="application-preview-column"><div className="preview-heading"><strong>草稿预览</strong><div className="preview-modes"><button aria-pressed={!previewEmbed} onClick={() => setPreviewEmbed(false)}>独立页面</button><button aria-pressed={previewEmbed} onClick={() => setPreviewEmbed(true)}>嵌入页面</button></div><Badge>未发布</Badge></div><ApplicationPreview ui={ui} assetUrl={assetUrl} embedded={previewEmbed} /></div>
      </div>}
      {tab === "access" && <div className="agent-access-layout">
        <div className="access-channel-grid">{(["chat", "embed"] as const).map(channel => {
          const effective = !!releasedChannels?.[channel] && live[channel];
          return <section className={`application-settings access-channel ${channels[channel] ? "selected" : ""}`} key={channel}>
            <div className="setting-heading"><label className="channel-checkbox"><input type="checkbox" checked={channels[channel]} onChange={e => draftChannel(channel, e.target.checked)} /><strong>{channel === "chat" ? "公开 Chat" : "iframe 嵌入"}</strong></label><Badge tone={effective ? "success" : "neutral"}>{effective ? "当前开放" : current?.current_release_id && releasedChannels?.[channel] && !live[channel] ? "即时停用" : "尚未开放"}</Badge></div>
            <p className="muted">{channel === "chat" ? "免登录访问独立聊天页面，各访客历史独立。" : "嵌入业务网站，由宿主后端使用用户 Key 完成身份换票。"}</p>
            <p className="muted small">{effective && !channels[channel] ? "草稿已关闭；当前发布入口仍可用。" : channels[channel] && !releasedChannels?.[channel] ? "已在草稿中启用，保存并发布后可开放；若即时开关仍停用，需明确恢复。" : releasedChannels?.[channel] && !live[channel] ? "发布新版本不会解除即时停用。" : "渠道设置随草稿发布。"}</p>
            {current?.current_release_id && releasedChannels?.[channel] && <button type="button" className={`text-button ${effective ? "danger" : ""}`} disabled={!publishable || busy} onClick={() => { setError(""); setConfirmAction({ title: `${effective ? "立即停用" : "恢复"}${channel === "chat" ? "公开聊天" : "嵌入渠道"}`, message: effective ? "此渠道现有访问立即失效，已发送业务保留真实结果。草稿配置保持不变。" : "重新开放已发布渠道；仍按当前用户权限及网站授权检查访问。", run: () => changeLive({ ...live, [channel]: !effective }) }); }}>{effective ? "立即停用" : "恢复当前发布渠道"}</button>}
          </section>;
        })}</div>
        <section className="application-settings access-permissions"><div className="setting-heading"><h2>授权用户</h2><Badge>立即生效</Badge></div><p className="muted small">用于 iframe 和管理试聊。公开 Chat 为匿名入口，不受此名单限制。</p>
          <div className="access-mode-options">{(["workspace", "allowlist"] as const).map(mode => <label key={mode} className={`access-mode ${access.access_mode === mode ? "selected" : ""}`}><input type="radio" name="access-mode" disabled={!admin} checked={access.access_mode === mode} onChange={() => setAccess({ ...access, access_mode: mode })} /><span><strong>{mode === "workspace" ? "空间成员" : "指定成员"}</strong><small>{mode === "workspace" ? "当前空间内有权限的有效成员" : "从当前空间选择可访问的成员"}</small></span></label>)}</div>
          {access.access_mode === "allowlist" && <AllowedMembers api={api} workspace={workspace} value={lines(accessUsers)} onChange={ids => setAccessUsers(ids.join("\n"))} enabled={admin} />}
          <button className="button primary" disabled={!current || busy || !admin || !accessDirty} onClick={() => { setError(""); setConfirmAction({ title: "更新访问权限", message: `立即更新 ${name} 的访问范围。移除的用户无法继续读取和执行现有会话。`, run: () => execute(async () => { const policy = { ...access, allowed_users: access.access_mode === "allowlist" ? lines(accessUsers) : [] }; await api.put(`${path}/access`, policy); setAccess(policy); setAccessUsers(policy.allowed_users.join("\n")); setCurrent(v => v ? { ...v, access: policy } : v); setNotice("访问权限已立即生效。"); await refreshSaved(); }) }); }}>保存访问权限</button>
        </section>
        {(channels.embed || releasedChannels?.embed || !!current?.published_origins?.length) && <section className="application-settings">
          <WebsiteOriginsEditor value={lines(origins)} onChange={values => { setOrigins(values.join("\n")); setDirty(true); }} />
          {!!current?.published_origins?.length && <details className="published-websites"><summary>已发布网站与即时撤销</summary>{current.published_origins.map(origin => <div className="origin-row" key={origin}><span>{origin}</span>{current.revoked_origins?.includes(origin) ? <Badge>已撤销</Badge> : <button className="text-button danger" disabled={busy || !publishable} onClick={() => { setError(""); setConfirmAction({ title: "撤销嵌入网站", message: `立即禁止 ${origin} 嵌入 ${name}。该网站的既有访问随即失效，草稿保持不变。`, run: () => execute(async () => { await api.post(`${path}/revoke-origin`, { origin }); setCurrent(v => v ? { ...v, revoked_origins: [...new Set([...(v.revoked_origins ?? []), origin])] } : v); setNotice("已立即撤销该网站，草稿保持不变。"); await refreshSaved(); }) }); }}>立即撤销</button>}</div>)}</details>}
        </section>}
      </div>}
    </fieldset></div>
    {dialog === "versions" && current && <VersionHistory api={api} workspace={workspace} agent={current} releases={releases} functions={functions} busy={busy} error={error} dirty={dirty||accessDirty} publishable={publishable} admin={admin} onClose={() => setDialog(null)} onRollback={r => execute(async () => { const updated = await api.post<Agent>(`${path}/rollback`, { release_id: r.release_id }); setCurrent(updated); setRevisionTick(v => v + 1); setNotice(`新会话已切换到 v${r.version}；草稿、旧会话及即时停用不变。`); await refreshSaved(); })} />}
    {dialog === "share" && current && <ShareApplication agent={current} chatAvailable={chatAvailable} embedAvailable={embedAvailable} busy={busy} error={error} onClose={() => setDialog(null)} onCopy={copy} onAccess={() => { setDialog(null); goTab("access"); }} />}
    {dialog === "reports" && <Modal title="测试报告" wide busy={busy} onClose={() => setDialog(null)}><div className="modal-body"><EvaluationArchiveViewer />{current && current.routing_strategy !== "three-layer-v1" && <details><summary>历史路由诊断</summary><EvaluationPage api={api} workspace={workspace} agents={[current]} functions={functions.filter(f => selected.includes(f.id))} writable={publishable} fixtureMode={current.fixture_mode} /></details>}</div></Modal>}
    {confirmAction && <Modal title={confirmAction.title} busy={busy} onClose={() => setConfirmAction(null)}><div className="modal-body"><p>{confirmAction.message}</p>{confirmAction.detail}{error && <Notice kind="error">{error}</Notice>}</div><footer className="modal-footer"><button className="button" disabled={busy} onClick={() => setConfirmAction(null)}>取消</button><button className="button primary" disabled={busy} onClick={() => void confirmAction.run().then(ok => { if (ok) setConfirmAction(null); })}>{busy ? "处理中…" : "确认"}</button></footer></Modal>}
  </div>;
}
function AllowedMembers({api,workspace,value,onChange,enabled}:{api:ApiClient;workspace:string;value:string[];onChange:(ids:string[])=>void;enabled:boolean}){
 const [query,setQuery]=useState(''),[members,setMembers]=useState<MemberDisplay[]>([]),[known,setKnown]=useState<Record<string,MemberDisplay>>({}),[error,setError]=useState('');
 useEffect(()=>{let live=true;if(!enabled)return;const timer=setTimeout(()=>{void api.get<Paged<MemberDisplay>>(`/api/v2/management/workspaces/${encodeURIComponent(workspace)}/members/page?${new URLSearchParams({query,limit:'50',status:'active'})}`).then(page=>{if(live){setMembers(page.items??[]);setKnown(old=>({...old,...Object.fromEntries((page.items??[]).map(m=>[m.user_id,m]))}));setError('');}}).catch(e=>live&&setError(errorText(e)));},150);return()=>{live=false;clearTimeout(timer);};},[api,workspace,query,enabled]);
 return <div className="allowed-members"><div className="table-toolbar"><span>已授权 {value.length} 人</span>{value.length>0&&<button type="button" className="text-button" disabled={!enabled} onClick={()=>onChange([])}>清空名单</button>}</div>{value.map(id=><div className="permission-row" key={id}><span>{known[id]?.display_name||known[id]?.username||'已授权成员（当前不可见）'}</span><button className="text-button" disabled={!enabled} onClick={()=>onChange(value.filter(v=>v!==id))}>移除</button></div>)}<Anchored trigger={<button className="button" disabled={!enabled}><Plus size={14}/>添加授权成员</button>}><label className="search-box"><Search size={14}/><input aria-label="搜索授权成员" placeholder="搜索姓名或账号" value={query} onChange={e=>setQuery(e.target.value)}/></label><div className="select-options">{members.filter(m=>!value.includes(m.user_id)).map(m=><button type="button" key={m.user_id} onClick={()=>onChange([...value,m.user_id])}>{m.display_name||m.username}<small>{m.username}</small></button>)}</div>{error&&<Notice kind="error">{error}</Notice>}</Anchored>{!value.length&&<p className="muted">空名单不授予任何用户访问。</p>}</div>;
}
