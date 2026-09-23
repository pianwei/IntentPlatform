import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useMessage,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ChevronDown,
  LogOut,
  MessageSquare,
  Plus,
  ShieldCheck,
  Sparkles,
  Square,
} from "lucide-react";
import { ApiClient, errorText } from "../../shared/api";
import { Badge, Busy, Empty, Login, Notice } from "../../shared/ui";
import { embeddingContext, trustedEmbedMessage } from "../../shared/embed";
import { eventCursor, mergeEvents } from "../../shared/events";
import type {
  Agent,
  Conversation,
  Identity,
  Run,
  RuntimeEvent,
  PublicApp,
  ConversationHistory,
  RuntimeFile,
} from "../../shared/types";
import { Welcome, ApplicationIcon } from "../../shared/ApplicationView";
import { readLocator, saveLocator } from "../../shared/application";
import "./Application.css";
import { BindingSummary, ResultCard, statuses } from "./ResultCard";

type EventActions = {
  run: Run | null;
  busy: boolean;
  onDownload?: (file: RuntimeFile) => Promise<void>;
  onAction: (
    event: RuntimeEvent,
    kind: "answers" | "confirmations",
    body: Record<string, unknown>,
  ) => Promise<void>;
};
const Actions = createContext<EventActions>({
  run: null,
  busy: false,
  onAction: async () => {},
});
type UserEntry = {
  id: string;
  text: string;
  runId?: string;
  delivery: "pending" | "accepted" | "uncertain";
};
const processing = new Set(["RECEIVED", "ROUTING", "EXECUTING"]);
function ChatMessage() {
  const message = useMessage();
  const event = message.metadata.custom?.event as RuntimeEvent | undefined;
  const actions = useContext(Actions);
  return (
    <MessagePrimitive.Root className={`chat-message ${message.role}`}>
      <div className="message-avatar">
        {message.role === "user" ? "我" : <Sparkles size={17} />}
      </div>
      <div className="message-body">
        {event ? (
          <ResultCard event={event} {...actions} />
        ) : (
          <div className="message-text">
            <MessagePrimitive.Content />
            {message.metadata.custom?.uncertain === true && (
              <small className="block delivery-warning">
                发送结果尚未确认，请恢复会话记录后核实，避免重复办理。
              </small>
            )}
          </div>
        )}
        {event && event.event_type !== "STATUS" && (
          <details className="event-reference"><summary>查看处理记录</summary>
            <small>请求 {event.run_id.slice(-8)} · 修订 {event.revision} · 事件 {event.sequence}</small>
          </details>
        )}
      </div>
    </MessagePrimitive.Root>
  );
}

