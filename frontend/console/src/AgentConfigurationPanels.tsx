import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Copy, ExternalLink, Plus, Trash2 } from "lucide-react";
import { Modal } from "./ModernUI";
import { Badge, Notice } from "../../shared/ui";
import { errorText, type ApiClient } from "../../shared/api";
import { appPath, embedSnippet, handshakeSnippet } from "../../shared/application";
import type { Agent, Capability, PublishedFunction, Release } from "../../shared/types";
import { VersionHistoryDialog } from "./VersionHistoryDialog";

export function SuggestedQuestionsEditor({ value, onChange }: { value: string[]; onChange: (value: string[]) => void }) {
  function move(index: number, direction: number) {
    const next = [...value];
    [next[index], next[index + direction]] = [next[index + direction], next[index]];
    onChange(next);
  }
  return <section className="question-editor" aria-label="推荐问题编辑">
    <div className="setting-heading"><h3>推荐问题</h3><span className="muted small">{value.length} / 12</span></div>
    <div className="editable-rows">{value.map((text, index) => <div className="editable-row" key={index}>
      <span className="row-number">{index + 1}</span>
      <input aria-label={`推荐问题 ${index + 1}`} placeholder="例如：帮我准备明天的客户拜访" maxLength={200} value={text} onChange={e => onChange(value.map((old, i) => i === index ? e.target.value : old))} />
      <button type="button" className="icon-button" aria-label={`上移推荐问题 ${index + 1}`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp size={14} /></button>
      <button type="button" className="icon-button" aria-label={`下移推荐问题 ${index + 1}`} disabled={index === value.length - 1} onClick={() => move(index, 1)}><ArrowDown size={14} /></button>
      <button type="button" className="icon-button" aria-label={`删除推荐问题 ${index + 1}`} onClick={() => onChange(value.filter((_, i) => i !== index))}><Trash2 size={14} /></button>
    </div>)}</div>
    <button type="button" className="text-button" disabled={value.length >= 12} onClick={() => onChange([...value, ""])}><Plus size={14} />添加推荐问题</button>
  </section>;
}

export function validatedOrigin(input: string): string {
  const text = input.trim();
  let url: URL;
  try { url = new URL(text); } catch { throw new Error("请输入完整网站地址，例如 https://portal.example.internal"); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || (url.pathname !== "/" && url.pathname !== "")) {
    throw new Error("网站地址只包含协议、域名和端口，不包含页面路径、参数或账号。");
  }
  return url.origin;
}

export function WebsiteOriginsEditor({ value, onChange }: { value: string[]; onChange: (value: string[]) => void }) {
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  function add() {
    try {
      const origin = validatedOrigin(input);
      if (value.includes(origin)) throw new Error("此网站已经添加。");
      onChange([...value, origin]); setInput(""); setError("");
    } catch (e) { setError(errorText(e)); }
  }
  return <section className="website-editor">
    <h3>允许嵌入的网站</h3>
    <p className="muted small">填写承载聊天窗口的业务网站，例如 https://portal.example.internal。新增网站在发布后生效。</p>
    <div className="editable-rows">{value.map(origin => <div className="editable-row website-row" key={origin}><span>{origin}</span><button type="button" className="icon-button" aria-label={`删除网站 ${origin}`} onClick={() => onChange(value.filter(item => item !== origin))}><Trash2 size={15} /></button></div>)}</div>
    <div className="inline-input"><input aria-label="新增嵌入网站" value={input} onChange={e => { setInput(e.target.value); setError(""); }} placeholder="https://portal.example.internal" onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); add(); } }} /><button type="button" className="button" disabled={!input.trim()} onClick={add}><Plus size={14} />添加网站</button></div>
    {error && <p className="field-error" role="alert">{error}</p>}
  </section>;
}

