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

export function embedSnippet(publicId: string, agentId: string, origin: string) {
  const src = new URL(appPath(publicId, true), origin).href;
  return `<iframe id="intent-agent" src="${src}" title="业务助手" width="100%" height="720" referrerpolicy="origin" style="border:0"></iframe>`;
}
export function handshakeSnippet(publicId: string, agentId: string, origin: string) {
  return `// 宿主后端需实现 /internal/intent/embed-ticket；验证当前登录用户，
// 使用受托身份调用平台 /api/v2/integrations/embed-tickets。不得向浏览器暴露管理令牌。
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
        instance_nonce: nonce, runtime_session_id: data.runtime_session_id })
    });
    if (!response.ok) throw new Error('宿主身份换票失败');
    const { ticket } = await response.json();
    if (nonce !== data.instance_nonce) return;
    frame.contentWindow.postMessage({ type: 'AGENT_INIT', protocol_version: '1',
      agent_id: agentId, instance_nonce: nonce, ticket,
      runtime_session_id: data.runtime_session_id }, agentOrigin);
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
