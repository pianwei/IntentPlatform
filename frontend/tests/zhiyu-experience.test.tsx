import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentApp } from "../agent-web/src/App";
import { AgentWorkspace } from "../agent-web/src/AgentWorkspace";
import { ResultCard } from "../agent-web/src/ResultCard";
import { BusinessPlanningPage } from "../console/src/BusinessPlanningPage";
import { ApiClient } from "../shared/api";
import { memoryKey, readAnonymous, readLocator, runtimeUI, saveAnonymous, saveLocator } from "../shared/application";
import type { Conversation, Identity, PublicApp, Run, RuntimeEvent } from "../shared/types";

const app: PublicApp = { public_id: "zhiyu", agent_id: "agent-zhiyu", release_id: "release-zhiyu", runtime_ui: runtimeUI({ name: "智驭业务助手", suggested_questions: ["帮我准备明天的客户拜访", "查询客户业绩"] }), channels: { chat: true, embed: true }, fixture_mode: true,
  synthetic_catalog: { id: "zhiyu-v1", version: 1, default_persona: "manager_a", model_answer_policy: "general-v1", personas: [
    { id: "manager_a", name: "客户经理甲", description: "查看甲的管户客户" }, { id: "manager_b", name: "客户经理乙", description: "查看乙的管户客户" }, { id: "supervisor", name: "模拟主管", description: "查看辖内业绩" },
  ] } };
const identity: Identity = { user_id: "guest-a", runtime_session_id: "session-a", access_token: "access-a", recovery_secret: "recovery-a", agent_id: app.agent_id, fixture_mode: true, demo_persona: "manager_a" };
const conversation: Conversation = { id: "c-a", conversation_id: "c-a", agent_id: app.agent_id, release_id: app.release_id, status: "active", fixture_mode: true, title: "甲的客户查询" };
const event = (payload: Record<string, unknown>, type: RuntimeEvent["event_type"] = "RESULT"): RuntimeEvent => ({ schema_version: "1", conversation_id: "c-a", run_id: "r-a", revision: 1, sequence: 1, event_type: type, payload });
const respond = (data: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status }));
beforeEach(() => {
  Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollTo = vi.fn(); Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); history.replaceState({}, "", "/"); });

function workspaceFixture(run?: Run, events: RuntimeEvent[] = [], practice = false) {
  const api = new ApiClient("runtime");
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.includes("include_empty")) return (run ? [conversation] : []) as never;
    if (path.endsWith("/history")) return { conversation: { ...conversation, practice_active: practice }, events, cursor: events.length, messages: [{ run_id: "r-a", text: "查看客户", revision: 1, status: run?.status }], runs: run ? [run] : [] } as never;
    if (path.includes("/events?")) return [] as never;
    if (path.includes("/runs/")) return run as never;
    return { id: app.agent_id, name: app.runtime_ui.title } as never;
  });
  const post = vi.spyOn(api, "post");
  if (run) saveLocator("test-zhiyu", { runtime_session_id: identity.runtime_session_id, conversation_id: conversation.id });
  return { api, get, post };
}

it.each([false, true])("shows only configured questions in chat/embed and fills the composer without business calls (embed=%s)", async embedded => {
  const { api, post } = workspaceFixture();
  render(<AgentWorkspace api={api} identity={identity} app={app} storageKey="test-zhiyu" embedded={embedded} onLogout={vi.fn()} />);
  const input = screen.getByRole("textbox", { name: "业务诉求" });
  await waitFor(() => expect(input).toBeEnabled());
  expect(screen.queryByRole("group", { name: "业务场景" })).toBeNull();
  expect(screen.queryByRole("button", { name: "生成尽调报告" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "帮我准备明天的客户拜访" }));
  expect(input).toHaveValue("帮我准备明天的客户拜访");
  expect(input).toHaveFocus();
  expect(post).not.toHaveBeenCalled();
  expect(screen.queryByText(/三层|Skill|管理平台/)).toBeNull();
});

