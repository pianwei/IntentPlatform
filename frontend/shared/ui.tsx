import { useState, type ReactNode } from "react";
import {
  AlertCircle,
  ArrowRight,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import { ApiClient, developmentLogin, errorText } from "./api";
import type { Identity } from "./types";
import { DEVELOPMENT_UI } from "./environment";
export function Notice({
  children,
  kind = "info",
}: {
  children: ReactNode;
  kind?: "info" | "error" | "success";
}) {
  return (
    <div
      className={`notice ${kind}`}
      role={kind === "error" ? "alert" : "status"}
    >
      <AlertCircle size={17} />
      <div>{children}</div>
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-symbol">◇</div>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Busy() {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={18} className="spin" />
      正在连接服务…
    </div>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
export function Login({
  api,
  onLogin,
  title = "连接工作空间",
  publicId,
  runtimeSessionId,
}: {
  api: ApiClient;
  onLogin: (identity: Identity) => void;
  title?: string;
  publicId?: string;
  runtimeSessionId?: string;
}) {
  const [identityToken, setIdentityToken] = useState("");
  const [user, setUser] = useState("dev-admin");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function connect(dev: boolean) {
    setBusy(true);
    setError("");
    try {
      const loginApi = publicId && dev ? new ApiClient("management") : api;
      let identity = dev
        ? await developmentLogin(loginApi, user)
        : await api.post<Identity>("/api/v2/auth/exchange", {
            identity_token: identityToken,
            ...(publicId ? { public_id: publicId, runtime_session_id: runtimeSessionId } : {}),
          });
      if (publicId && dev) {
        try {
          identity = await loginApi.post<Identity>(`/api/v2/runtime/apps/${encodeURIComponent(publicId)}/sessions`, { runtime_session_id: runtimeSessionId });
        } finally { loginApi.clear(); }
      }
      api.setToken(identity.access_token);
      setIdentityToken("");
      onLogin(identity);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-screen">
      <div className="login-brand">
        <div className="brand-icon">i</div>
        <span>意图中台</span>
        <small>INTENT PLATFORM</small>
      </div>
      <section className="login-card">
        <ShieldCheck size={32} className="accent" />
        <h1>{title}</h1>
        <p className="muted">
          使用可信身份进入已授权的空间。会话凭证仅保存在当前页面内存中。
        </p>
        <p className="muted small">身份服务适配入口；真实行内 SSO 尚待接入验证。</p>
        {error && <Notice kind="error">{error}</Notice>}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void connect(false);
          }}
        >
          <label>
            行内身份凭证
            <input
              type="password"
              value={identityToken}
              onChange={(e) => setIdentityToken(e.target.value)}
              placeholder="由行内身份服务签发"
              autoComplete="off"
              required
            />
          </label>
          <button className="primary full" disabled={busy || !identityToken}>
            连接身份服务 <ArrowRight size={16} />
          </button>
        </form>
        {DEVELOPMENT_UI && (
          <div className="dev-login">
            <Badge tone="warning">本地开发</Badge>
            <p>以下身份仅用于隔离与流程验证，不代表真实业务账号。</p>
            <select
              aria-label="开发身份"
              value={user}
              onChange={(e) => setUser(e.target.value)}
            >
              <option value="dev-admin">开发维护者 · 工作空间 A</option>
              <option value="dev-reader">开发只读用户 · 工作空间 A</option>
              <option value="dev-other">开发用户 · 工作空间 B</option>
            </select>
            <button
              className="secondary full"
              disabled={busy}
              onClick={() => void connect(true)}
            >
              使用开发身份
            </button>
          </div>
        )}
      </section>
      <p className="login-foot">内网运行 · 空间隔离 · 版本可追溯</p>
    </div>
  );
}
