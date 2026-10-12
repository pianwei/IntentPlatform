import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../shared/api";
import { ApplicationPreview } from "../shared/ApplicationView";
import { applicationRoute, appPath, embedSnippet, memoryKey, readLocator, runtimeUI, saveLocator } from "../shared/application";
import { SafeMarkdown } from "../shared/SafeMarkdown";
import { Login } from "../shared/ui";
import { AgentEditor } from "../console/src/AgentEditor";
import { AgentWorkspace } from "../agent-web/src/AgentWorkspace";
import { ResultCard } from "../agent-web/src/ResultCard";
import type { Agent, Conversation, Identity, PublicApp, RuntimeEvent } from "../shared/types";
vi.mock("../shared/environment", () => ({ DEVELOPMENT_UI: true }));

const respond = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const ui = runtimeUI({ name: "业务助手", suggested_questions: ["查询资料"] });
const app: PublicApp = { public_id: "public-a", agent_id: "agent-a", release_id: "release-current", runtime_ui: ui, channels: { chat: true, embed: true }, fixture_mode: true };
const agent: Agent = { id: "agent-a", public_id: "public-a", name: "业务助手", workspace_id: "ws-a", revision: 8, capability_ids: ["cap-a"], suggested_questions: [], runtime_ui: ui, channels: { chat: true, embed: false }, live_channels: { chat: true, embed: false }, access: { access_mode: "allowlist", allowed_users: ["alice"] }, fixture_mode: true };
const identity: Identity = { user_id: "alice", agent_id: "agent-a", access_token: "runtime-secret", runtime_session_id: "session-a", fixture_mode: true };
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollTo = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("application publication boundary", () => {
  it("saves interface changes as a revision-bound draft without modifying live access or channels", async () => {
    const fetch = vi.fn((path: string, options: RequestInit) => Promise.resolve(respond(options.method === "PATCH" ? { ...agent, revision: 9 } : [])));
    vi.stubGlobal("fetch", fetch);
    render(<AgentEditor api={new ApiClient("management")} workspace="ws-a" item={agent} functions={[]} onClose={vi.fn()} onSaved={vi.fn().mockResolvedValue(undefined)} />);
    fireEvent.click(screen.getByRole("button",{name:"对话界面"}));
    fireEvent.change(screen.getByLabelText("欢迎语"), { target: { value: "欢迎咨询业务" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(fetch.mock.calls.some(([, o]) => o.method === "PATCH")).toBe(true));
    const patch = fetch.mock.calls.find(([, o]) => o.method === "PATCH")!;
    const body = JSON.parse(patch[1].body as string);
    expect(patch[0]).toBe("/api/v2/management/workspaces/ws-a/agents/agent-a/draft");
    expect(body).toMatchObject({ revision: 8, runtime_ui: { welcome_message: "欢迎咨询业务" } });
    expect(body).not.toHaveProperty("access"); expect(body).not.toHaveProperty("allowed_users");
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/publish") || path.endsWith("/access") || path.endsWith("/channels"))).toBe(false);
  });
  it("submits an empty allowlist as an explicit live denial, independently of draft saves", async () => {
    const fetch = vi.fn((path: string, options: RequestInit) => Promise.resolve(respond(options.method === "PUT" ? { ...agent, access: { access_mode: "allowlist", allowed_users: [] } } : [])));
    vi.stubGlobal("fetch", fetch);
    render(<AgentEditor api={new ApiClient("management")} workspace="ws-a" item={agent} admin functions={[]} onClose={vi.fn()} onSaved={vi.fn().mockResolvedValue(undefined)} />);
    fireEvent.click(screen.getByRole("button", { name: "访问控制" }));
    fireEvent.click(screen.getByRole("button", {name:"清空名单"}));
    fireEvent.click(screen.getByRole("button", { name: "保存访问权限" }));
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(fetch.mock.calls.some(([, o]) => o.method === "PUT")).toBe(true));
    const update = fetch.mock.calls.find(([, o]) => o.method === "PUT")!;
    expect(update[0]).toMatch(/\/access$/); expect(JSON.parse(update[1].body as string)).toEqual({ access_mode: "allowlist", allowed_users: [] });
    expect(fetch.mock.calls.some(([, o]) => o.method === "PATCH")).toBe(false);
  });
  it("visual previews cannot execute suggestions or create conversations", () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    render(<ApplicationPreview ui={ui} />);
    expect(screen.getByRole("button", { name: "查询资料" })).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("can revoke an active published origin after the draft removed it without overwriting unsaved edits", async () => {
    const fetch = vi.fn((_path: string, _options: RequestInit) => Promise.resolve(respond([]))); vi.stubGlobal("fetch", fetch);
    render(<AgentEditor api={new ApiClient("management")} workspace="ws-a" item={{ ...agent, allowed_origins: [], published_origins: ["https://old-host.internal"], revoked_origins: [] }} functions={[]} onClose={vi.fn()} onSaved={vi.fn().mockResolvedValue(undefined)} />);
    fireEvent.click(screen.getByRole("button", { name: "访问控制" }));
    fireEvent.click(screen.getByText("已发布网站与即时撤销"));
    const draft = screen.getByLabelText("新增嵌入网站");
    fireEvent.change(draft, { target: { value: "https://next-host.internal" } });
    fireEvent.click(screen.getByRole("button", { name: "添加网站" }));
    fireEvent.click(screen.getByRole("button", { name: "立即撤销" }));
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await screen.findByText("已撤销");
    expect(screen.getByText("https://next-host.internal")).toBeVisible();
    const revoke = fetch.mock.calls.find(([path]) => path.endsWith("/revoke-origin"))!;
    expect(JSON.parse(revoke[1].body as string)).toEqual({ origin: "https://old-host.internal" });
    expect(fetch.mock.calls.some(([, options]) => options.method === "PATCH")).toBe(false);
  });
  it("renders standalone and embedded preview layouts without adding fake history", () => {
    const { rerender } = render(<ApplicationPreview ui={ui} />);
    expect(screen.getByText("实际会话将在使用后显示")).toBeVisible();
    rerender(<ApplicationPreview ui={ui} embedded />);
    expect(screen.queryByText("实际会话将在使用后显示")).toBeNull();
    expect(screen.getByRole("button", { name: "查询资料" })).toBeDisabled();
  });
  it("uses public same-origin paths without embedding credentials", () => {
    expect(applicationRoute("/chat/public-a")).toEqual({ publicId: "public-a", embedded: false });
    expect(applicationRoute("/console/")).toBeNull();
    expect(appPath("public-a", true)).toBe("/embed/public-a");
    const html = embedSnippet("public-a", "agent-a", "https://intent.internal");
    expect(html).toContain('src="https://intent.internal/embed/public-a"');
    expect(html).not.toMatch(/access_token|ticket|5174/);
  });
});

describe("runtime identity and recovery", () => {
  it("exchanges the development identity for an application-bound token before entering chat", async () => {
    const fetch = vi.fn((path: string) => Promise.resolve(respond(path.endsWith("/dev/login") ? { ...identity, access_token: "management-secret", runtime_session_id: undefined } : identity)));
    vi.stubGlobal("fetch", fetch);
    const onLogin = vi.fn(); const api = new ApiClient("runtime");
    render(<Login api={api} publicId="public-a" runtimeSessionId="session-a" onLogin={onLogin} />);
    fireEvent.click(screen.getByRole("button", { name: "使用开发身份" }));
    await waitFor(() => expect(onLogin).toHaveBeenCalledWith(identity));
    const call = (fetch.mock.calls as unknown as [string, RequestInit][]).find(([path]) => path.endsWith("/sessions"))!;
    expect((call[1].headers as Headers).get("Authorization")).toBe("Bearer management-secret");
    expect(JSON.parse(call[1].body as string)).toEqual({ runtime_session_id: "session-a" });
    await api.get("/api/v2/runtime/conversations");
    const last = (fetch.mock.calls as unknown as [string, RequestInit][]).at(-1)!;
    expect((last[1].headers as Headers).get("Authorization")).toBe("Bearer runtime-secret");
    expect(JSON.stringify(sessionStorage)).not.toContain("secret");
  });
  it("restores one server snapshot at its cursor, keeping the original UI and never resending a message", async () => {
    const key = memoryKey("public-a"); saveLocator(key, { runtime_session_id: "session-a", conversation_id: "conversation-a" });
    const conversation: Conversation = { id: "conversation-a", conversation_id: "conversation-a", agent_id: "agent-a", release_id: "release-old", status: "active", fixture_mode: true, title: "昨日查询", runtime_ui: { ...ui, title: "旧版本界面" } };
    const fetch = vi.fn((path: string) => {
      if (path.endsWith("/agents/agent-a")) return Promise.resolve(respond(agent));
      if (path.split("?")[0].endsWith("/conversations")) return Promise.resolve(respond([conversation]));
      if (path.endsWith("/history")) return Promise.resolve(respond({ conversation, messages: [{ run_id: "run-old", text: "曾经提交的查询", revision: 1, status: "SUCCEEDED" }], runs: [{ run_id: "run-old", conversation_id: "conversation-a", revision: 1, status: "SUCCEEDED", steps: [{ name: "internal-step-name", status: "SUCCEEDED", input: { secret: "internal-input" }, output: "internal-output", elapsed_ms: 8 }] }], events: [{ schema_version: "1", conversation_id: "conversation-a", run_id: "run-old", revision: 1, sequence: 17, event_type: "RESULT", payload: { title: "最终回答", component: "text", data: "余额为100元" } }], cursor: 17 }));
      if (path.includes("/events?")) return Promise.resolve(respond([]));
      return Promise.resolve(respond({ run_id: "run-old", conversation_id: "conversation-a", revision: 1, status: "SUCCEEDED" }));
    }); vi.stubGlobal("fetch", fetch);
    render(<AgentWorkspace api={new ApiClient("runtime")} app={app} storageKey={key} identity={identity} embedded={false} onLogout={vi.fn()} />);
    await screen.findByText("曾经提交的查询");
    expect(screen.getByRole("heading", { name: "旧版本界面" })).toBeVisible();
    expect(await screen.findByText("余额为100元")).toBeVisible();
    expect(screen.queryByText(/internal-step-name|internal-input|internal-output|处理记录/)).toBeNull();
    await waitFor(() => expect(fetch.mock.calls.some(([path]) => path.endsWith("/events?after=17"))).toBe(true));
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/messages"))).toBe(false);
    expect(readLocator(key)).toEqual({ runtime_session_id: "session-a", conversation_id: "conversation-a" });
    expect(sessionStorage.getItem(key)).not.toContain("runtime-secret");
  });
});

describe("controlled runtime content", () => {
  it("renders Markdown without executing HTML or javascript links and does not load images", () => {
    const { container } = render(<SafeMarkdown text={'# 返回说明\n**已完成** [恶意](javascript:alert) [详情](https://bank.internal/detail)\n<img src=x onerror=alert(1)>\n![图片](https://outside.example/pixel)'} />);
    expect(container.querySelector("img")).toBeNull(); expect(container.querySelector("script")).toBeNull();
    expect(screen.queryByRole("link", { name: "恶意" })).toBeNull();
    expect(screen.getByRole("link", { name: "详情" })).toHaveAttribute("rel", "noopener noreferrer");
  });
  it.each(["file", "detail"])("downloads only a server file ID from %s results and surfaces permission denial", async (component) => {
    const file = { id: "file-a", filename: "结果.csv", media_type: "text/csv", size: 32 };
    const download = vi.fn().mockRejectedValue(new Error("文件访问权限已撤销"));
    const event: RuntimeEvent = { schema_version: "1", conversation_id: "c", run_id: "r", revision: 1, sequence: 3, event_type: "RESULT", payload: { component, files: [file], data: { url: "https://untrusted.example/file" } } };
    render(<ResultCard event={event} run={null} onAction={vi.fn()} onDownload={download} />);
    expect(download).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "下载" }));
    await screen.findByText("文件访问权限已撤销"); expect(download).toHaveBeenCalledWith(file);
    expect(screen.queryByRole("link")).toBeNull();
  });
  it("keeps the same API boundary for binary files", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("file data", { headers: { "Content-Type": "text/plain" } })); vi.stubGlobal("fetch", fetch);
    const api = new ApiClient("runtime"); api.setToken("binary-token");
    await expect(api.blob("/api/v2/management/workspaces/ws/assets/a")).rejects.toMatchObject({ status: 403 });
    const blob = await api.blob("/api/v2/runtime/files/a"); expect(blob.size).toBe(9);
    expect(fetch.mock.calls[0][0]).toBe("/api/v2/runtime/files/a"); expect(fetch.mock.calls[0][1].credentials).toBe("omit");
  });
});