export function AgentWorkspace({
  api,
  identity,
  app,
  storageKey,
  embedded,
  onLogout,
}: {
  api: ApiClient;
  identity: Identity;
  app: PublicApp;
  storageKey: string;
  embedded: boolean;
  onLogout: () => Promise<void>;
}) {
  const agentId = app.agent_id;
  const [historyItems, setHistoryItems] = useState<Conversation[]>([]);
  const [restoring, setRestoring] = useState(true);
  const cursorRef = useRef(0);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [iconUrl, setIconUrl] = useState<string>();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [users, setUsers] = useState<UserEntry[]>([]);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("待建立会话");
  const [reconnect, setReconnect] = useState(0);
  const eventsRef = useRef<RuntimeEvent[]>([]);
  const runRef = useRef<Run | null>(null);
  const generation = useRef(0);
  const inFlight = useRef(false);
  useEffect(() => {
    generation.current++;
    setAgent(null);
    setConversation(null);
    setUsers([]);
    setEvents([]);
    eventsRef.current = [];
    runRef.current = null;
    setRun(null);
    setError("");
    setBusy(false);
    if (!agentId) return;
    const controller = new AbortController();
    setRestoring(true);
    const epoch = generation.current;
    Promise.all([
      api.get<Agent>(`/api/v2/runtime/agents/${encodeURIComponent(agentId)}`, controller.signal),
      api.get<Conversation[]>("/api/v2/runtime/conversations", controller.signal),
    ]).then(async ([value, items]) => {
      if (controller.signal.aborted || epoch !== generation.current) return;
      setAgent(value); setHistoryItems(items);
      const remembered = readLocator(storageKey).conversation_id;
      if (remembered && items.some(c => c.id === remembered || c.conversation_id === remembered)) {
        await restore(remembered, epoch);
      }
    }).catch(e => !controller.signal.aborted && setError(errorText(e)))
      .finally(() => { if (!controller.signal.aborted && epoch === generation.current) setRestoring(false); });
    return () => controller.abort();
  }, [api, agentId, storageKey]);
  useEffect(() => {
    if (!conversation) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    const epoch = generation.current;
    const controller = new AbortController();
    async function poll() {
      try {
        const incoming = await api.get<RuntimeEvent[]>(
          `/api/v2/runtime/conversations/${conversation!.id}/events?after=${Math.max(cursorRef.current, eventCursor(eventsRef.current))}`,
          controller.signal,
        );
        if (!active || epoch !== generation.current) return;
        eventsRef.current = mergeEvents(
          eventsRef.current,
          incoming,
          conversation!.id,
        );
        setEvents(eventsRef.current);
        cursorRef.current = Math.max(cursorRef.current, eventCursor(eventsRef.current));
        const currentRunId =
          runRef.current?.run_id ?? eventsRef.current.at(-1)?.run_id;
        if (currentRunId) {
          const updated = await api.get<Run>(
            `/api/v2/runtime/runs/${currentRunId}`,
            controller.signal,
          );
          if (!active || epoch !== generation.current) return;
          runRef.current = updated;
          setRun(updated);
        }
        failures = 0;
        setConnection("事件已连接");
      } catch (e) {
        if (!active) return;
        failures++;
        setConnection(
          `连接中断，正在从事件 ${Math.max(cursorRef.current, eventCursor(eventsRef.current))} 恢复`,
        );
        if (failures === 1) setError(errorText(e));
      } finally {
        if (active)
          timer = setTimeout(
            () => void poll(),
            Math.min(10000, 900 * Math.max(1, failures * 2)),
          );
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [api, conversation, reconnect]);
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  function remember(value: Conversation) {
    saveLocator(storageKey, { runtime_session_id: identity.runtime_session_id, conversation_id: value.id });
  }
  async function refreshHistory() {
    const epoch = generation.current;
    try { const items = await api.get<Conversation[]>("/api/v2/runtime/conversations"); if (epoch === generation.current) setHistoryItems(items); }
    catch (e) { if (epoch === generation.current) setError(errorText(e)); }
  }
  async function restore(id: string, epoch: number) {
    const snapshot = await api.get<ConversationHistory>(`/api/v2/runtime/conversations/${encodeURIComponent(id)}/history`);
    if (epoch !== generation.current) return;
    eventsRef.current = mergeEvents([], snapshot.events, id);
    cursorRef.current = snapshot.cursor;
    const last = snapshot.messages.at(-1)?.run_id;
    runRef.current = snapshot.runs.find(r => r.run_id === last) ?? snapshot.runs.at(-1) ?? null;
    setUsers(snapshot.messages.map(m => ({ id: `history-${m.run_id}`, text: m.text, runId: m.run_id, delivery: "accepted" })));
    setEvents(eventsRef.current); setRun(runRef.current); setConversation(snapshot.conversation);
    remember(snapshot.conversation); setConnection("历史已恢复，继续读取后续事件");
  }
  async function selectConversation(id: string) {
    if (busy || restoring) return;
    const epoch = ++generation.current;
    setRestoring(true); setError(""); setConversation(null); setUsers([]); setEvents([]); setRun(null); eventsRef.current = []; runRef.current = null; cursorRef.current = 0;
    try { await restore(id, epoch); } catch (e) { if (epoch === generation.current) setError(errorText(e)); }
    finally { if (epoch === generation.current) setRestoring(false); }
  }
  async function download(file: RuntimeFile) {
    const epoch = generation.current;
    const blob = await api.blob(`/api/v2/runtime/files/${encodeURIComponent(file.id)}`);
    if (epoch !== generation.current) throw new Error("会话已切换，未继续下载旧会话文件。");
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = file.filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function startConversation() {
    const value = await api.post<Conversation>(
      `/api/v2/runtime/agents/${agentId}/conversations`,
    );
    return { ...value, id: value.id || value.conversation_id };
  }
  async function fresh() {
    if (busy || restoring) return;
    setBusy(true);
    setError("");
    try {
      const value = await startConversation();
      generation.current++;
      eventsRef.current = [];
      cursorRef.current = 0;
      runRef.current = null;
      setEvents([]);
      setUsers([]);
      setRun(null);
      setConversation(value);
      remember(value);
      void refreshHistory();
      setConnection("正在连接事件");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function send(text: string) {
    if (!text.trim() || !agent || restoring || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const epoch = generation.current;
    const id = crypto.randomUUID();
    try {
      let current = conversation;
      if (!current) {
        current = await startConversation();
        if (epoch !== generation.current) return;
        setConversation(current);
        remember(current);
      }
      setUsers((previous) => [...previous, { id, text, delivery: "pending" }]);
      const accepted = await api.post<Run>(
        `/api/v2/runtime/conversations/${current.id}/messages`,
        { text, client_message_id: id },
      );
      if (epoch !== generation.current) return;
      runRef.current = accepted;
      setRun(accepted);
      setUsers((previous) =>
        previous.map((item) =>
          item.id === id
            ? { ...item, runId: accepted.run_id, delivery: "accepted" }
            : item,
        ),
      );
      setReconnect((value) => value + 1);
      void refreshHistory();
    } catch (e) {
      if (epoch === generation.current) {
        setError(errorText(e));
        setUsers((previous) =>
          previous.map((item) =>
            item.id === id ? { ...item, delivery: "uncertain" } : item,
          ),
        );
      }
    } finally {
      inFlight.current = false;
      if (epoch === generation.current) setBusy(false);
    }
  }
  async function eventAction(
    event: RuntimeEvent,
    kind: "answers" | "confirmations",
    body: Record<string, unknown>,
  ) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const epoch = generation.current;
    try {
      const updated = await api.post<Run>(
        `/api/v2/runtime/runs/${event.run_id}/${kind}`,
        { revision: event.revision, ...body },
      );
      if (epoch === generation.current) {
        runRef.current = updated;
        setRun(updated);
        setReconnect((value) => value + 1);
      }
    } catch (e) {
      if (epoch === generation.current) setError(errorText(e));
    } finally {
      inFlight.current = false;
      if (epoch === generation.current) setBusy(false);
    }
  }
  async function cancel() {
    if (!run) return;
    await eventAction(
      { run_id: run.run_id, revision: run.revision } as RuntimeEvent,
      "confirmations",
      { confirmed: false },
    );
  }
  async function cancelRun() {
    if (!run || busy) return;
    setBusy(true);
    try {
      const updated = await api.post<Run>(
        `/api/v2/runtime/runs/${run.run_id}/cancel`,
        { revision: run.revision },
      );
      runRef.current = updated;
      setRun(updated);
      setReconnect((value) => value + 1);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const messages = useMemo(() => {
    const result: ThreadMessageLike[] = [];
    const included = new Set<number>();
    const latestStatus = new Map<string, number>();
    for (const event of events)
      if (event.event_type === "STATUS")
        latestStatus.set(event.run_id, event.sequence);
    const appendEvent = (event: RuntimeEvent) => {
      if (
        event.event_type === "STATUS" &&
        latestStatus.get(event.run_id) !== event.sequence
      )
        return;
      included.add(event.sequence);
      result.push({
        id: `event-${event.conversation_id}-${event.sequence}`,
        role: "assistant",
        content: [
          {
            type: "text",
            text: String(
              event.payload.message ??
                event.payload.title ??
                statuses[String(event.payload.status)] ??
                "业务响应",
            ),
          },
        ],
        metadata: { custom: { event } },
      });
    };
    for (const user of users) {
      result.push({
        id: user.id,
        role: "user",
        content: user.text,
        metadata: { custom: { uncertain: user.delivery === "uncertain" } },
      });
      events
        .filter((event) => event.run_id === user.runId)
        .forEach(appendEvent);
    }
    events
      .filter((event) => !included.has(event.sequence))
      .forEach(appendEvent);
    return result;
  }, [users, events]);
  const ui = conversation?.runtime_ui ?? app.runtime_ui;
  useEffect(() => {
    let active = true, url = "";
    setIconUrl(undefined);
    if (ui.icon.kind === "asset" && ui.icon.value) {
      api.blob(`/api/v2/runtime/assets/${encodeURIComponent(ui.icon.value)}`).then(blob => {
        if (active) { url = URL.createObjectURL(blob); setIconUrl(url); }
      }).catch(e => active && setError(errorText(e)));
    }
    return () => { active = false; if (url) URL.revokeObjectURL(url); };
  }, [api, ui.icon.kind, ui.icon.value]);
  const running = busy || restoring || (!!run && processing.has(run.status));
  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    convertMessage: (message) => message,
    isRunning: running,
    isDisabled: !agent || restoring,
    onNew: async (message: AppendMessage) => {
      const text = message.content
        .filter((part) => part.type === "text")
        .map((part) => (part.type === "text" ? part.text : ""))
        .join("\n");
      await send(text);
    },
    onCancel: cancelRun,
  });
  return (
    <div className={`agent-layout application-runtime ${embedded ? "embedded" : ""} ${!ui.show_history ? "hide-history" : ""}`} style={{ "--app-accent": ui.primary_color } as React.CSSProperties}>
      {!embedded && ui.show_history && (
        <aside className="agent-sidebar">
          <div className="runtime-brand"><ApplicationIcon ui={ui} assetUrl={iconUrl} /><strong>{ui.title}</strong></div>
          <button className="new-chat" disabled={!agent || busy || restoring} onClick={() => void fresh()}><Plus size={18} />开始新会话</button>
          <div className="nav-label">历史会话</div>
          <div className="agent-nav conversation-history">
            {historyItems.map(item => <button key={item.id} disabled={busy || restoring} className={item.id === conversation?.id ? "active" : ""} onClick={() => void selectConversation(item.id)}>
              <MessageSquare size={16} /><span>{item.title || "新会话"}<small>{item.updated_at ? new Date(item.updated_at).toLocaleDateString() : ""}</small></span>
            </button>)}
            {!historyItems.length && <p className="muted small">开始对话后，会话将显示在这里。</p>}
          </div>
          <div className="sidebar-bottom"><p>{identity.user_id}<br />仅使用当前应用已授权功能</p><button onClick={() => void onLogout()}><LogOut size={16} />退出</button></div>
        </aside>
      )}
      <main className="chat-main">
        <header className="chat-header">
          <div className="chat-heading-icon">
            <ApplicationIcon ui={ui} assetUrl={iconUrl} />
          </div>
          <div>
            <h1>{ui.title}</h1>
            <p>
              {agent
                ? `${agent.capabilities?.length ?? 0} 项已授权功能 · ${agent.fixture_mode ? "本地开发夹具" : "已发布业务能力"}`
                : "选择已发布的智能体"}
            </p>
          </div>
          <div className="chat-header-right">
            {identity.fixture_mode && <Badge tone="warning">开发环境</Badge>}
            <div className={`runtime-header-actions ${embedded || !ui.show_history ? "always" : "compact"}`}>
                <button
                  className="secondary"
                  disabled={!agent || busy}
                  onClick={() => void fresh()}
                >
                  <Plus size={15} />
                  新会话
                </button>
                <button
                  className="icon-button"
                  aria-label="退出会话"
                  onClick={() => void onLogout()}
                >
                  <LogOut size={18} />
                </button>
            </div>
          </div>
        </header>
        {error && (
          <div className="chat-notice">
            <Notice kind="error">
              {error}
              <button
                className="text-button"
                disabled={busy || restoring}
                onClick={() => {
                  if (conversation) void selectConversation(conversation.id);
                  else { setError(""); void refreshHistory(); }
                }}
              >
                恢复会话记录
              </button>
            </Notice>
          </div>
        )}
        {!agent && !restoring ? (
          <Empty title="暂无可运行的智能体">
            请由维护者在 Console 组合并发布功能，或检查当前身份授权。
          </Empty>
        ) : (
          <AssistantRuntimeProvider runtime={runtime}>
            <Actions.Provider value={{ run, busy: busy || restoring, onAction: eventAction, onDownload: download }}>
              <ThreadPrimitive.Root className="thread-root">
                <ThreadPrimitive.Viewport className="thread-viewport">
                  {users.length === 0 && (
                    <div>
                      <Welcome ui={ui} assetUrl={iconUrl} disabled={busy || restoring} onSelect={text => void send(text)} />
                      {app.fixture_mode && <p className="runtime-fixture-note">开发夹具 · 仅用于本地流程验证，不代表真实业务或 SSO 验收</p>}
                    </div>
                  )}
                  <ThreadPrimitive.Messages
                    components={{
                      UserMessage: ChatMessage,
                      AssistantMessage: ChatMessage,
                    }}
                  />
                  <ThreadPrimitive.ScrollToBottom
                    className="scroll-bottom"
                    aria-label="滚动至最新消息"
                  >
                    <ArrowDown size={17} />
                  </ThreadPrimitive.ScrollToBottom>
                </ThreadPrimitive.Viewport>
                <div className="composer-wrap">
                  <BindingSummary bindings={run?.bindings} />
                  {run && (
                    <div className="conversation-status">
                      <span className="status-dot" />
                      {statuses[run.status] ?? run.status}
                      <span>{connection}</span>
                      {["WAITING_INPUT", "WAITING_CONFIRMATION"].includes(
                        run.status,
                      ) && (
                        <button
                          className="text-button"
                          onClick={() => void cancelRun()}
                        >
                          取消当前请求
                        </button>
                      )}
                    </div>
                  )}
                  <ComposerPrimitive.Root className="composer">
                    <ComposerPrimitive.Input
                      placeholder={ui.input_placeholder}
                      autoFocus
                      className="composer-input"
                      aria-label="业务诉求"
                    />
                    <div className="composer-actions">
                      <span>
                        <ShieldCheck size={13} />
                        可信执行 · 可追溯结果
                      </span>
                      {running ? (
                        <button
                          className="send-button"
                          type="button"
                          aria-label="停止当前请求"
                          onClick={() => void cancelRun()}
                        >
                          <Square size={16} />
                        </button>
                      ) : (
                        <ComposerPrimitive.Send
                          className="send-button"
                          aria-label="发送"
                        >
                          <ArrowUp size={20} />
                        </ComposerPrimitive.Send>
                      )}
                    </div>
                  </ComposerPrimitive.Root>
                  <p className="composer-footnote">
                    结果由业务系统提供，请核对重要信息。
                  </p>
                </div>
              </ThreadPrimitive.Root>
            </Actions.Provider>
          </AssistantRuntimeProvider>
        )}
      </main>
    </div>
  );
}
