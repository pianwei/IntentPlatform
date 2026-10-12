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
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  X,
  LogOut,
  MessageSquare,
  Plus,
  Sparkles,
  Square,
} from "lucide-react";
import * as Dialog from "@radix-ui/react-dialog";
import { ComposerDraft, CopyText, TurnContent, groupedMessages, historyGroups, type UserEntry } from "./ChatExperience";
import { ApiClient, errorText } from "../../shared/api";
import { Empty, Notice } from "../../shared/ui";
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
import { TextSizeControl, textScale } from "../../shared/displayPreferences";
import { useChatTextSize } from "./textPreferences";
import "./Application.css";

type EventActions = {
  run: Run | null;
  summaries?: Record<string, Run>;
  busy: boolean;
  onDownload?: (file: RuntimeFile) => Promise<void>;
  onCancel?: () => Promise<void>;
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
const processing = new Set(["RECEIVED", "ROUTING", "EXECUTING"]);
function ChatMessage() {
  const message = useMessage();
  const custom = message.metadata.custom;
  const actions = useContext(Actions);
  const text = message.content.filter(part => part.type === "text").map(part => part.type === "text" ? part.text : "").join("\n");
  return <MessagePrimitive.Root className={`chat-message ${message.role}`}>
    {message.role !== "user" && <div className="message-avatar"><Sparkles size={16} /></div>}
    <div className="message-body">
      {message.role === "assistant" ? <TurnContent events={(custom?.turnEvents ?? []) as RuntimeEvent[]} runId={String(custom?.runId ?? "")} pending={custom?.pending === true} {...actions} /> : <>
        <div className="message-text"><MessagePrimitive.Content />
          {custom?.uncertain === true && <small className="block delivery-warning">发送结果尚未确认，请恢复会话记录后核实，避免重复办理。</small>}
        </div><CopyText text={text} label="复制问题" />
      </>}
    </div>
  </MessagePrimitive.Root>;
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
  const [textSize, setTextSize] = useChatTextSize(app.public_id);
  const [localDraftKey, setLocalDraftKey] = useState(() => `new-${crypto.randomUUID()}`);
  const [historyItems, setHistoryItems] = useState<Conversation[]>([]);
  const [restoring, setRestoring] = useState(true);
  const cursorRef = useRef(0);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [iconUrl, setIconUrl] = useState<string>();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [users, setUsers] = useState<UserEntry[]>([]);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [summaries, setSummaries] = useState<Record<string, Run>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("待建立会话");
  const [reconnect, setReconnect] = useState(0);
  const eventsRef = useRef<RuntimeEvent[]>([]);
  const runRef = useRef<Run | null>(null);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const drafts = useRef<Record<string, string>>({});
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [narrow, setNarrow] = useState(() => window.innerWidth <= 900);
  useEffect(() => {
    const resize = () => setNarrow(window.innerWidth <= 900);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  useEffect(() => {
    generation.current++;
    drafts.current = {};
    inFlight.current = false;
    setLocalDraftKey(`new-${crypto.randomUUID()}`);
    setHistoryQuery("");
    setAgent(null);
    setConversation(null);
    setUsers([]);
    setEvents([]);
    eventsRef.current = [];
    runRef.current = null;
    setRun(null);
    setSummaries({});
    setError("");
    setBusy(false);
    if (!agentId) return;
    const controller = new AbortController();
    setRestoring(true);
    const epoch = generation.current;
    Promise.all([
      api.get<Agent>(`/api/v2/runtime/agents/${encodeURIComponent(agentId)}`, controller.signal),
      api.get<Conversation[]>("/api/v2/runtime/conversations?include_empty=false", controller.signal),
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
          setSummaries(previous => ({ ...previous, [updated.run_id]: updated }));
        }
        failures = 0;
        setConnection("已连接");
      } catch (e) {
        if (!active) return;
        failures++;
        setConnection(
          "连接中断，正在恢复",
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
    try { const items = await api.get<Conversation[]>("/api/v2/runtime/conversations?include_empty=false"); if (epoch === generation.current) setHistoryItems(items); }
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
    setSummaries(Object.fromEntries(snapshot.runs.map(item => [item.run_id, item])));
    remember(snapshot.conversation); setConnection("历史已恢复，继续读取后续事件");
  }
  async function selectConversation(id: string) {
    if (busy || restoring) return;
    const epoch = ++generation.current;
    setHistoryOpen(false);
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
  function fresh() {
    if (busy || restoring || inFlight.current) return;
    setHistoryOpen(false);
    setError("");
    generation.current++;
    eventsRef.current = [];
    cursorRef.current = 0;
    runRef.current = null;
    setEvents([]); setUsers([]); setRun(null); setSummaries({});
    setConversation(null);
    setLocalDraftKey(`new-${crypto.randomUUID()}`);
    saveLocator(storageKey, { runtime_session_id: identity.runtime_session_id });
    setConnection("待建立会话");
  }
  async function send(text: string) {
    if (!text.trim() || !agent || restoring || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const epoch = generation.current;
    const id = crypto.randomUUID();
    let messageStarted = false;
    try {
      let current = conversation;
      if (!current) {
        current = await startConversation();
        if (epoch !== generation.current) return;
        setConversation(current);
        remember(current);
      }
      setUsers((previous) => [...previous, { id, text, delivery: "pending" }]);
      messageStarted = true;
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
        if (!messageStarted) {
          drafts.current[localDraftKey] = text;
          runtime.thread.composer.setText(text);
        }
        setUsers((previous) =>
          previous.map((item) =>
            item.id === id ? { ...item, delivery: "uncertain" } : item,
          ),
        );
      }
    } finally {
      if (epoch === generation.current) { inFlight.current = false; setBusy(false); }
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
  const messages = useMemo(() => groupedMessages(users, events), [users, events]);
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
  const catalog = app.synthetic_catalog?.id === "zhiyu-v1" ? app.synthetic_catalog : null;
  // Current state is supplied by the server, including during history restoration.
  const practiceEvent = [...events].reverse().find(event => event.event_type === "RESULT" && typeof event.payload.practice_active === "boolean");
  const practiceActive = practiceEvent ? practiceEvent.payload.practice_active === true : conversation?.practice_active === true;
  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    convertMessage: (message) => message,
    isRunning: running,
    isDisabled: !agent || restoring || busy,
    onNew: async (message: AppendMessage) => {
      const text = message.content
        .filter((part) => part.type === "text")
        .map((part) => (part.type === "text" ? part.text : ""))
        .join("\n");
      await send(text);
    },
    onCancel: cancelRun,
  });
  const groupedHistory = historyGroups(historyItems, historyQuery);
  const historyPanel = <>
    <div className="runtime-brand"><ApplicationIcon ui={ui} assetUrl={iconUrl} /><strong>{ui.title}</strong>
      {!embedded && !narrow && <button className="icon-button" aria-label="收起历史会话" onClick={() => setHistoryCollapsed(true)}><PanelLeftClose size={17} /></button>}
    </div>
    <button className="new-chat" disabled={!agent || busy || restoring} onClick={() => void fresh()}><Plus size={17} />开始新会话</button>
    <label className="history-search"><Search size={15} /><input aria-label="搜索历史会话" placeholder="搜索会话" value={historyQuery} onChange={e => setHistoryQuery(e.target.value)} /></label>
    <nav className="conversation-history" aria-label="历史会话">
      {groupedHistory.map(([label, items]) => <section key={label}><h2>{label}</h2>{items.map(item => <button key={item.id} disabled={busy || restoring} aria-current={item.id === conversation?.id ? "page" : undefined} className={item.id === conversation?.id ? "active" : ""} onClick={() => void selectConversation(item.id)}><MessageSquare size={15} /><span>{item.title || "新会话"}</span></button>)}</section>)}
      {!groupedHistory.length && <p className="history-empty">{historyQuery ? "没有匹配的会话" : "您的对话会保存在这里"}</p>}
    </nav>
    <div className="sidebar-bottom"><button onClick={() => void onLogout()}><LogOut size={15} />结束访问</button></div>
  </>;
  return <div className={`agent-layout application-runtime ${embedded ? "embedded" : ""} ${historyCollapsed || !ui.show_history ? "history-collapsed" : ""}`} style={{ "--app-accent": ui.primary_color, "--text-scale": textScale[textSize] } as React.CSSProperties}>
    {!embedded && ui.show_history && !historyCollapsed && !narrow && <aside className="agent-sidebar">{historyPanel}</aside>}
    <main className="chat-main">
      <header className="chat-header">
        {ui.show_history && (embedded || narrow || historyCollapsed) && <button className="icon-button" aria-label="展开历史会话" onClick={() => embedded || narrow ? setHistoryOpen(true) : setHistoryCollapsed(false)}><PanelLeftOpen size={19} /></button>}
        <ApplicationIcon ui={ui} assetUrl={iconUrl} />
        <div className="chat-heading"><h1>{ui.title}</h1>{conversation && <p>{historyItems.find(item=>item.id===conversation.id)?.title || conversation.title || "新会话"}</p>}</div>
        <div className="chat-header-right"><TextSizeControl value={textSize} onChange={setTextSize} /><button className="icon-button" aria-label="新会话" title="新会话" disabled={!agent || busy || restoring} onClick={() => void fresh()}><Plus size={19} /></button>
          {(embedded || !ui.show_history || historyCollapsed || narrow) && <button className="icon-button" aria-label="退出会话" title="结束访问" onClick={() => void onLogout()}><LogOut size={17} /></button>}
        </div>
      </header>
      {catalog && practiceActive && <div className="zhiyu-context-bar">
        <button className="end-practice" type="button" disabled={running || !!run && ["WAITING_INPUT", "WAITING_CONFIRMATION"].includes(run.status)} onClick={() => void send("结束陪练，并总结本次陪练反馈。")}>结束陪练并查看反馈</button>
      </div>}
      {error && <div className="chat-notice"><Notice kind="error">{error}<button className="text-button" disabled={busy || restoring} onClick={() => {
        if (conversation) void selectConversation(conversation.id); else { setError(""); void refreshHistory(); }
      }}>恢复会话记录</button></Notice></div>}
      {!agent && !restoring ? <Empty title="当前应用暂不可用">请稍后重试，或联系应用维护者。</Empty> : <AssistantRuntimeProvider runtime={runtime}>
        <Actions.Provider value={{ run, summaries, busy: busy || restoring, onAction: eventAction, onDownload: download, onCancel: cancelRun }}>
          <ThreadPrimitive.Root className="thread-root">
            <ThreadPrimitive.Viewport className="thread-viewport">
              {!users.length && <Welcome ui={ui} assetUrl={iconUrl} disabled={busy || restoring} onSelect={text => { runtime.thread.composer.setText(text); document.querySelector<HTMLTextAreaElement>(".composer-input")?.focus(); }} />}
              <ThreadPrimitive.Messages components={{ UserMessage: ChatMessage, AssistantMessage: ChatMessage }} />
              <ThreadPrimitive.ScrollToBottom className="scroll-bottom" aria-label="滚动至最新消息"><ArrowDown size={15} /><span>回到最新</span></ThreadPrimitive.ScrollToBottom>
            </ThreadPrimitive.Viewport>
            <div className="composer-wrap">
              <ComposerPrimitive.Root className="composer">
                <ComposerDraft conversationKey={conversation?.id ?? localDraftKey} drafts={drafts} />
                <ComposerPrimitive.Input placeholder={ui.input_placeholder} autoFocus cancelOnEscape={false} className="composer-input" aria-label="业务诉求" />
                <div className="composer-actions"><span>Enter 发送 · Shift + Enter 换行</span>
                  {running ? <button className="send-button" type="button" aria-label="停止当前请求" disabled={!run || busy || restoring} onClick={() => void cancelRun()}><Square size={15} /></button> : <ComposerPrimitive.Send className="send-button" aria-label="发送"><ArrowUp size={19} /></ComposerPrimitive.Send>}
                </div>
              </ComposerPrimitive.Root>
              {connection.includes("中断") && <p className="runtime-connection" role="status">正在恢复连接，已受理的请求不会重复提交。</p>}
            </div>
          </ThreadPrimitive.Root>
        </Actions.Provider>
      </AssistantRuntimeProvider>}
    </main>
    <Dialog.Root open={historyOpen} onOpenChange={setHistoryOpen}>
      <Dialog.Portal><Dialog.Overlay className="runtime-dialog-overlay" /><Dialog.Content className="runtime-history-dialog" style={{ "--app-accent": ui.primary_color, "--text-scale": textScale[textSize] } as React.CSSProperties}>
        <Dialog.Title className="runtime-dialog-title">会话</Dialog.Title><Dialog.Description className="runtime-sr-only">搜索历史或开始新会话。切换会话不会重新提交请求。</Dialog.Description>
        <Dialog.Close className="runtime-dialog-close" aria-label="关闭历史会话"><X size={18} /></Dialog.Close>{historyPanel}
      </Dialog.Content></Dialog.Portal>
    </Dialog.Root>
  </div>;
}
