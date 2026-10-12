import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AgentWorkspace } from "../agent-web/src/AgentWorkspace";
import { ResultCard } from "../agent-web/src/ResultCard";
import { readChatTextSize, saveChatTextSize } from "../agent-web/src/textPreferences";
import { TextSizeControl, textScale } from "../shared/displayPreferences";
import { BusinessResultContent } from "../shared/BusinessResult";
import { ApiClient } from "../shared/api";
import { runtimeUI } from "../shared/application";
import type { PublicApp, RuntimeEvent } from "../shared/types";

const ui = runtimeUI({ name: "业务助手", suggested_questions: [] });
const app = (): PublicApp => ({ public_id: crypto.randomUUID(), agent_id: "agent-a", release_id: "release-a", runtime_ui: ui, channels: { chat: true, embed: true }, fixture_mode: true });
const identity = { user_id: "visitor-a", agent_id: "agent-a", access_token: "runtime-test-only", runtime_session_id: "session-a", fixture_mode: true };
const conversation = { id: "created", conversation_id: "created", agent_id: "agent-a", release_id: "release-a", status: "active", fixture_mode: true };
const respond = (data: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(data), { status }));
const event = (payload: Record<string, unknown>): RuntimeEvent => ({ schema_version: "1", conversation_id: "c", run_id: "r", revision: 1, sequence: 1, event_type: "RESULT", payload });
function mount(value = app(), embedded = false) {
  return render(<AgentWorkspace api={new ApiClient("runtime")} identity={identity} app={value} storageKey={`locator-${value.public_id}`} embedded={embedded} onLogout={vi.fn()} />);
}
beforeEach(() => {
  Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollTo = vi.fn(); Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); });

