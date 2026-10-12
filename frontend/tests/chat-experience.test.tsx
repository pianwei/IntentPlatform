import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentWorkspace } from "../agent-web/src/AgentWorkspace";
import { groupedMessages, historyGroups, TurnContent } from "../agent-web/src/ChatExperience";
import { BusinessResultContent } from "../shared/BusinessResult";
import { ApiClient } from "../shared/api";
import { memoryKey, runtimeUI, saveLocator } from "../shared/application";
import type { Conversation, PublicApp, RuntimeEvent, Run } from "../shared/types";

const ui = runtimeUI({ name: "统一业务助手", suggested_questions: [] });
const app: PublicApp = { public_id: "chat-experience", agent_id: "chat-agent", release_id: "release-a", runtime_ui: ui, channels: { chat: true, embed: true }, fixture_mode: true };
const identity = { user_id: "guest-a", agent_id: app.agent_id, access_token: "runtime-test-value", runtime_session_id: "session-a", fixture_mode: true };
const conversation = (id: string, title: string, updated_at?: string): Conversation => ({ id, conversation_id: id, agent_id: app.agent_id, release_id: "release-a", status: "active", fixture_mode: true, title, updated_at });
const event = (type: string, sequence: number, payload: Record<string, unknown>, revision = 1): RuntimeEvent => ({ schema_version: "1", conversation_id: "c-a", run_id: "r-a", revision, sequence, event_type: type, payload } as RuntimeEvent);
const respond = (data: unknown) => Promise.resolve(new Response(JSON.stringify(data)));
beforeEach(() => {
  Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollTo = vi.fn(); Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("focused question and answer experience", () => {
  it("keeps one stable answer identity while status and approved results grow", () => {
    const users = [{ id: "u", text: "查余额", runId: "r-a", delivery: "accepted" as const }];
    const first = groupedMessages(users, [event("STATUS", 1, { status: "ROUTING" })]);
    const next = groupedMessages(users, [event("STATUS", 1, { status: "ROUTING" }), event("RESULT", 2, { data: "余额 100" }), event("STATUS", 3, { status: "SUCCEEDED" })]);
    expect(first).toHaveLength(2); expect(next).toHaveLength(2);
    expect(first[1].id).toBe(next[1].id);
    expect(next[1].metadata?.custom?.turnEvents).toHaveLength(3);
  });

  it("collapses obsolete forms and preserves the exact current question revision", async () => {
    const old = event("QUESTION", 1, { question_id: "old", message: "旧问题", fields: [{ name: "old", type: "string" }] });
    const current = event("QUESTION", 2, { question_id: "current", message: "请补充账号", fields: [{ name: "account", label: "账号", type: "string", required: true }] }, 2);
    const run = { run_id: "r-a", revision: 2, status: "WAITING_INPUT" } as Run;
    const onAction = vi.fn().mockResolvedValue(undefined);
    const view = render(<TurnContent events={[old, current]} runId="r-a" run={run} busy={false} onAction={onAction} />);
    expect(screen.queryByLabelText("old")).toBeNull();
    fireEvent.change(screen.getByLabelText("账号 *"), { target: { value: "A100" } });
    fireEvent.click(screen.getByRole("button", { name: "提交补充信息" }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith(current, "answers", { question_id: "current", values: { account: "A100" } }));
    view.rerender(<TurnContent events={[old, current]} runId="r-a" run={{ ...run, revision: 3, status: "SUCCEEDED" }} busy={false} onAction={onAction} />);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText("已结束的交互 · 2")).toBeVisible();
  });

  it("searches conversation titles and groups by local calendar day", () => {
    const today = new Date(2026, 8, 29, 12);
    const items = [conversation("a", "客户资金", new Date(2026, 8, 29, 1).toISOString()), conversation("b", "客户资料", new Date(2026, 8, 28, 23).toISOString()), conversation("c", "贷款", new Date(2026, 8, 15).toISOString())];
    expect(historyGroups(items, "客户", today).map(([title, rows]) => [title, rows.length])).toEqual([["今天", 1], ["昨天", 1]]);
    expect(historyGroups(items, "不存在", today)).toEqual([]);
  });

  it("shows recorded historical processing and waiting time without inventing missing durations", () => {
    const saved = { run_id: "r-a", status: "SUCCEEDED", revision: 2, timing: { total_ms: 4100, processing_ms: 1100, user_wait_ms: 3000 } } as Run;
    const view = render(<TurnContent runId="r-a" events={[]} run={null} summaries={{ "r-a": saved }} busy={false} onAction={vi.fn()} />);
    expect(screen.getByText("处理 1.1 秒 · 等待 3.0 秒")).toHaveAttribute("title", "整轮 4.1 秒");
    view.rerender(<TurnContent runId="r-a" events={[event("STATUS", 1, { status: "SUCCEEDED" })]} run={null} busy={false} onAction={vi.fn()} />);
    expect(screen.getByText("耗时未记录")).toBeVisible();
  });

  it("keeps drafts separate across conversations without submitting or persisting them", async () => {
    const a = conversation("c-a", "资金查询"), b = conversation("c-b", "客户资料");
    const storage = memoryKey(app.public_id); saveLocator(storage, { runtime_session_id: identity.runtime_session_id, conversation_id: a.id });
    const fetch = vi.fn((path: string) => {
      if (path.endsWith(`/agents/${app.agent_id}`)) return respond({ id: app.agent_id, name: ui.title });
      if (path.split("?")[0].endsWith("/conversations")) return respond([a, b]);
      if (path.endsWith("/history")) return respond({ conversation: path.includes("c-b") ? b : a, messages: [], runs: [], events: [], cursor: 0 });
      return respond([]);
    }); vi.stubGlobal("fetch", fetch);
    render(<AgentWorkspace api={new ApiClient("runtime")} app={app} identity={identity} storageKey={storage} embedded={false} onLogout={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("textbox", { name: "业务诉求" })).not.toBeDisabled());
    fireEvent.change(screen.getByRole("textbox", { name: "业务诉求" }), { target: { value: "资金草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "客户资料" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "业务诉求" })).toHaveValue(""));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "业务诉求" })).not.toBeDisabled());
    fireEvent.change(screen.getByRole("textbox", { name: "业务诉求" }), { target: { value: "资料草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "资金查询" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "业务诉求" })).toHaveValue("资金草稿"));
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/messages"))).toBe(false);
    expect(JSON.stringify(sessionStorage)).not.toMatch(/资金草稿|资料草稿|runtime-test-value/);
    fireEvent.click(screen.getByRole("button", { name: "收起历史会话" }));
    expect(screen.queryByRole("navigation", { name: "历史会话" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "展开历史会话" }));
    expect(screen.getByRole("navigation", { name: "历史会话" })).toBeVisible();
  });

  it("opens compact history as an accessible modal in iframe without management navigation", async () => {
    const fetch = vi.fn((path: string) => respond(path.split("?")[0].endsWith("/conversations") ? [] : { id: app.agent_id, name: ui.title }));
    vi.stubGlobal("fetch", fetch);
    render(<AgentWorkspace api={new ApiClient("runtime")} app={app} identity={identity} storageKey="embed-chat" embedded onLogout={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "展开历史会话" }));
    const modal = await screen.findByRole("dialog", { name: "会话" });
    expect(within(modal).getByRole("textbox", { name: "搜索历史会话" })).toBeVisible();
    expect(screen.queryByText(/Console|管理平台|事件游标/)).toBeNull();
    fireEvent.click(within(modal).getByRole("button", { name: "关闭历史会话" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetch.mock.calls.every(([path]) => path.startsWith("/api/v2/runtime/"))).toBe(true);
  });

  it("copies only displayable results and never repeats a business request", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<TurnContent runId="r-a" events={[event("RESULT", 1, { component: "text", data: "not-displayable-raw", result_unavailable: true })]} run={null} busy={false} onAction={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "复制答复" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("业务操作已确认成功，但结果当前不可展示。"));
    expect(screen.queryByText("not-displayable-raw")).toBeNull();
    expect(screen.queryByRole("button", { name: /重新生成/ })).toBeNull();
  });

  it("enlarges a returned table with Escape and restores focus to its trigger", async () => {
    const user = userEvent.setup();
    render(<BusinessResultContent data={{ component: "table", data: Array.from({ length: 7 }, (_, i) => ({ name: `项目 ${i}`, amount: i })) }} />);
    const trigger = screen.getByRole("button", { name: "放大表格" });
    await user.click(trigger);
    expect(await screen.findByRole("dialog", { name: "结果明细" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("does not let the live composer consume Escape from a result dialog", async () => {
    const user = userEvent.setup();
    const saved = conversation("c-a", "流水查询");
    const result = event("RESULT", 1, { component: "table", data: Array.from({ length: 7 }, (_, i) => ({ name: `项目 ${i}`, amount: i })) });
    const storage = memoryKey(app.public_id); saveLocator(storage, { runtime_session_id: identity.runtime_session_id, conversation_id: saved.id });
    const fetch = vi.fn((path: string) => {
      if (path.split("?")[0].endsWith("/conversations")) return respond([saved]);
      if (path.endsWith("/history")) return respond({ conversation: saved, messages: [], runs: [{ run_id: "r-a", revision: 1, status: "SUCCEEDED" }], events: [result], cursor: 1 });
      return respond(path.endsWith(`/agents/${app.agent_id}`) ? { id: app.agent_id, name: ui.title } : []);
    }); vi.stubGlobal("fetch", fetch);
    render(<AgentWorkspace api={new ApiClient("runtime")} app={app} identity={identity} storageKey={storage} embedded={false} onLogout={vi.fn()} />);
    const trigger = await screen.findByRole("button", { name: "放大表格" });
    await user.click(trigger);
    expect(await screen.findByRole("dialog", { name: "结果明细" })).toBeVisible();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(fetch.mock.calls.some(([path]) => /cancel|messages$/.test(path))).toBe(false);
  });

  it("protects the old draft when switching to a local blank conversation", async () => {
    const a = conversation("c-a", "旧会话"), b = conversation("c-b", "新会话");
    const storage = memoryKey(app.public_id); saveLocator(storage, { runtime_session_id: identity.runtime_session_id, conversation_id: a.id });
    const fetch = vi.fn((path: string) => {
      if (path.split("?")[0].endsWith("/conversations")) return respond([a]);
      if (path.endsWith("/history")) return respond({ conversation: path.includes("c-b") ? b : a, messages: [], runs: [], events: [], cursor: 0 });
      return respond(path.endsWith(`/agents/${app.agent_id}`) ? { id: app.agent_id, name: ui.title } : []);
    }); vi.stubGlobal("fetch", fetch);
    render(<AgentWorkspace api={new ApiClient("runtime")} app={app} identity={identity} storageKey={storage} embedded={false} onLogout={vi.fn()} />);
    const input = screen.getByRole("textbox", { name: "业务诉求" });
    await waitFor(() => expect(input).not.toBeDisabled());
    fireEvent.change(input, { target: { value: "旧草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "开始新会话" }));
    await waitFor(() => expect(input).not.toBeDisabled());
    expect(input).toHaveValue("");
    fireEvent.change(input, { target: { value: "新草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "旧会话" }));
    await waitFor(() => expect(input).toHaveValue("旧草稿"));
    expect(fetch.mock.calls.some(([path]) => path.endsWith(`/agents/${app.agent_id}/conversations`))).toBe(false);
  });
});
