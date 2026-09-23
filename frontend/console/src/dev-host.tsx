import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiClient, errorText } from "../../shared/api";
import { Login, Notice } from "../../shared/ui";
import type { Agent, Identity } from "../../shared/types";
import "../../shared/styles.css";
import { DEVELOPMENT_UI } from "../../shared/environment";
function DevelopmentHost() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const api = useMemo(() => new ApiClient("management"), []);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("等待 iframe 加载");
  const [height, setHeight] = useState(720);
  const frame = useRef<HTMLIFrameElement>(null);
  const nonce = useRef("");
  const [agentOrigin, setAgentOrigin] = useState(location.origin);
  const [originInput, setOriginInput] = useState(location.origin);
  useEffect(() => {
    if (identity)
      api
        .get<Agent[]>("/api/v2/runtime/agents")
        .then((items) => {
          setAgents(items);
          setAgentId(items[0]?.id ?? "");
        })
        .catch((e) => setError(errorText(e)));
  }, [api, identity]);
  useEffect(() => {
    if (!identity || !agentId) return;
    nonce.current = "";
    const issued = new Set<string>();
    let active = true;
    async function receive(event: MessageEvent) {
      const data = event.data;
      if (
        event.origin !== agentOrigin ||
        event.source !== frame.current?.contentWindow ||
        !data ||
        data.protocol_version !== "1" ||
        data.agent_id !== agentId ||
        typeof data.instance_nonce !== "string" ||
        !data.instance_nonce
      )
        return;
      if (data.type === "AGENT_READY") {
        if (issued.has(data.instance_nonce)) return;
        issued.add(data.instance_nonce);
        nonce.current = data.instance_nonce;
        setStatus("正在通过受托开发身份申请一次性票据");
        try {
          const result = await api.post<{ ticket: string }>(
            "/api/v2/integrations/embed-tickets",
            {
              agent_id: agentId,
              host_origin: location.origin,
              instance_nonce: data.instance_nonce,
              runtime_session_id: data.runtime_session_id,
            },
          );
          if (active && nonce.current === data.instance_nonce) {
            frame.current?.contentWindow?.postMessage(
              {
                type: "AGENT_INIT",
                protocol_version: "1",
                agent_id: agentId,
                instance_nonce: data.instance_nonce,
                ticket: result.ticket,
                runtime_session_id: data.runtime_session_id,
              },
              agentOrigin,
            );
            setStatus("已发送一次性票据，等待运行会话");
          }
        } catch (e) {
          if (active) setError(errorText(e));
        }
      } else if (data.instance_nonce === nonce.current) {
        if (data.type === "AGENT_AUTHENTICATED")
          setStatus("运行会话已建立，可开始对话");
        if (data.type === "AGENT_RESIZE" && Number.isFinite(data.height))
          setHeight(Math.min(1000, Math.max(480, data.height)));
        if (data.type === "AUTH_REQUIRED")
          setStatus("身份需要续期，等待新的握手并重新申请票据");
        if (data.type === "AGENT_ERROR")
          setError(String(data.message ?? "智能体连接失败"));
      }
    }
    window.addEventListener("message", receive);
    return () => {
      active = false;
      window.removeEventListener("message", receive);
    };
  }, [identity, agentId, agentOrigin, api]);
  if (!DEVELOPMENT_UI)
    return <Notice kind="error">该联调宿主仅在显式开发构建中可用。</Notice>;
  if (!identity)
    return (
      <Login api={api} onLogin={setIdentity} title="本地 iframe 联调宿主" />
    );
  return (
    <main className="dev-host">
      <div className="page-heading">
        <div>
          <div className="eyebrow">LOCAL INTEGRATION HOST</div>
          <h1>智能体 iframe 联调</h1>
          <p>
            开发模式用受托开发用户模拟宿主后端；正式宿主必须由可信后端申请票据。
          </p>
        </div>
        <button
          className="secondary"
          onClick={() => {
            frame.current?.contentWindow?.postMessage(
              {
                type: "HOST_LOGOUT",
                protocol_version: "1",
                agent_id: agentId,
                instance_nonce: nonce.current,
              },
              agentOrigin,
            );
            setStatus("已发送宿主退出事件");
          }}
        >
          通知智能体退出
        </button>
      </div>
      <form onSubmit={e => { e.preventDefault(); try {
        const parsed = new URL(originInput);
        if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) throw new Error("请输入准确的 HTTP(S) Origin，不含路径、凭据或查询参数。");
        setAgentOrigin(parsed.origin); setError(""); setStatus("等待新来源的 iframe 握手");
      } catch(e) { setError(errorText(e)); } }}>
        <label>智能体站点 Origin<input value={originInput} onChange={e => setOriginInput(e.target.value)} placeholder="http://127.0.0.1:5173" /></label>
        <button className="secondary">应用测试来源</button>
        <p className="muted small">宿主实际 Origin：{location.origin}。需同时登记发布来源与开发宿主 CSP；不能通过此字段伪造宿主来源。</p>
      </form>
      <Notice>
        来源：{location.origin} → {agentOrigin}。校验 Origin、窗口
        source、协议版本和实例 nonce。
      </Notice>
      {error && <Notice kind="error">{error}</Notice>}
      <label>
        选择已发布的智能体
        <select
          value={agentId}
          onChange={(e) => {
            setAgentId(e.target.value);
            setStatus("等待新 iframe 实例");
          }}
        >
          {agents.map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.name}
            </option>
          ))}
        </select>
      </label>
      <p role="status" className="muted">
        {status}
      </p>
      {agentId && (
        <iframe
          key={`${agentOrigin}:${agentId}`}
          ref={frame}
          title="智能体运行界面"
          src={`${agentOrigin}/embed/${encodeURIComponent(agents.find(a => a.id === agentId)?.public_id ?? agentId)}`}
          referrerPolicy="origin"
          style={{ height }}
        />
      )}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<DevelopmentHost />);