it("keeps the chat focused on the draft without exposing simulated identity settings", async () => {
  const { api, post } = workspaceFixture();
  render(<AgentWorkspace api={api} identity={identity} app={app} storageKey="test-zhiyu" embedded={false} onLogout={vi.fn()} />);
  const input = screen.getByRole("textbox", { name: "业务诉求" });
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "未发送客户问题" } });
  expect(input).toHaveValue("未发送客户问题");
  expect(screen.queryByRole("combobox", { name: "模拟角色" })).toBeNull();
  expect(screen.queryByText(/客户经理甲|客户经理乙|模拟主管|查看甲的管户客户/)).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.querySelector(".zhiyu-context-bar")).toBeNull();
  expect(post).not.toHaveBeenCalled();
});

it("preserves restored write confirmation without switching identity or cancelling business", async () => {
  const run = { run_id: "r-a", conversation_id: "c-a", revision: 1, status: "WAITING_CONFIRMATION" } as Run;
  const { api, post } = workspaceFixture(run, [event({ name: "创建营销任务", arguments: {} }, "CONFIRMATION")]);
  render(<AgentWorkspace api={api} identity={identity} app={app} storageKey="test-zhiyu" embedded={false} onLogout={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "确认执行" })).toBeEnabled();
  expect(screen.queryByRole("combobox", { name: "模拟角色" })).toBeNull();
  expect(post).not.toHaveBeenCalled();
});

it("ends restored practice with one normal message, never a separate business endpoint", async () => {
  const run = { run_id: "r-a", conversation_id: "c-a", revision: 1, status: "SUCCEEDED" } as Run;
  const { api, post } = workspaceFixture(run, [event({ component: "text", data: "欢迎来访", answer_source: "practice", practice_active: true })], true);
  post.mockResolvedValue({ ...run, run_id: "r-b", status: "ROUTING" });
  render(<AgentWorkspace api={api} identity={identity} app={app} storageKey="test-zhiyu" embedded={false} onLogout={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "结束陪练并查看反馈" }));
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  expect(post).toHaveBeenCalledWith("/api/v2/runtime/conversations/c-a/messages", expect.objectContaining({ text: "结束陪练，并总结本次陪练反馈。", client_message_id: expect.any(String) }));
});

it("uses explicit server practice state to hide the end action after practice ended", async () => {
  const run = { run_id: "r-a", conversation_id: "c-a", revision: 1, status: "SUCCEEDED" } as Run;
  const { api, post } = workspaceFixture(run, [event({ component: "text", data: "本次陪练反馈", answer_source: "practice", practice_active: false })], false);
  render(<AgentWorkspace api={api} identity={identity} app={app} storageKey="test-zhiyu" embedded={false} onLogout={vi.fn()} />);
  await screen.findByText("本次陪练反馈");
  expect(screen.queryByRole("button", { name: "结束陪练并查看反馈" })).toBeNull();
  expect(post).not.toHaveBeenCalled();
});

it.each([["model", "模型回答"], ["practice", "模拟陪练"], ["mixed", "业务结果与解释"], ["business", "业务结果"]])("labels a %s answer without exposing planner diagnostics", (source, label) => {
  render(<ResultCard event={event({ component: "text", data: "可展示答复", answer_source: source })} run={null} onAction={vi.fn()} />);
  expect(screen.getByText(label, { selector: ".answer-source" })).toBeVisible();
  expect(screen.queryByText(/r-a|zhiyu-v1/)).toBeNull();
});