export function VersionHistory({ api, workspace, agent, releases, functions, busy, error, publishable, dirty, admin=false, onClose, onRollback }: {
  api: ApiClient; workspace: string; agent: Agent; releases: Release[]; functions: Capability[];
  busy: boolean; error: string; publishable: boolean; dirty: boolean; admin?:boolean; onClose: () => void; onRollback: (release: Release) => Promise<boolean>;
}) {
  const [selected, setSelected] = useState<Release | null>(() => releases.find(r => r.release_id === agent.current_release_id) ?? releases[0] ?? null);
  const [confirm, setConfirm] = useState(false);
  const [names, setNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  useEffect(() => {
    if (!selected && releases.length) setSelected(releases.find(r => r.release_id === agent.current_release_id) ?? releases[0]);
  }, [selected, releases, agent.current_release_id]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    setLoading(true); setNames({}); setDetailError("");
    const ids = [...new Set(selected.capabilities.map(c => c.capability_id))];
    Promise.all(ids.map(async id => {
      const versions = await api.get<PublishedFunction[]>(`/api/v2/management/workspaces/${encodeURIComponent(workspace)}/functions/${encodeURIComponent(id)}/versions`);
      const version = selected.capabilities.find(c => c.capability_id === id)?.version;
      const fixed = versions.find(v => v.version === version);
      if (!fixed) throw new Error("固定功能版本已不可读取，不能用草稿替代。");
      return [id, fixed.definition.name] as const;
    })).then(entries => active && setNames(Object.fromEntries(entries))).catch(e => active && setDetailError(errorText(e))).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [api, workspace, selected]);
  const active = releases.find(r => r.release_id === agent.current_release_id);
  const removed = active?.capabilities.filter(c => !selected?.capabilities.some(old => old.capability_id === c.capability_id)) ?? [];
  return <VersionHistoryDialog api={api} versions={releases.map(release=>({id:release.release_id,version:release.version,created_at:release.created_at,definition:release.summary,current:release.release_id===agent.current_release_id,caption:`${release.capabilities.length} 项功能`}))} selectedId={selected?.release_id??null} onSelect={id=>{setSelected(releases.find(release=>release.release_id===id)??null);setConfirm(false);}} onClose={onClose} busy={busy} resourceId={agent.id} auditAllowed={admin} auditPath={`/api/v2/management/workspaces/${encodeURIComponent(workspace)}/audit?resource_id=${encodeURIComponent(agent.id)}`} footer={<footer className="modal-footer"><button type="button" className="button" disabled={busy} onClick={() => confirm ? setConfirm(false) : onClose()}>{confirm ? "取消回退" : "关闭"}</button><button type="button" className="button primary" disabled={busy || dirty || loading || !!detailError || !publishable || !selected || selected.capabilities.some(c => !names[c.capability_id]) || selected.release_id === agent.current_release_id} onClick={() => { if (!selected) return; if (!confirm) setConfirm(true); else void onRollback(selected).then(ok => { if (ok) setConfirm(false); }); }}>{confirm ? "确认回退，用于新会话" : "用于新会话"}</button></footer>}>
    {!selected?<p className="muted">尚无发布记录。</p>:<><h3>v{selected.version} 的业务能力</h3><p className="muted small">回退只影响新会话；草稿、已有会话和即时停用保持不变。</p>{loading&&<p className="muted" role="status">正在读取版本详情…</p>}{detailError&&<Notice kind="error">功能详情未能读取：{detailError}</Notice>}<ul className="release-functions">{selected.capabilities.map(capability=>{const current=active?.capabilities.find(c=>c.capability_id===capability.capability_id);return <li key={capability.capability_id}><span>{names[capability.capability_id]??"版本功能详情未读取"}</span><Badge>v{capability.version}</Badge>{active&&selected.release_id!==active.release_id&&<small>{!current?"新增":current.version!==capability.version?`当前 v${current.version}`:"版本相同"}</small>}</li>;})}</ul>{!!removed.length&&<details><summary>此版本不包含 {removed.length} 项当前能力</summary><ul>{removed.map(c=><li key={c.capability_id}>{functions.find(f=>f.id===c.capability_id)?.name??"历史功能"}</li>)}</ul></details>}{confirm&&<Notice>确认让“{agent.name}”的新会话使用 v{selected.version}？服务端会重新检查依赖是否可用。</Notice>}{dirty&&<p className="muted small">请先保存或处理当前未保存修改，再回退版本。</p>}{error&&<Notice kind="error">{error}</Notice>}</>}
  </VersionHistoryDialog>;
}

export function ShareApplication({ agent, chatAvailable, embedAvailable, busy, error, onClose, onCopy, onAccess }: {
  agent: Agent; chatAvailable: boolean; embedAvailable: boolean; busy: boolean; error: string;
  onClose: () => void; onCopy: (value: string, label: string) => Promise<unknown>; onAccess: () => void;
}) {
  const publicId = agent.public_id;
  const url = publicId ? new URL(appPath(publicId), location.origin).href : "";
  const [copied, setCopied] = useState("");
  async function copy(value: string, label: string) { if (await onCopy(value, label)) setCopied(`${label}已复制`); }
  return <Modal title="分享与嵌入" wide busy={busy} onClose={onClose}><div className="modal-body share-application">
    <section><div className="setting-heading"><h3>公开聊天链接</h3><Badge tone={chatAvailable ? "success" : "neutral"}>{chatAvailable ? "已开放" : "未开放"}</Badge></div><label>独立页面链接<input readOnly value={url} /></label><div className="button-row"><button type="button" className="button" disabled={!chatAvailable || busy} onClick={() => void copy(url, "页面链接")}><Copy size={15} />复制链接</button>{chatAvailable && <a className="button" href={appPath(publicId!)} target="_blank" rel="noreferrer">打开应用<ExternalLink size={15} /></a>}</div></section>
    <section><div className="setting-heading"><h3>嵌入聊天窗口</h3><Badge tone={embedAvailable ? "success" : "neutral"}>{embedAvailable ? "已开放" : "未开放"}</Badge></div><p className="muted small">宿主后端使用用户 API Key 换取一次性票据，密钥不放入网页或链接。</p><label>iframe 代码<textarea rows={4} readOnly value={publicId ? embedSnippet(publicId, agent.id, location.origin) : "发布后生成"} /></label><button type="button" className="button" disabled={!embedAvailable || busy} onClick={() => void copy(embedSnippet(publicId!, agent.id, location.origin), "iframe 代码")}><Copy size={15} />复制 iframe</button>
      <details><summary>身份握手与接入说明</summary><p>在访问控制中登记承载 iframe 的业务网站。宿主后端实现换票端点，校验当前访客、Origin 和 CSRF，并维护各访客独立的恢复关联。</p><pre className="code-preview">{publicId && handshakeSnippet(publicId, agent.id, location.origin)}</pre><button type="button" className="button" disabled={!publicId || busy} onClick={() => void copy(handshakeSnippet(publicId!, agent.id, location.origin), "握手示例")}>复制握手 JavaScript</button></details>
    </section>
    {copied && <p role="status" className="muted">{copied}</p>}{error && <Notice kind="error">{error}</Notice>}
  </div><footer className="modal-footer"><button type="button" className="text-button" disabled={busy} onClick={onAccess}>配置访问方式</button><button type="button" className="button" disabled={busy} onClick={onClose}>关闭</button></footer></Modal>;
}