describe("local new chat and first-message acceptance", () => {
  it("does not create server rows for repeated empty starts and requests nonempty history", async () => {
    const fetch = vi.fn((path: string) => respond(path.includes("/conversations?") ? [] : { id: "agent-a", name: ui.title }));
    vi.stubGlobal("fetch", fetch); mount();
    const input = screen.getByRole("textbox", { name: "业务诉求" });
    await waitFor(() => expect(input).not.toBeDisabled());
    fireEvent.change(input, { target: { value: "尚未发送" } });
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "新会话" }));
    expect(input).toHaveValue("");
    expect(fetch.mock.calls.some(([path]) => path === "/api/v2/runtime/conversations?include_empty=false")).toBe(true);
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/messages") || /agents\/[^/]+\/conversations$/.test(path))).toBe(false);
    expect(JSON.stringify(sessionStorage)).not.toContain("尚未发送");
  });

  it("creates once on first send, keeps one client message id and never resends during recovery", async () => {
    let finish!: (response: Response) => void;
    const fetch = vi.fn((path: string, init?: RequestInit) => {
      if (init?.method === "POST" && path.endsWith("/conversations")) return new Promise<Response>(resolve => { finish = resolve; });
      if (path.endsWith("/messages")) return respond({ run_id: "run-a", conversation_id: "created", revision: 1, status: "SUCCEEDED" });
      if (path.includes("/conversations?") || path.includes("/events?")) return respond([]);
      if (path.endsWith("/run-a")) return respond({ run_id: "run-a", revision: 1, status: "SUCCEEDED" });
      return respond({ id: "agent-a", name: ui.title });
    }); vi.stubGlobal("fetch", fetch); mount();
    const input = screen.getByRole("textbox", { name: "业务诉求" });
    await waitFor(() => expect(input).not.toBeDisabled());
    fireEvent.change(input, { target: { value: "查客户账户" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(finish).toBeDefined());
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "新会话" })).toBeDisabled();
    finish(new Response(JSON.stringify(conversation)));
    await screen.findByText("查客户账户");
    await waitFor(() => expect(input).not.toBeDisabled());
    const calls = fetch.mock.calls.filter(([path]) => path.endsWith("/messages"));
    expect(calls).toHaveLength(1);
    expect(JSON.parse(calls[0][1]!.body as string)).toMatchObject({ text: "查客户账户", client_message_id: expect.any(String) });
    expect(fetch.mock.calls.filter(([path, init]) => init?.method === "POST" && path.endsWith("/conversations"))).toHaveLength(1);
  });

  it("restores the unsent input when server conversation creation fails", async () => {
    const fetch = vi.fn((path: string, init?: RequestInit) => init?.method === "POST" ? respond({ detail: "会话暂不可用" }, 503) : respond(path.includes("/conversations?") ? [] : { id: "agent-a", name: ui.title }));
    vi.stubGlobal("fetch", fetch); mount();
    const input = screen.getByRole("textbox", { name: "业务诉求" });
    await waitFor(() => expect(input).not.toBeDisabled());
    fireEvent.change(input, { target: { value: "保留未发送问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("会话暂不可用");
    await waitFor(() => expect(input).toHaveValue("保留未发送问题"));
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/messages"))).toBe(false);
  });
});

describe("scoped readable text preferences", () => {
  it("offers exactly the approved three scales", () => {
    const changed = vi.fn(); render(<TextSizeControl value="standard" onChange={changed} />);
    expect(textScale).toEqual({ standard: 1, large: 1.125, extra_large: 1.25 });
    fireEvent.change(screen.getByRole("combobox", { name: "字号" }), { target: { value: "extra_large" } });
    expect(changed).toHaveBeenCalledWith("extra_large");
  });

  it("persists only the display value per application and falls back to memory without storage", () => {
    const first = crypto.randomUUID(), second = crypto.randomUUID();
    expect(readChatTextSize(first)).toBe("standard"); saveChatTextSize(first, "large");
    expect(localStorage.getItem(`intent.chat.text-size.${first}`)).toBe("large");
    expect(readChatTextSize(second)).toBe("standard");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("disabled"); });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("disabled"); });
    saveChatTextSize(second, "extra_large");
    expect(readChatTextSize(second)).toBe("extra_large");
    expect(readChatTextSize(crypto.randomUUID())).toBe("standard");
  });

  it("keeps the runtime and its history dialog independent from the host page font scale", async () => {
    vi.stubGlobal("fetch", vi.fn((path: string) => respond(path.includes("/conversations?") ? [] : { id: "agent-a", name: ui.title })));
    const before = document.documentElement.style.getPropertyValue("--text-scale");
    document.documentElement.style.setProperty("--text-scale", "1.125");
    const view = mount(app(), true);
    try {
      fireEvent.change(screen.getByRole("combobox", { name: "字号" }), { target: { value: "extra_large" } });
      expect(view.container.querySelector(".application-runtime")).toHaveStyle({ "--text-scale": "1.25" });
      expect(document.documentElement.style.getPropertyValue("--text-scale")).toBe("1.125");
      fireEvent.click(screen.getByRole("button", { name: "展开历史会话" }));
      expect(await screen.findByRole("dialog", { name: "会话" })).toHaveStyle({ "--text-scale": "1.25" });
    } finally { document.documentElement.style.setProperty("--text-scale", before); }
  });

  it("carries the current result host scale into the enlarged table portal", async () => {
    const view = render(<BusinessResultContent data={{ component: "table", data: Array.from({ length: 6 }, (_, i) => ({ name: `项目${i}`, amount: i })) }} />);
    (view.container.querySelector(".business-table") as HTMLElement).style.setProperty("--text-scale", "1.25");
    fireEvent.click(screen.getByRole("button", { name: "放大表格" }));
    expect(await screen.findByRole("dialog", { name: "结果明细" })).toHaveStyle({ "--text-scale": "1.25" });
  });
});

describe("conclusion-first approved results", () => {
  it("shows supplementary results alongside conclusion and downloads without a generic disclosure", () => {
    render(<ResultCard event={event({ component: "text", data: "账户共有 100 元。", components: [{ title: "账户余额", component: "detail", data: { balance: 100 } }], files: [{ id: "file", filename: "明细.csv", size: 12, media_type: "text/csv" }] })} run={null} onAction={vi.fn()} onDownload={vi.fn()} />);
    expect(screen.getByText("账户共有 100 元。")).toBeVisible();
    expect(screen.getByRole("button", { name: "下载" })).toBeVisible();
    expect(screen.getByText("账户余额")).toBeVisible();
    expect(screen.queryByText(/查看业务明细/)).toBeNull();
  });

  it("never collapses the only business result when no summary was returned", () => {
    render(<ResultCard event={event({ component: "detail", data: { balance: 100 }, components: [{ title: "贷款明细", component: "detail", data: { outstanding: 200 } }] })} run={null} onAction={vi.fn()} />);
    expect(screen.getByText("100")).toBeVisible(); expect(screen.getByText("贷款明细")).toBeVisible();
    expect(screen.queryByText(/查看业务明细/)).toBeNull();
  });
});
