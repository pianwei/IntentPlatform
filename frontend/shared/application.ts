import type { Agent, RuntimeUI } from "./types";

export function runtimeUI(agent?: Pick<Agent, "name" | "suggested_questions" | "runtime_ui"> | null): RuntimeUI {
  return {
    title: agent?.name || "业务助手",
    icon: { kind: "emoji", value: "✦" },
    description: "通过已授权的业务功能，为您处理工作中的问题。",
    welcome_message: "有什么可以帮您",
    suggested_questions: agent?.suggested_questions ?? [],
    primary_color: "#137C8B",
    input_placeholder: "输入您的业务诉求…",
    show_history: true,
    ...agent?.runtime_ui,
  };
}
export function appPath(publicId: string, embedded = false) {
  return `/${embedded ? "embed" : "chat"}/${encodeURIComponent(publicId)}`;
}
export function canOpenPublishedChat(agent?: Pick<Agent, "current_release_id" | "published_channels" | "live_channels"> | null) {
  // Draft channel edits do not change the currently published entry point.
  return !!agent?.current_release_id && agent.published_channels?.chat === true && agent.live_channels?.chat === true;
}
export function applicationRoute(path: string) {
  const match = path.match(/^\/(chat|embed)\/([^/]+)\/?$/);
  if (!match) return null;
  try { return { publicId: decodeURIComponent(match[2]), embedded: match[1] === "embed" }; }
  catch { return null; }
}
export function memoryKey(publicId: string, embedded = false, hostOrigin = "") {
  return `intent.runtime.${publicId}.${embedded ? hostOrigin : "chat"}`;
}
export type SessionLocator = { runtime_session_id?: string; conversation_id?: string };
export function readLocator(key: string): SessionLocator {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "{}");
    return {
      runtime_session_id: typeof value.runtime_session_id === "string" ? value.runtime_session_id : undefined,
      conversation_id: typeof value.conversation_id === "string" ? value.conversation_id : undefined,
    };
  } catch { return {}; }
}
export function saveLocator(key: string, value: SessionLocator) {
  try {
    // Only these nonsecret locators are persisted. Credentials never enter storage.
    sessionStorage.setItem(key, JSON.stringify({ runtime_session_id: value.runtime_session_id, conversation_id: value.conversation_id }));
  } catch { /* Storage can be disabled in a host; the current session still works. */ }
}
export function clearLocator(key: string) { try { sessionStorage.removeItem(key); } catch { /* no storage */ } }

// Anonymous recovery is a narrowly scoped secret, separate from in-memory access tokens.
// Embedded applications never read or write this credential.
export type AnonymousRecovery = { runtime_session_id?: string; recovery_secret?: string; demo_persona?: string };
function anonymousKey(publicId: string, persona?: string) { return `intent.anonymous.${publicId}${persona ? `.persona.${persona}` : ""}`; }
export function readAnonymous(publicId: string, persona?: string): AnonymousRecovery {
  try { const v=JSON.parse(localStorage.getItem(anonymousKey(publicId, persona))||"{}");
    return typeof v.runtime_session_id==="string"&&typeof v.recovery_secret==="string" ? {
      runtime_session_id: v.runtime_session_id, recovery_secret: v.recovery_secret,
      ...(typeof v.demo_persona === "string" ? { demo_persona: v.demo_persona } : {}),
    } : {};
  } catch { return {}; }
}
export function saveAnonymous(publicId: string, runtimeSessionId: string, recoverySecret: string, persona?: string) {
  try {
    const value = JSON.stringify({runtime_session_id:runtimeSessionId,recovery_secret:recoverySecret,...(persona ? {demo_persona: persona} : {})});
    localStorage.setItem(anonymousKey(publicId), value);
    if (persona) localStorage.setItem(anonymousKey(publicId, persona), value);
  } catch { /* Current session remains usable without persistent history. */ }
}
export function clearAnonymous(publicId: string) { try {
  const persona = readAnonymous(publicId).demo_persona;
  localStorage.removeItem(anonymousKey(publicId));
  if (persona) localStorage.removeItem(anonymousKey(publicId, persona));
} catch { /* no storage */ } }

export function embedSnippet(publicId: string, agentId: string, origin: string) {
  const src = new URL(appPath(publicId, true), origin).href;
  return `<iframe id="intent-agent" src="${src}" title="业务助手" width="100%" height="720" referrerpolicy="origin" style="border:0"></iframe>`;
}
export function handshakeSnippet(publicId: string, agentId: string, origin: string) {
  return `// 宿主后端需实现 /internal/intent/embed-ticket；验证当前访客，
// 用用户绑定的 API Key 调用 /api/v2/integrations/embed-tickets。
// Key 仅留在后端；visitor_id 与恢复关联也由后端维护，不信任浏览器传入的会话编号。
const frame = document.getElementById('intent-agent');
const agentOrigin = ${JSON.stringify(origin)};
const agentId = ${JSON.stringify(agentId)};
let nonce = '';
window.addEventListener('message', async (event) => {
  const data = event.data;
  if (event.origin !== agentOrigin || event.source !== frame.contentWindow ||
      !data || data.protocol_version !== '1' || data.agent_id !== agentId ||
      typeof data.instance_nonce !== 'string' || !data.instance_nonce) return;
  if (data.type === 'AGENT_READY') {
    nonce = data.instance_nonce;
    const response = await fetch('/internal/intent/embed-ticket', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId, host_origin: location.origin,
        instance_nonce: nonce })
    });
    if (!response.ok) throw new Error('宿主身份换票失败');
    const { ticket, runtime_session_id } = await response.json();
    if (nonce !== data.instance_nonce) return;
    frame.contentWindow.postMessage({ type: 'AGENT_INIT', protocol_version: '1',
      agent_id: agentId, instance_nonce: nonce, ticket,
      runtime_session_id }, agentOrigin);
  }
  // AUTH_REQUIRED 后等待新的 AGENT_READY，按同样流程续期。
  // AGENT_RESIZE 可按宿主允许的高度范围处理。
});
// 宿主退出：先在后端撤销关联运行会话，再通知 iframe。
function notifyAgentLogout() {
  frame.contentWindow.postMessage({ type: 'HOST_LOGOUT', protocol_version: '1',
    agent_id: agentId, instance_nonce: nonce }, agentOrigin);
}
// 应用地址：${new URL(appPath(publicId), origin).href}`;
}
