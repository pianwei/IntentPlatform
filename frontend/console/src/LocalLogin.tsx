import { useState } from "react";
import { ArrowRight, KeyRound } from "lucide-react";
import { ApiClient, errorText } from "../../shared/api";
import { Notice } from "../../shared/ui";
import type { Identity } from "../../shared/types";

export function LocalLogin({ api, onLogin }: { api: ApiClient; onLogin: (identity: Identity) => void }) {
  const [setup, setSetup] = useState(new URLSearchParams(location.search).get("setup") === "1");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  async function submit() {
    setBusy(true); setError(""); setSuccess("");
    try {
      if (setup) {
        if (password !== confirm) throw new Error("两次密码不一致");
        await api.post("/api/v2/auth/set-password", { setup_token: credential.trim(), new_password: password });
        setCredential(""); setPassword(""); setConfirm(""); setSetup(false); setSuccess("密码已设置，请登录。");
        history.replaceState(null, "", "/console/");
      } else {
        const identity = await api.post<Identity>("/api/v2/auth/login", { username: username.trim(), password });
        setPassword(""); api.setToken(identity.access_token); onLogin(identity);
      }
    } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); }
  }
  return <div className="login-screen local-login"><a className="login-brand" href="/console/"><span className="brand-icon">i</span>意图中台</a><section className="login-card"><KeyRound size={28} /><h1>{setup ? "设置账号密码" : "登录管理平台"}</h1>
    {error && <Notice kind="error">{error}</Notice>}{success && <Notice kind="success">{success}</Notice>}
    <form onSubmit={e => { e.preventDefault(); void submit(); }}>
      {setup ? <label>一次性设置凭证<input required type="password" autoComplete="off" value={credential} onChange={e => setCredential(e.target.value)} placeholder="粘贴管理员提供的凭证" /></label> : <label>用户名<input required autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} placeholder="输入用户名" maxLength={80} /></label>}
      <label>{setup ? "新密码" : "密码"}<input required type="password" autoComplete={setup ? "new-password" : "current-password"} minLength={setup ? 12 : undefined} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} placeholder={setup ? "至少 12 个字符" : "输入密码"} /></label>
      {setup && <label>确认新密码<input required type="password" autoComplete="new-password" value={confirm} onChange={e => setConfirm(e.target.value)} /></label>}
      <button className="primary full" disabled={busy}>{busy ? "正在处理…" : setup ? "设置密码" : "登录"}<ArrowRight size={15} /></button>
    </form><button className="text-button" disabled={busy} onClick={() => { setSetup(!setup); setError(""); setPassword(""); setConfirm(""); setCredential(""); }}>{setup ? "返回登录" : "首次使用或管理员已重置密码"}</button>
  </section></div>;
}
