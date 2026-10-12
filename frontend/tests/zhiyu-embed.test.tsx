import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentApp } from "../agent-web/src/App";
import { runtimeUI } from "../shared/application";

const parentDescriptor = Object.getOwnPropertyDescriptor(window, "parent");
const referrerDescriptor = Object.getOwnPropertyDescriptor(document, "referrer");
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear();
  if (parentDescriptor) Object.defineProperty(window, "parent", parentDescriptor);
  if (referrerDescriptor) Object.defineProperty(document, "referrer", referrerDescriptor);
  else delete (document as unknown as { referrer?: string }).referrer;
  history.replaceState({}, "", "/");
});

it("uses the trusted embedded identity without displaying a persona selector or switching identity", async () => {
  const parent = { postMessage: vi.fn() } as unknown as Window;
  Object.defineProperty(window, "parent", { value: parent, configurable: true });
  Object.defineProperty(document, "referrer", { value: "https://host.internal/page", configurable: true });
  history.replaceState({}, "", "/embed/zhiyu");
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollTo = vi.fn(); Element.prototype.scrollIntoView = vi.fn();
  const app = { public_id: "zhiyu", agent_id: "agent-zhiyu", release_id: "r", runtime_ui: runtimeUI({ name: "智驭业务助手", suggested_questions: [] }), channels: { chat: true, embed: true }, fixture_mode: true,
    synthetic_catalog: { id: "zhiyu-v1", version: 1, default_persona: "manager_a", model_answer_policy: "general-v1", personas: [{ id: "manager_a", name: "客户经理甲", description: "甲的范围" }, { id: "manager_b", name: "客户经理乙", description: "乙的范围" }] } };
  const fetch = vi.fn((path: string, _options?: RequestInit) => {
    const data = path.endsWith("/handshakes") ? { instance_nonce: "nonce-zhiyu", expires_at: "2099-01-01T00:00:00Z" }
      : path.endsWith("/exchange") ? { access_token: "key-runtime-a", user_id: "key-user", runtime_session_id: "sid-a", agent_id: app.agent_id, demo_persona: "manager_a", fixture_mode: true }
      : path.includes("include_empty") ? [] : path.includes("/agents/") ? { id: app.agent_id, name: app.runtime_ui.title } : app;
    return Promise.resolve(new Response(JSON.stringify(data)));
  }); vi.stubGlobal("fetch", fetch);
  render(<AgentApp />);
  await waitFor(() => expect(parent.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "AGENT_READY" }), "https://host.internal"));
  const payload = { type: "AGENT_INIT", protocol_version: "1", agent_id: app.agent_id, instance_nonce: "nonce-zhiyu", ticket: "one-time", runtime_session_id: "sid-a" };
  await act(async () => window.dispatchEvent(new MessageEvent("message", { origin: "https://wrong.internal", source: parent, data: payload })));
  expect(fetch.mock.calls.some(([path]) => path.endsWith("/exchange"))).toBe(false);
  await act(async () => window.dispatchEvent(new MessageEvent("message", { origin: "https://host.internal", source: parent, data: payload })));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "业务诉求" })).toBeEnabled());
  expect(screen.queryByRole("combobox", { name: "模拟角色" })).toBeNull();
  expect(screen.queryByText(/甲的范围|客户经理甲|客户经理乙/)).toBeNull();
  expect(parent.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "AGENT_AUTHENTICATED", runtime_session_id: "sid-a", instance_nonce: "nonce-zhiyu" }), "https://host.internal");
  expect(fetch.mock.calls.some(([path]) => /anonymous-sessions|demo-persona|\/management\/|\/messages$/.test(path))).toBe(false);
  expect(JSON.stringify(localStorage)).not.toMatch(/sid-a|sid-b|key-runtime/);
  expect(JSON.stringify(sessionStorage)).not.toMatch(/key-runtime/);
});