it("restores an existing server-bound persona and history without exposing a switch or submitting business", async () => {
  history.replaceState({}, "", "/chat/zhiyu");
  saveAnonymous("zhiyu", "session-b", "recovery-b", "manager_b");
  const key = memoryKey("zhiyu");
  saveLocator(`${key}.persona.manager_b`, { runtime_session_id: "session-b", conversation_id: "c-b" });
  const restored = { ...identity, user_id: "guest-b", runtime_session_id: "session-b", recovery_secret: "recovery-b", access_token: "access-b", demo_persona: "manager_b" };
  const answer = { ...event({ component: "text", data: "经理乙的原有查询结果" }), conversation_id: "c-b" };
  const fetch = vi.fn((path: string, _options?: RequestInit) => {
    if (path.endsWith("/anonymous-sessions")) return respond(restored);
    if (path.endsWith("/history")) return respond({ conversation: { ...conversation, id: "c-b", conversation_id: "c-b" }, events: [answer], cursor: 1, messages: [{ run_id: "r-a", text: "查询我的客户", revision: 1, status: "SUCCEEDED" }], runs: [{ run_id: "r-a", conversation_id: "c-b", revision: 1, status: "SUCCEEDED" }] });
    if (path.includes("include_empty")) return respond([{ ...conversation, id: "c-b", conversation_id: "c-b" }]);
    if (path.includes("/agents/")) return respond({ id: app.agent_id, name: app.runtime_ui.title });
    if (path.includes("/events?")) return respond([]);
    return respond(app);
  }); vi.stubGlobal("fetch", fetch);
  const first = render(<AgentApp />);
  expect(await screen.findByText("经理乙的原有查询结果")).toBeVisible();
  expect(screen.queryByRole("combobox", { name: "模拟角色" })).toBeNull();
  const recovery = fetch.mock.calls.find(([path]) => path.endsWith("/anonymous-sessions"))!;
  expect(JSON.parse(String(recovery[1]?.body))).toEqual({ runtime_session_id: "session-b", recovery_secret: "recovery-b", demo_persona: "manager_b" });
  expect(readAnonymous("zhiyu").demo_persona).toBe("manager_b");
  expect(readLocator(`${key}.persona.manager_b`).conversation_id).toBe("c-b");
  first.unmount(); render(<AgentApp />);
  expect(await screen.findByText("经理乙的原有查询结果")).toBeVisible();
  expect(fetch.mock.calls.some(([path]) => /demo-persona|\/messages$|\/management\//.test(path))).toBe(false);
  expect(JSON.stringify(localStorage)).not.toMatch(/access-b/);
});

it("connects a fresh chat with no user-selected persona and keeps new conversation local until send", async () => {
  history.replaceState({}, "", "/chat/zhiyu");
  const fetch = vi.fn((path: string, _options?: RequestInit) => {
    if (path.endsWith("/anonymous-sessions")) return respond(identity);
    if (path.includes("include_empty")) return respond([]);
    if (path.includes("/agents/")) return respond({ id: app.agent_id, name: app.runtime_ui.title });
    return respond(app);
  }); vi.stubGlobal("fetch", fetch);
  render(<AgentApp />);
  await waitFor(() => expect(screen.getByRole("textbox", { name: "业务诉求" })).toBeEnabled());
  expect(screen.queryByRole("combobox", { name: "模拟角色" })).toBeNull();
  const create = fetch.mock.calls.find(([path]) => path.endsWith("/anonymous-sessions"))!;
  expect(JSON.parse(String(create[1]?.body))).toEqual({});
  fireEvent.click(screen.getByRole("button", { name: /^新会话$/ }));
  expect(fetch.mock.calls.filter(([path]) => path.endsWith("/anonymous-sessions"))).toHaveLength(1);
  expect(fetch.mock.calls.some(([path, options]) => options?.method === "POST" && /demo-persona|\/conversations$|\/messages$/.test(path))).toBe(false);
});

it("installs the reviewed catalog in the selected workspace rather than recreating the legacy demo", async () => {
  const api = new ApiClient("management"); vi.spyOn(api, "get").mockResolvedValue([]);
  const post = vi.spyOn(api, "post").mockResolvedValue({ chat_path: "/chat/zhiyu" });
  render(<BusinessPlanningPage api={api} workspace="ws-a" writable publishable admin development onSeeded={vi.fn().mockResolvedValue(undefined)} />);
  fireEvent.click(screen.getByRole("button", { name: "安装或核对智驭场景" }));
  await screen.findByText(/智驭业务助手已就绪/);
  expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws-a/synthetic/zhiyu/install", {});
});
