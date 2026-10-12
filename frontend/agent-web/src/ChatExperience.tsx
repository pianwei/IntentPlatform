import { useEffect, useState, type MutableRefObject } from "react";
import { useComposerRuntime, type ThreadMessageLike } from "@assistant-ui/react";
import { Check, Copy, LoaderCircle, Square } from "lucide-react";
import type { Conversation, Run, RuntimeEvent, RuntimeFile } from "../../shared/types";
import { ResultCard, canInteract, statuses } from "./ResultCard";

export type UserEntry = { id: string; text: string; runId?: string; delivery: "pending" | "accepted" | "uncertain" };

/** One stable assistant message per request: events update that answer in place. */
export function groupedMessages(users: UserEntry[], events: RuntimeEvent[]): ThreadMessageLike[] {
  const result: ThreadMessageLike[] = [];
  const used = new Set<string>();
  const byRun = new Map<string, RuntimeEvent[]>();
  for (const event of events) byRun.set(event.run_id, [...(byRun.get(event.run_id) ?? []), event]);
  function reply(id: string, pending = false) {
    used.add(id);
    const items = byRun.get(id) ?? [];
    result.push({ id: `reply-${id}`, role: "assistant", content: [{ type: "text", text: "业务答复" }],
      metadata: { custom: { turnEvents: items, runId: id, pending } } });
  }
  for (const user of users) {
    result.push({ id: user.id, role: "user", content: user.text,
      metadata: { custom: { uncertain: user.delivery === "uncertain" } } });
    if (user.runId) reply(user.runId);
    else if (user.delivery === "pending") reply(user.id, true);
  }
  for (const id of byRun.keys()) if (!used.has(id)) reply(id);
  return result;
}

export function historyGroups(items: Conversation[], query: string, today = new Date()) {
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1).getTime();
  const week = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 7).getTime();
  const groups = new Map<string, Conversation[]>();
  for (const item of [...items].sort((a, b) => (Date.parse(b.updated_at ?? "") || 0) - (Date.parse(a.updated_at ?? "") || 0))) {
    if (!`${item.title ?? "新会话"}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) continue;
    const stamp = Date.parse(item.updated_at ?? item.created_at ?? "");
    const label = stamp >= start ? "今天" : stamp >= yesterday ? "昨天" : stamp >= week ? "近 7 天" : "更早";
    groups.set(label, [...(groups.get(label) ?? []), item]);
  }
  return [...groups.entries()];
}

/** Drafts remain in this runtime component only; credentials and drafts aren't persisted to browser storage. */
export function ComposerDraft({ conversationKey, drafts }: { conversationKey: string; drafts: MutableRefObject<Record<string, string>> }) {
  const composer = useComposerRuntime();
  useEffect(() => {
    composer.setText(drafts.current[conversationKey] ?? "");
    return composer.subscribe(() => { drafts.current[conversationKey] = composer.getState().text; });
  }, [composer, conversationKey, drafts]);
  return null;
}

export function CopyText({ text, label = "复制" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const timer = setTimeout(() => setState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [state]);
  return <button type="button" className="runtime-copy" aria-label={label} onClick={async () => {
    try { await navigator.clipboard.writeText(text); setState("copied"); } catch { setState("failed"); }
  }}>{state === "copied" ? <Check size={14} /> : <Copy size={14} />}<span aria-live="polite">{state === "copied" ? "已复制" : state === "failed" ? "复制失败，请手动选择" : label}</span></button>;
}

export function TurnContent({ events, runId, pending, run, summaries, busy, onAction, onDownload, onCancel }: {
  events: RuntimeEvent[]; runId: string; pending?: boolean; run: Run | null; busy: boolean;
  summaries?: Record<string, Run>;
  onAction: (event: RuntimeEvent, kind: "answers" | "confirmations", body: Record<string, unknown>) => Promise<void>;
  onDownload?: (file: RuntimeFile) => Promise<void>; onCancel?: () => Promise<void>;
}) {
  const current = run?.run_id === runId ? run : null;
  const saved = current ?? summaries?.[runId];
  const statusEvent = [...events].reverse().find(event => event.event_type === "STATUS");
  const status = current?.status ?? String(statusEvent?.payload.status ?? saved?.status ?? (pending ? "RECEIVED" : ""));
  const working = ["RECEIVED", "ROUTING", "EXECUTING"].includes(status);
  const interactions = events.filter(event => event.event_type === "QUESTION" || event.event_type === "CONFIRMATION");
  const active = interactions.filter(event => canInteract(event, current));
  const historical = interactions.filter(event => !canInteract(event, current));
  const results = events.filter(event => event.event_type === "RESULT");
  const errors = events.filter(event => event.event_type === "ERROR");
  const copied = results.map(event => {
    const data = event.payload;
    if (data.result_unavailable === true) return "业务操作已确认成功，但结果当前不可展示。";
    return [typeof data.data === "string" ? data.data : data.data ? JSON.stringify(data.data, null, 2) : "",
      Array.isArray(data.components) ? data.components.filter(component => component && ["table", "detail", "citation", "text", "markdown"].includes(component.component))
        .map(component => `${component.title ?? ""}\n${typeof component.data === "string" ? component.data : JSON.stringify(component.data, null, 2) ?? ""}`).join("\n") : ""].filter(Boolean).join("\n");
  }).filter(Boolean).join("\n\n");
  return <div className="runtime-turn" data-testid="answer-group">
    {working && <div className="turn-progress" role="status"><LoaderCircle size={15} className="spin" />{status === "ROUTING" ? "正在理解您的问题…" : status === "EXECUTING" ? "正在处理…" : "正在接收…"}</div>}
    {historical.length > 0 && <details className="past-interactions"><summary>已结束的交互 · {historical.length}</summary>
      {historical.map(event => <p key={event.sequence}>{String(event.payload.message ?? (event.event_type === "CONFIRMATION" ? "操作确认" : "补充信息"))}<small>此交互已结束，无需再次提交</small></p>)}
    </details>}
    {active.map(event => <ResultCard key={event.sequence} event={event} run={current} busy={busy} onAction={onAction} onDownload={onDownload} />)}
    {results.map(event => <ResultCard key={event.sequence} event={event} run={current} busy={busy} onAction={onAction} onDownload={onDownload} />)}
    {errors.map(event => <ResultCard key={event.sequence} event={event} run={current} busy={busy} onAction={onAction} onDownload={onDownload} />)}
    {!working && !active.length && !results.length && !errors.length && status && <p className="turn-empty-status">{statuses[status] ?? "请求已更新"}</p>}
    <div className="turn-tools">
      {copied && <CopyText text={copied} label="复制答复" />}
      {status && !working && <span className={`turn-status state-${status.toLowerCase()}`}>{statuses[status] ?? "已更新"}</span>}
      {current && ["RECEIVED", "ROUTING", "EXECUTING", "WAITING_INPUT", "WAITING_CONFIRMATION"].includes(status) &&
        <button className="runtime-copy" type="button" disabled={busy} onClick={() => void onCancel?.()}><Square size={12} />取消当前请求</button>}
      {!working && status && <span className="turn-duration" title={saved?.timing?.total_ms != null ? `整轮 ${(saved.timing.total_ms / 1000).toFixed(1)} 秒` : undefined}>
        {saved?.timing?.processing_ms != null ? `处理 ${(saved.timing.processing_ms / 1000).toFixed(1)} 秒` : "耗时未记录"}
        {saved?.timing?.user_wait_ms ? ` · 等待 ${(saved.timing.user_wait_ms / 1000).toFixed(1)} 秒` : ""}
      </span>}
    </div>
  </div>;
}
