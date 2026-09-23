import { useEffect, useState } from "react";
import { ArrowLeft, Check, Copy, ExternalLink, Globe, Palette, ShieldCheck, Upload } from "lucide-react";
import { ApiClient, errorText } from "../../shared/api";
import { Badge, Notice } from "../../shared/ui";
import { ApplicationPreview } from "../../shared/ApplicationView";
import { appPath, embedSnippet, handshakeSnippet, runtimeUI } from "../../shared/application";
import type { AccessPolicy, Agent, Capability, Channels, PublicApp, Release, RuntimeUI } from "../../shared/types";
import "./AgentEditor.css";
import { useGovernanceCatalog } from "./useGovernanceCatalog";
import { connectorLabels } from "../../shared/governance";

const lines = (value: string) => [...new Set(value.split("\n").map(s => s.trim()).filter(Boolean))];
export function AgentEditor({ api, workspace, item, functions, publishable = true, onClose, onSaved }: {
  api: ApiClient; workspace: string; item: Agent | null; functions: Capability[];
  publishable?: boolean;
  onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [current, setCurrent] = useState(item);
  const [tab, setTab] = useState<"interface" | "access" | "publish">("interface");
  const [ui, setUI] = useState<RuntimeUI>(runtimeUI(item));
  const [name, setName] = useState(item?.name ?? "");
  const [selected, setSelected] = useState(item?.capability_ids ?? []);
  const [origins, setOrigins] = useState(item?.allowed_origins?.join("\n") ?? "");
  const [channels, setChannels] = useState<Channels>(item?.channels ?? { chat: true, embed: false });
  const [access, setAccess] = useState<AccessPolicy>(item?.access ?? { access_mode: "workspace", allowed_users: [] });
  const [accessUsers, setAccessUsers] = useState(item?.access?.allowed_users.join("\n") ?? "");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(!item);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [assetUrl, setAssetUrl] = useState<string>();
  const [published, setPublished] = useState<PublicApp | null>(null);
  const [releases, setReleases] = useState<Release[]>([]);
  const [revisionTick, setRevisionTick] = useState(0);
  const [previewEmbed, setPreviewEmbed] = useState(false);
  const [integrationRefs, setIntegrationRefs] = useState<string[] | undefined>(item?.integration_refs);
  const { catalog, error: catalogError, loading: catalogLoading, reload: reloadCatalog } = useGovernanceCatalog(api, workspace);
  const prefix = `/api/v2/management/workspaces/${encodeURIComponent(workspace)}`;
  const path = `${prefix}/agents/${current?.id}`;
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
    if (current?.id) api.get<Release[]>(`${path}/releases`).then(value => active && setReleases(value)).catch(e => active && setError(errorText(e)));
    if (current?.public_id && current.current_release_id) {
      api.get<PublicApp>(`/api/v2/runtime/apps/${current.public_id}`).then(value => active && setPublished(value)).catch(() => { /* Both channels disabled may hide the public configuration. */ });
    }
    return () => { active = false; };
  }, [api, path, current?.current_release_id, current?.public_id, revisionTick]);
  function edit(patch: Partial<RuntimeUI>) { setUI(value => ({ ...value, ...patch })); setDirty(true); }
  async function execute(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function save() {
    await execute(async () => {
      const body = { name, revision: current?.revision, capability_ids: selected, allowed_origins: lines(origins), suggested_questions: ui.suggested_questions, runtime_ui: ui, channels, ...(integrationRefs === undefined ? {} : { integration_refs: integrationRefs }) };
      const saved = current ? await api.patch<Agent>(`${path}/draft`, body) : await api.post<Agent>(`${prefix}/agents`, body);
      setCurrent(value => ({ ...(value ?? { name, workspace_id: workspace, suggested_questions: ui.suggested_questions, fixture_mode: false }), ...saved }));
      if (saved.integration_refs !== undefined) setIntegrationRefs(saved.integration_refs);
      setDirty(false); setNotice("草稿已保存；发布后新会话才使用此界面配置。"); await onSaved();
    });
  }
  async function publish() {
    await execute(async () => {
      await api.post(`${path}/publish`, { revision: current?.revision });
      const all = await api.get<Agent[]>(`${prefix}/agents`);
      const updated = all.find(a => a.id === current?.id);
      if (updated) setCurrent(updated);
      setRevisionTick(v => v + 1); setNotice("已发布。既有会话保持原发布版本；即时停用设置继续有效。"); await onSaved();
    });
  }
  async function changeLive(next: Channels) {
    await execute(async () => {
      const updated = await api.put<Agent>(`${path}/channels`, next);
      setCurrent(value => value ? { ...value, ...updated, live_channels: next } : value);
      setRevisionTick(v => v + 1); setNotice("渠道即时开关已更新，服务端持续检查运行会话权限。"); await onSaved();
    });
  }
  async function copy(value: string, label: string) {
    await execute(async () => { await navigator.clipboard.writeText(value); setNotice(`${label}已复制。`); });
  }
  async function upload(file: File) {
    await execute(async () => {
      if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("仅支持 PNG、JPEG 或 WebP 图片。");
      const body = new FormData(); body.append("file", file);
      const asset = await api.request<{ id: string }>(`${prefix}/assets`, { method: "POST", body });
      edit({ icon: { kind: "asset", value: asset.id } }); setNotice("图标已上传，请保存草稿并发布后生效。");
    });
  }
  const publicId = current?.public_id;
  const live = current?.live_channels ?? published?.channels ?? { chat: false, embed: false };
  const releasedChannels = current?.published_channels ?? published?.channels;
  const revocableIntegrations = [...new Set([...(current?.published_integration_refs ?? []), ...(current?.published_integration_refs === null ? catalog?.integrations.map(integration => integration.ref) ?? [] : []), ...(current?.revoked_integration_refs ?? [])])];
  const publicUrl = publicId ? new URL(appPath(publicId), location.origin).href : "";
  return <div className="application-editor" role="dialog" aria-modal="true" aria-label="智能体配置">
    <header className="application-editor-header">
      <button className="icon-button" aria-label="关闭智能体编辑" onClick={onClose}><ArrowLeft size={20} /></button>
      <div><h1>{current ? current.name : "创建场景智能体"}</h1><p>应用配置 <span>·</span> 草稿 r{current?.revision ?? "—"} <span>·</span> {current?.current_release_id ? "已有发布版本" : "尚未发布"}</p></div>
      <Badge tone={dirty ? "warning" : "neutral"}>{dirty ? "有未保存修改" : "草稿已保存"}</Badge>
      <button className="primary" disabled={busy || !name.trim() || !selected.length} onClick={() => void save()}>保存智能体草稿</button>
    </header>
    <nav className="application-tabs" aria-label="智能体配置页">
      {([["interface", "对话界面", Palette], ["access", "访问控制", ShieldCheck], ["publish", "发布与嵌入", Globe]] as const).map(([key, label, Icon]) => <button key={key} aria-current={tab === key ? "page" : undefined} className={tab === key ? "active" : ""} onClick={() => setTab(key)}><Icon size={17} />{label}</button>)}
    </nav>
    <div className="application-editor-content"><fieldset className="application-editor-fields" disabled={busy}>
      {error && <Notice kind="error">{error}</Notice>}{notice && <Notice kind="success">{notice}</Notice>}
      {tab === "interface" && <div className="application-design-grid">
        <section className="application-settings"><h2>对话界面</h2><p className="muted">应用的外观和开场信息，随版本一起发布。</p>
          <label>智能体名称<input required value={name} onChange={e => { setName(e.target.value); edit({ title: e.target.value }); }} /></label>
          <label>简介<textarea rows={2} value={ui.description} onChange={e => edit({ description: e.target.value })} /></label>
          <div className="form-row"><label>图标字符<input maxLength={16} value={ui.icon.kind === "emoji" ? ui.icon.value : ""} onChange={e => edit({ icon: { kind: "emoji", value: e.target.value } })} /></label><label>主题色<input type="color" value={ui.primary_color} onChange={e => edit({ primary_color: e.target.value })} /></label></div>
          <label className="upload-control"><Upload size={16} /> 上传本地图标<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} /></label>
          <p className="muted small">PNG、JPEG、WebP；保存在内网，不引用外部图片。</p>
          <label>欢迎语<textarea rows={2} value={ui.welcome_message} onChange={e => edit({ welcome_message: e.target.value })} /></label>
          <label>建议问题（一行一个）<textarea rows={4} value={ui.suggested_questions.join("\n")} onChange={e => edit({ suggested_questions: e.target.value.split("\n") })} /></label>
          <label>输入框提示<input value={ui.input_placeholder} onChange={e => edit({ input_placeholder: e.target.value })} /></label>
          <label className="checkbox"><input type="checkbox" checked={ui.show_history} onChange={e => edit({ show_history: e.target.checked })} />显示历史会话侧栏</label>
        </section><div className="application-preview-column"><div className="preview-heading"><strong>草稿预览</strong><div className="preview-modes"><button aria-pressed={!previewEmbed} onClick={() => setPreviewEmbed(false)}>独立页面</button><button aria-pressed={previewEmbed} onClick={() => setPreviewEmbed(true)}>嵌入页面</button></div><Badge>未发布</Badge></div><ApplicationPreview ui={ui} assetUrl={assetUrl} embedded={previewEmbed} /></div>
      </div>}
      {tab === "access" && <div className="application-two-column">
        <section className="application-settings integration-selector"><h2>可信身份集成</h2><p className="muted">从当前空间获准目录中选择，保存草稿并发布后生效。身份仍由可信服务校验；此处不能新增信任来源或填写密钥。</p>
          {catalogError && <Notice kind="error">{catalogError}</Notice>}{catalogLoading && <p className="muted">正在读取获准集成…</p>}
          {integrationRefs === undefined && <Notice>当前响应未提供集成草稿选择，保存其他设置会保留服务端规则。明确选择后才提交集成列表。</Notice>}
          {catalog && !catalog.integrations.length && <Notice>当前空间没有获准身份集成，需要服务端配置后再选择。</Notice>}
          <div className="integration-options">{catalog?.integrations.map(integration => <div key={integration.ref}><label className="checkbox"><input type="checkbox" aria-label={`身份集成 ${integration.ref}`} checked={integrationRefs?.includes(integration.ref) ?? false} disabled={busy || (!!current?.revoked_integration_refs?.includes(integration.ref) && !integrationRefs?.includes(integration.ref))} onChange={e => { setIntegrationRefs(e.target.checked ? [...(integrationRefs ?? []), integration.ref] : (integrationRefs ?? []).filter(ref => ref !== integration.ref)); setDirty(true); }} />{integration.label} · {integration.ref}</label><small>版本 {integration.version} · {integration.fixture_mode ? "开发身份，不能作为正式 SSO 验收" : "服务端获准集成"}{current?.revoked_integration_refs?.includes(integration.ref) && " · 已即时撤销，需选择新的获准引用"}</small></div>)}</div>
          {integrationRefs?.filter(ref => !catalog?.integrations.some(integration => integration.ref === ref)).map(ref => <div className="origin-row" key={ref}><span><code>{ref}</code> · {catalog ? "不在当前获准目录" : "目录尚未核对"}</span><button type="button" className="text-button" disabled={busy} onClick={() => { setIntegrationRefs((integrationRefs ?? []).filter(value => value !== ref)); setDirty(true); }}>从草稿移除 {ref}</button></div>)}
          {integrationRefs?.length === 0 && <Notice>草稿没有选择身份集成；启用渠道的发布将由服务端检查，空列表不会自动放开访问。</Notice>}
          {catalogError && <button type="button" className="secondary" onClick={reloadCatalog}>重新读取集成目录</button>}
          <h3>当前发布与即时撤销</h3>{current?.published_integration_refs === null ? <Notice>当前发布为旧版身份规则：仅允许当前服务端已批准的集成，并持续检查撤销。下次发布会固定所选集成版本。</Notice> : current?.published_integration_refs === undefined ? <p className="muted">未提供当前发布的集成清单。</p> : current.published_integration_refs.length === 0 ? <p className="muted">当前发布没有身份集成。</p> : null}
          {current?.published_integration_refs === null && <p className="muted small">下列获准目录项是旧版规则可核查的候选引用，不代表它们已被当前应用使用。</p>}{revocableIntegrations.map(ref => <div className="origin-row" key={ref}><code>{ref}</code>{current?.revoked_integration_refs?.includes(ref) ? <Badge>已即时撤销</Badge> : <button type="button" className="text-button danger" disabled={busy || !publishable} onClick={() => void execute(async () => { const updated = await api.post<Agent>(`${path}/revoke-integration`, { integration_ref: ref }); setCurrent(value => value ? { ...value, published_integration_refs: updated.published_integration_refs ?? value.published_integration_refs, revoked_integration_refs: updated.revoked_integration_refs ?? [...new Set([...(value.revoked_integration_refs ?? []), ref])] } : value); setRevisionTick(value => value + 1); setNotice(`身份集成 ${ref} 已立即撤销；未保存的草稿保持不变。`); await onSaved(); })}>立即撤销集成 {ref}</button>}</div>)}
          <p className="muted small">即时撤销影响已有会话，发布和回滚不会恢复。更换集成需要选择新的服务端获准引用再发布。</p>
        </section>
        <section className="application-settings"><h2>访问权限</h2><p className="muted">独立于界面草稿，保存后立即生效。所有访问还必须满足空间权限。</p>
          <label>访问范围<select value={access.access_mode} onChange={e => setAccess({ ...access, access_mode: e.target.value as AccessPolicy["access_mode"] })}><option value="workspace">当前空间的有效成员</option><option value="allowlist">指定用户</option></select></label>
          {access.access_mode === "allowlist" && <><label>允许的用户标识（一行一个）<textarea rows={7} value={accessUsers} onChange={e => setAccessUsers(e.target.value)} /></label><Notice>名单为空将拒绝所有用户，不会自动放开。</Notice></>}
          <button className="primary" disabled={!current || busy} onClick={() => void execute(async () => { const policy = { ...access, allowed_users: access.access_mode === "allowlist" ? lines(accessUsers) : [] }; await api.put(`${path}/access`, policy); setCurrent(v => v ? { ...v, access: policy } : v); setNotice("访问权限已立即生效。"); await onSaved(); })}>保存访问权限</button>
        </section>
        <section className="application-settings"><h2>嵌入来源</h2><p className="muted">来源列表随草稿发布；即时撤销用于已发布宿主。</p>
          <label>允许的 iframe 宿主 Origin（一行一个）<textarea rows={6} value={origins} onChange={e => { setOrigins(e.target.value); setDirty(true); }} placeholder="https://portal.example.internal" /></label>
          <Notice>只允许准确的协议、域名和端口；Origin 不代替用户身份。</Notice>
          <h3>已发布宿主</h3>
          {(current?.published_origins ?? []).map(origin => <div className="origin-row" key={origin}><code>{origin}</code>{current?.revoked_origins?.includes(origin) ? <Badge>已撤销</Badge> : <button className="text-button danger" disabled={busy || !publishable} onClick={() => void execute(async () => { await api.post(`${path}/revoke-origin`, { origin }); setCurrent(value => value ? { ...value, revoked_origins: [...new Set([...(value.revoked_origins ?? []), origin])] } : value); setNotice(`已立即撤销 ${origin}；未保存的草稿保持不变。`); await onSaved(); })}>立即撤销</button>}</div>)}
          {!current?.published_origins?.length && <p className="muted small">当前没有已发布的宿主来源。</p>}
        </section>
      </div>}
      {tab === "publish" && <div className="application-two-column">
        <div><section className="application-settings"><h2>功能组合 <Badge>{selected.length} 项</Badge></h2><p className="muted">选择已发布功能。数量不代表真实业务验收结果。</p>
          <div className="capability-picker">{functions.filter(f => selected.includes(f.id) || (f.latest_version && f.status !== "disabled")).map(f => <label className="checkbox" key={f.id}><input type="checkbox" aria-label={`选择功能 ${f.name}`} checked={selected.includes(f.id)} disabled={!selected.includes(f.id) && (!f.latest_version || f.status === "disabled")} onChange={e => { setSelected(e.target.checked ? [...selected, f.id] : selected.filter(id => id !== f.id)); setDirty(true); }} /><span>{f.name}<small className="block muted">{f.latest_version ? `v${f.latest_version}` : "未发布"} · {f.is_fixture ? "开发夹具" : connectorLabels[f.definition.connector.kind] ?? "已接入功能"}{f.status === "disabled" && " · 已停用，请移除或恢复后再发布"}</small></span></label>)}{selected.filter(id => !functions.some(f => f.id === id)).map(id => <label className="checkbox" key={id}><input type="checkbox" aria-label={`移除不可用功能 ${id}`} checked onChange={() => { setSelected(selected.filter(value => value !== id)); setDirty(true); }} /><span>{id}<small className="block muted">已选功能不在当前目录中，可取消选择；可用性由服务端校验。</small></span></label>)}</div>
          {!functions.some(f => f.latest_version) && <Notice>先接入并发布功能，再创建应用。</Notice>}
          <h3>发布渠道</h3>{(["chat", "embed"] as const).map(channel => <label className="checkbox" key={channel}><input type="checkbox" checked={channels[channel]} onChange={e => { setChannels({ ...channels, [channel]: e.target.checked }); setDirty(true); }} />{channel === "chat" ? "独立对话页面" : "iframe 嵌入"}</label>)}
          <Notice>保存草稿后发布。再次发布不会覆盖已执行的即时停用；旧会话保持原版本。</Notice>
          <button className="primary" disabled={busy || dirty || !current || !publishable} onClick={() => void publish()}><Check size={16} />发布当前草稿</button>
        </section>
        <section className="application-settings"><h2>发布记录</h2>{releases.length ? releases.map(r => <div className="release-row" key={r.release_id}><div><strong>v{r.version}</strong><code>{r.release_id}</code><small>{r.capabilities.length} 项功能</small></div><button className="secondary" disabled={busy || dirty || !publishable || current?.current_release_id === r.release_id} onClick={() => void execute(async () => { const value = await api.post<Agent>(`${path}/rollback`, { release_id: r.release_id }); setCurrent(value); setRevisionTick(v => v + 1); setNotice("新会话默认版本已切换，旧会话不迁移。"); await onSaved(); })}>用于新会话</button></div>) : <p className="muted">尚无发布记录。</p>}</section></div>
        <div><section className="application-settings"><h2>已发布应用</h2><p className="muted">当前版本 {current?.current_release_id ?? "尚未发布"}</p>
          {published && <p><strong>{published.runtime_ui.title}</strong><br />{published.runtime_ui.welcome_message}</p>}
          {(["chat", "embed"] as const).map(channel => <div className="channel-row" key={channel}><span>{channel === "chat" ? "独立页面" : "iframe"}<Badge tone={live[channel] && releasedChannels?.[channel] ? "success" : "neutral"}>{live[channel] && releasedChannels?.[channel] ? "可用" : "已停用 / 未发布"}</Badge></span><button className="secondary" disabled={busy || !publishable || !current?.current_release_id || (!live[channel] && !releasedChannels?.[channel])} onClick={() => void changeLive({ ...live, [channel]: !live[channel] })}>{live[channel] ? "立即停用" : "启用渠道"}</button></div>)}
          <label>独立页面链接<input readOnly value={publicUrl} /></label><div className="button-row"><button className="secondary" disabled={!published?.channels.chat || busy} onClick={() => void copy(publicUrl, "页面链接")}><Copy size={15} />复制链接</button>{published?.channels.chat && <a className="text-button" href={appPath(publicId!)} target="_blank" rel="noreferrer">打开应用<ExternalLink size={15} /></a>}</div>
        </section>
        <section className="application-settings"><h2>嵌入到业务系统</h2><p className="muted">iframe 负责加载界面，宿主后端还需完成可信身份换票。</p>
          <label>iframe 代码<textarea rows={4} readOnly value={publicId ? embedSnippet(publicId, current!.id, location.origin) : "发布后生成"} /></label><button className="secondary" disabled={!published?.channels.embed || busy} onClick={() => void copy(embedSnippet(publicId!, current!.id, location.origin), "iframe 代码")}><Copy size={15} />复制 iframe</button>
          <details><summary>宿主握手示例与接入说明</summary><p>先在允许来源中登记宿主，再实现示例中的宿主后端端点。该端点不是平台自动提供；必须校验登录身份、CSRF 和委托权限。登录令牌不写入 HTML。</p><pre className="code-preview">{publicId && handshakeSnippet(publicId, current!.id, location.origin)}</pre><button className="secondary" disabled={!publicId || busy} onClick={() => void copy(handshakeSnippet(publicId!, current!.id, location.origin), "握手示例")}>复制握手 JavaScript</button></details>
        </section></div>
      </div>}
    </fieldset></div>
  </div>;
}
