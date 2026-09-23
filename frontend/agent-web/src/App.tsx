import { useEffect, useMemo, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { ApiClient, errorText } from "../../shared/api";
import { Busy, Empty, Login, Notice } from "../../shared/ui";
import { applicationRoute, clearLocator, memoryKey, readLocator, saveLocator } from "../../shared/application";
import { embeddingContext, trustedEmbedMessage } from "../../shared/embed";
import type { Identity, PublicApp } from "../../shared/types";
import { AgentWorkspace } from "./AgentWorkspace";

export function AgentApp() {
  const route = useMemo(() => applicationRoute(location.pathname), []);
  const context = useMemo(() => embeddingContext(location, document.referrer), []);
  const publicId = route?.publicId ?? "";
  const key = memoryKey(publicId, context.isEmbed, context.hostOrigin);
  const [app, setApp] = useState<PublicApp | null>(null);
  const [loading, setLoading] = useState(true);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [error, setError] = useState("");
  const [renewal, setRenewal] = useState(0);
  const [hostLoggedOut, setHostLoggedOut] = useState(false);
  const [handshake, setHandshake] = useState<{ instance_nonce: string; expires_at: string } | null>(null);
  const authEpoch = useRef(0);
  const nonce = useRef("");
  const requestedSession = useRef(readLocator(key).runtime_session_id);
  const expireRef = useRef<() => void>(() => {});
  const api = useMemo(() => new ApiClient("runtime", () => expireRef.current()), []);
  function notifyHost(type: string, extra: Record<string, unknown> = {}) {
    if (context.isEmbed && context.hostOrigin && app && nonce.current) {
      window.parent.postMessage({ type, protocol_version: "1", agent_id: app.agent_id, instance_nonce: nonce.current, ...extra }, context.hostOrigin);
    }
  }
  expireRef.current = () => {
    authEpoch.current++; api.clear(); setIdentity(null); setError("身份已失效，请重新连接以恢复会话。");
    notifyHost("AUTH_REQUIRED", { runtime_session_id: requestedSession.current });
    setRenewal(v => v + 1);
  };
  function accept(value: Identity) {
    api.setToken(value.access_token);
    requestedSession.current = value.runtime_session_id;
    saveLocator(key, { ...readLocator(key), runtime_session_id: value.runtime_session_id });
    setIdentity(value); setError("");
  }
  async function logout() {
    authEpoch.current++;
    const revoke = api.post("/api/v2/runtime/sessions/logout");
    api.clear(); clearLocator(key); requestedSession.current = undefined;
    setIdentity(null); setHostLoggedOut(context.isEmbed);
    notifyHost("AUTH_REQUIRED");
    try { await revoke; setError("已退出；重新连接将建立新运行会话。"); }
    catch (e) { setError(`本页凭证已清除，服务端退出未确认：${errorText(e)}`); }
  }
  useEffect(() => {
    if (!publicId) { setLoading(false); return; }
    let active = true;
    const publicApi = new ApiClient("runtime");
    async function load() {
      try {
        const value = await publicApi.get<PublicApp>(`/api/v2/runtime/apps/${encodeURIComponent(publicId)}`);
        if (!active) return;
        if (!(context.isEmbed ? value.channels.embed : value.channels.chat)) throw new Error("此访问渠道已停用。");
        setApp(value);
      } catch (e) { if (active) { setApp(null); setError(errorText(e)); api.clear(); setIdentity(null); } }
      finally { if (active) setLoading(false); }
    }
    void load();
    const timer = setInterval(() => void load(), 15000);
    return () => { active = false; clearInterval(timer); };
  }, [publicId, context.isEmbed, api]);
  useEffect(() => {
    if (!identity?.expires_at) return;
    const remaining = new Date(identity.expires_at).getTime() - Date.now() - 15000;
    const timer = setTimeout(() => expireRef.current(), Math.max(1000, remaining));
    return () => clearTimeout(timer);
  }, [identity]);
  useEffect(() => {
    if (!context.isEmbed || !app || hostLoggedOut) return;
    if (!context.hostOrigin || window.parent === window) { setError("无法确认可信宿主，请从已登记的业务系统打开。"); return; }
    let active = true, consumed = false, localNonce = "";
    const agentId = app.agent_id;
    const resumeId = requestedSession.current;
    const epochAtStart = authEpoch.current;
    const listener = async (event: MessageEvent) => {
      if (!active || !trustedEmbedMessage(event, context.hostOrigin, window.parent, agentId, localNonce)) return;
      if (event.data.type === "HOST_LOGOUT") { await logout(); return; }
      if (consumed || (event.data.runtime_session_id !== undefined && event.data.runtime_session_id !== resumeId)) return;
      consumed = true;
      const epoch = authEpoch.current;
      try {
        const value = await api.post<Identity>("/api/v2/runtime/embed/exchange", { ticket: event.data.ticket, agent_id: agentId, host_origin: context.hostOrigin, instance_nonce: localNonce, runtime_session_id: resumeId });
        if (active && epoch === authEpoch.current) {
          accept(value);
          notifyHost("AGENT_AUTHENTICATED", { runtime_session_id: value.runtime_session_id });
        }
      } catch (e) {
        if (active && epoch === authEpoch.current) {
          setError(errorText(e)); notifyHost("AGENT_ERROR", { message: "身份兑换失败，请由宿主重新连接。" });
        }
      }
    };
    window.addEventListener("message", listener);
    api.post<{ instance_nonce: string; expires_at: string }>("/api/v2/runtime/embed/handshakes", { agent_id: agentId, host_origin: context.hostOrigin }).then(value => {
      if (!active || epochAtStart !== authEpoch.current) return;
      nonce.current = localNonce = value.instance_nonce; setHandshake(value);
      window.parent.postMessage({ type: "AGENT_READY", protocol_version: "1", agent_id: agentId, instance_nonce: localNonce, runtime_session_id: resumeId }, context.hostOrigin);
    }).catch(e => active && setError(errorText(e)));
    return () => { active = false; window.removeEventListener("message", listener); };
  }, [api, app?.agent_id, context.isEmbed, context.hostOrigin, renewal, hostLoggedOut]);
  useEffect(() => {
    if (!context.isEmbed || !handshake) return;
    const observer = new ResizeObserver(() => notifyHost("AGENT_RESIZE", { height: Math.min(1200, Math.max(480, document.documentElement.scrollHeight)) }));
    observer.observe(document.body); return () => observer.disconnect();
  }, [context.isEmbed, handshake]);
  if (!route) return <Empty title="请使用已发布的应用链接">由管理平台打开 /chat/ 或 /embed/ 应用地址。</Empty>;
  if (loading) return <Busy />;
  if (!app) return <Empty title="应用暂不可用">{error || "尚未发布或访问渠道已停用。"}</Empty>;
  if (!identity) return context.isEmbed ? <div className="embed-wait"><Sparkles size={30} /><h2>{app.runtime_ui.title}</h2>{error ? <Notice kind="error">{error}</Notice> : <p>等待可信宿主完成身份握手…</p>}
    <button className="secondary" onClick={() => { setHostLoggedOut(false); setError(""); setRenewal(v => v + 1); }}>重新连接宿主</button>
    {requestedSession.current && <button className="text-button" onClick={() => { clearLocator(key); requestedSession.current = undefined; setHostLoggedOut(false); setRenewal(v => v + 1); }}>放弃本地恢复标识，建立新会话</button>}
  </div> : <>{error && <Notice kind="error">{error}</Notice>}<Login api={api} onLogin={accept} publicId={publicId} runtimeSessionId={requestedSession.current} title={app.runtime_ui.title} />
    {requestedSession.current && <div className="resume-choice"><p>重新验证身份后恢复此应用的运行会话。</p><button className="text-button" onClick={() => { clearLocator(key); requestedSession.current = undefined; setRenewal(v => v + 1); }}>使用新身份或放弃恢复，建立新会话</button></div>}
  </>;
  return <AgentWorkspace key={`${identity.user_id}:${identity.runtime_session_id}`} api={api} identity={identity} app={app} storageKey={key} embedded={context.isEmbed} onLogout={logout} />;
}
