import { useEffect, useRef, useState } from "react";
import { Check, KeyRound, Plus, ShieldCheck, SlidersHorizontal, UserRound } from "lucide-react";
import { ApiClient, errorText } from "../../shared/api";
import type { Profile, UserKey } from "../../shared/accounts";
import { roleNames } from "../../shared/accounts";
import { Notice } from "../../shared/ui";
import { Modal, Status } from "./ModernUI";
import { TextSizeControl, type TextSize } from "../../shared/displayPreferences";

export function SecretOnce({ title, secret, onClose }: { title: string; secret: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  return <Modal title={title} onClose={onClose}><div className="modal-body"><p>完整值仅在此显示一次，请通过安全渠道保存。</p><textarea className="secret-once" readOnly value={secret} aria-label="一次性凭据" rows={3} autoComplete="off" /><button className="button" onClick={async () => { try { await navigator.clipboard.writeText(secret); setCopied(true); } catch { setError("无法访问剪贴板，请选中上方内容手动复制。"); } }}>{copied ? "已复制" : "复制"}</button>{error && <Notice kind="error">{error}</Notice>}</div><footer className="modal-footer"><button className="button primary" onClick={onClose}>已保存，关闭</button></footer></Modal>;
}
export function PersonalCenter({ api, profile, onProfile, onClose, onPasswordChanged, initialTab = "资料" }: { api: ApiClient; profile: Profile; onProfile: (p: Profile) => void; onClose: () => void; onPasswordChanged: () => void; initialTab?: string }) {
  const [tab, setTab] = useState(initialTab);
  const [display, setDisplay] = useState(profile.display_name);
  const [passwords, setPasswords] = useState(["", "", ""]);
  const [keys, setKeys] = useState<UserKey[]>([]);
  const [keyName, setKeyName] = useState("");
  const [expires, setExpires] = useState("");
  const [secret, setSecret] = useState("");
  const [copied, setCopied] = useState(false);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [textSize, setTextSize] = useState<TextSize>(profile.preferences?.text_size ?? "standard");
  const [pendingExit, setPendingExit] = useState<"close" | "cancel-key" | null>(null);
  const [keyAction, setKeyAction] = useState<{ key: UserKey; action: "rotate" | "revoke" } | null>(null);
  const discardNotice = useRef<HTMLDivElement>(null);
  const secretInput = useRef<HTMLTextAreaElement>(null);
  const dirtyKey = creating && !!(keyName || expires);
  const dirty = display.trim() !== profile.display_name || passwords.some(Boolean) || dirtyKey;
  useEffect(() => { setTextSize(profile.preferences?.text_size ?? "standard"); }, [profile.preferences?.text_size]);
  useEffect(() => { if (pendingExit) discardNotice.current?.focus(); }, [pendingExit]);
  useEffect(() => { if (secret) secretInput.current?.focus(); }, [secret]);
  useEffect(() => {
    let active = true;
    if (tab === "API Key") api.get<UserKey[]>("/api/v2/me/api-keys").then(k => { if (active) setKeys(k); }).catch(e => active && setError(errorText(e)));
    return () => { active = false; };
  }, [api, tab]);
  async function perform(action: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(""); setSuccess(""); setPendingExit(null);
    try { await action(); } catch (e) { setError(errorText(e)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function saveProfile(patch: Record<string, unknown>) {
    const p = await api.patch<Profile>("/api/v2/me", patch);
    onProfile(p); setSuccess("已保存");
    if ("display_name" in patch) setDisplay(p.display_name);
  }
  function changeTextSize(value: TextSize) {
    void perform(async () => {
      const previous = profile;
      setTextSize(value);
      onProfile({ ...profile, preferences: { ...profile.preferences, text_size: value } });
      try { await saveProfile({ preferences: { text_size: value } }); }
      catch (e) { setTextSize(previous.preferences?.text_size ?? "standard"); onProfile(previous); throw e; }
    });
  }
  function requestClose() {
    if (inFlight.current) return;
    if (dirty || secret) setPendingExit("close"); else onClose();
  }
  function cancelCreate() {
    if (inFlight.current) return;
    if (dirtyKey) setPendingExit("cancel-key");
    else { setCreating(false); setKeyName(""); setExpires(""); }
  }
  function acceptExit() {
    if (inFlight.current) return;
    if (pendingExit === "cancel-key") { setCreating(false); setKeyName(""); setExpires(""); setPendingExit(null); }
    else onClose();
  }
  async function refreshKeysAfterMutation(hasSecret = false) {
    try { setKeys(await api.get<UserKey[]>("/api/v2/me/api-keys")); }
    catch { setError(hasSecret ? "操作已完成，但列表刷新失败。请先保存新凭据，再重新打开 API Key 列表核对。" : "操作已完成，但列表刷新失败。请重新打开 API Key 列表核对。"); }
  }
  function showSecret(value: string) { setSecret(value); setCopied(false); setPendingExit(null); }
  async function mutateKey(action: "rotate" | "revoke", key: UserKey) {
    const result = await api.post<{ secret?: string }>(`/api/v2/me/api-keys/${encodeURIComponent(key.id)}/${action}`);
    setKeyAction(null);
    if (result.secret) showSecret(result.secret);
    else setSuccess("Key 已撤销");
    await refreshKeysAfterMutation(!!result.secret);
  }
  const notice = pendingExit && <div className="notice warning" role="alert" ref={discardNotice} tabIndex={-1}>
    <div><p>{secret ? `完整凭据仅显示一次，请确认已经安全保存后再关闭。${dirty ? "另外还有未保存的修改，关闭后将丢弃。" : ""}` : "有未保存的修改。离开后这些输入将不会保留。"}</p>
      <div className="button-row"><button className="button" onClick={() => setPendingExit(null)}>{secret ? "继续保存凭据" : "继续编辑"}</button><button className="button danger-text" onClick={acceptExit}>{secret ? dirty ? "已保存，放弃修改并关闭" : "已保存并关闭" : "放弃修改"}</button></div></div>
  </div>;
  return <Modal title={secret ? "保存新的 API Key" : "个人中心"} onClose={requestClose} wide={!secret} busy={busy}>
    {secret ? <>
      <div className="modal-body">{notice}{error && <Notice kind="error">{error}</Notice>}
        <p>完整值仅在此显示一次，请通过安全渠道保存。</p>
        <textarea ref={secretInput} className="secret-once" readOnly value={secret} aria-label="一次性凭据" rows={3} autoComplete="off" />
        <button className="button" onClick={async () => { try { await navigator.clipboard.writeText(secret); setCopied(true); } catch { setError("无法访问剪贴板，请选中上方内容手动复制。"); } }}>{copied ? "已复制" : "复制"}</button>
      </div><footer className="modal-footer"><button className="button primary" disabled={busy} onClick={() => { setSecret(""); setPendingExit(null); }}>已保存，关闭</button></footer>
    </> : <div className="profile-layout">
      <nav aria-label="个人中心分类">{[["资料", UserRound], ["角色权限", ShieldCheck], ["账户安全", KeyRound], ["API Key", KeyRound], ["偏好设置", SlidersHorizontal]].map(([label, Icon]) => {
        const I = Icon as typeof UserRound;
        return <button key={String(label)} disabled={busy || !!keyAction} className={tab === label ? "active" : ""} onClick={() => { setTab(String(label)); setError(""); setSuccess(""); setPendingExit(null); }}><I size={16} />{String(label)}</button>;
      })}</nav>
      <div className="profile-content">{notice}{error && <Notice kind="error">{error}</Notice>}{success && <Notice kind="success">{success}</Notice>}
        {keyAction ? <section><h3>{keyAction.action === "rotate" ? "轮换 API Key？" : "撤销 API Key？"}</h3><p>“{keyAction.key.name}”的旧 Key 和派生运行会话将立即失效。</p><div className="button-row"><button className="button" disabled={busy} onClick={() => setKeyAction(null)}>取消</button><button className="button primary" disabled={busy} onClick={() => void perform(() => mutateKey(keyAction.action, keyAction.key))}>确认</button></div></section> : <>
          {tab === "资料" && <><div className="profile-identity"><span className="avatar large">{profile.display_name.slice(0, 1)}</span><div><h3>{profile.display_name}</h3><p>{profile.username}</p></div></div>
            <form onSubmit={e => { e.preventDefault(); void perform(() => saveProfile({ display_name: display.trim() })); }}>
              <label className="field">显示名称<input disabled={busy} required maxLength={120} value={display} onChange={e => setDisplay(e.target.value)} /></label>
              <dl className="info-list"><div><dt>用户名</dt><dd>{profile.username}</dd></div><div><dt>账号 ID</dt><dd>{profile.user_id}</dd></div><div><dt>所属空间</dt><dd>{profile.workspaces.map(w => w.name).join("、") || "尚未分配"}</dd></div></dl><button className="button primary" disabled={busy}>保存资料</button>
            </form></>}
          {tab === "角色权限" && <><h3>当前权限</h3>{profile.is_platform_admin && <div className="permission-row"><ShieldCheck size={18} /><div><strong>平台管理员</strong><p>管理账号、创建空间和分配管理员</p></div></div>}{profile.workspaces.map(w => <div className="permission-row" key={w.id}><UserRound size={18} /><div><strong>{w.name}</strong><p>{w.roles.map(r => roleNames[r] || r).join("、")}</p></div></div>)}</>}
          {tab === "账户安全" && <><h3>修改密码</h3><p className="muted">修改后需重新登录，已创建的 API Key 保持有效。</p>
            <form onSubmit={e => { e.preventDefault(); void perform(async () => { if (passwords[1] !== passwords[2]) throw new Error("两次输入的新密码不一致"); await api.post("/api/v2/me/password", { current_password: passwords[0], new_password: passwords[1] }); setPasswords(["", "", ""]); onPasswordChanged(); }); }}>
              {["当前密码", "新密码", "确认新密码"].map((label, i) => <label className="field" key={label}>{label}<input required disabled={busy} type="password" minLength={i ? 12 : undefined} maxLength={128} autoComplete={i ? "new-password" : "current-password"} value={passwords[i]} onChange={e => setPasswords(v => v.map((p, j) => j === i ? e.target.value : p))} /></label>)}<button className="button primary" disabled={busy}>确认修改</button>
            </form></>}
          {tab === "API Key" && <>
            <div className="section-caption"><h3>我的 API Key</h3>{!creating && <button className="button" disabled={busy} onClick={() => setCreating(true)}><Plus size={14} />创建 Key</button>}</div><p className="muted">用于宿主后端接入，继承你的当前业务权限。</p>
            {creating && <form className="key-create" onSubmit={e => { e.preventDefault(); void perform(async () => {
              const r = await api.post<{ key: UserKey; secret: string }>("/api/v2/me/api-keys", { name: keyName.trim(), expires_at: expires ? new Date(expires).toISOString() : null });
              showSecret(r.secret); setCreating(false); setKeyName(""); setExpires(""); await refreshKeysAfterMutation(true);
            }); }}>
              <label className="field">名称 <em>*</em><input required disabled={busy} maxLength={120} value={keyName} onChange={e => setKeyName(e.target.value)} placeholder="例如：客户服务门户" /></label><label className="field">到期时间 <span className="optional">选填</span><input disabled={busy} type="datetime-local" value={expires} onChange={e => setExpires(e.target.value)} /></label>
              <div className="button-row"><button className="button" type="button" disabled={busy} onClick={cancelCreate}>取消创建</button><button className="button primary" disabled={busy}>创建并显示一次</button></div>
            </form>}
            {!keys.length && !creating && <p className="empty-state">还没有 API Key</p>}
            {keys.map(k => <div className="key-row" key={k.id}><KeyRound size={17} /><div><strong>{k.name}</strong><small>{k.prefix || "••••"} · {k.expires_at ? `到期 ${new Date(k.expires_at).toLocaleDateString()}` : "无到期时间"}</small></div><Status value={k.revoked ? "已撤销" : k.expires_at && new Date(k.expires_at) < new Date() ? "已过期" : "有效"} /><button className="text-button" disabled={busy || k.revoked || creating} onClick={() => setKeyAction({ key: k, action: "rotate" })}>轮换</button><button className="text-button danger-text" disabled={busy || k.revoked || creating} onClick={() => setKeyAction({ key: k, action: "revoke" })}>撤销</button></div>)}
          </>}
          {tab === "偏好设置" && <><h3>文字大小</h3><p className="muted">立即预览并保存到当前账号，适用于管理平台。</p><TextSizeControl value={textSize} onChange={changeTextSize} disabled={busy} /><button className="text-button" disabled={busy || textSize === "standard"} onClick={() => changeTextSize("standard")}>恢复默认字号</button>
            <h3>列表密度</h3><div className="density-options">{([["comfortable", "舒适"], ["compact", "紧凑"]] as const).map(([value, label]) => <button key={value} disabled={busy} aria-pressed={(profile.preferences?.density || "comfortable") === value} className={(profile.preferences?.density || "comfortable") === value ? "selected" : ""} onClick={() => void perform(() => saveProfile({ preferences: { density: value } }))}><div>{[0, 1, 2].map(i => <i key={i} />)}</div><span>{label}{(profile.preferences?.density || "comfortable") === value && <Check size={14} />}</span></button>)}</div>
          </>}
        </>}
      </div>
    </div>}
  </Modal>;
}
