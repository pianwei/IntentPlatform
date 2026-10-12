import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ConsoleWorkspace } from "../console/src/App";
import { AgentEditor, clearAgentDrafts } from "../console/src/AgentEditor";
import { ApiClient } from "../shared/api";
import { canOpenPublishedChat, runtimeUI } from "../shared/application";
import type { Agent } from "../shared/types";

const published: Agent = {
  id: "agent-a", public_id: "public-a", workspace_id: "ws", name: "业务问答",
  current_release_id: "release-a", revision: 1, suggested_questions: [], fixture_mode: true,
  channels: { chat: true, embed: true }, published_channels: { chat: true, embed: true },
  live_channels: { chat: true, embed: true }, routing_strategy: "three-layer-v1",
};
afterEach(() => { cleanup(); clearAgentDrafts(); vi.restoreAllMocks(); });

function fixture(items: Agent[]) {
  const api = new ApiClient("management");
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/v2/me") return { user_id: "admin", display_name: "管理员", preferences: {} } as never;
    if (path === "/api/v2/management/workspaces") return [{ id: "ws", name: "工作空间", roles: ["admin"] }] as never;
    if (path.endsWith("/agents")) return items as never;
    if (path.startsWith("/api/v2/runtime/apps/")) return {
      public_id: "public-a", agent_id: "agent-a", release_id: "release-a", runtime_ui: runtimeUI(),
      channels: { chat: canOpenPublishedChat(items[0]), embed: true }, fixture_mode: true,
    } as never;
    return [] as never;
  });
  const post = vi.spyOn(api, "post");
  return { api, post };
}

it("requires both the published channel and the immediate switch, independently of draft edits", () => {
  expect(canOpenPublishedChat(published)).toBe(true);
  const disabledDraft = { ...published, channels: { chat: false, embed: false } };
  expect(canOpenPublishedChat(disabledDraft)).toBe(true);
  expect(canOpenPublishedChat({ ...published, live_channels: { chat: false, embed: true } })).toBe(false);
  expect(canOpenPublishedChat({ ...published, published_channels: { chat: false, embed: true } })).toBe(false);
  expect(canOpenPublishedChat({ ...published, current_release_id: undefined })).toBe(false);
  expect(canOpenPublishedChat({ ...published, published_channels: undefined })).toBe(false);
});

it("does not offer a broken chat link or preview for a published agent with its chat channel closed", async () => {
  const closed = { ...published, live_channels: { chat: false, embed: false } };
  const open = { ...published, id: "agent-b", public_id: "public-b", name: "正常问答" };
  const { api, post } = fixture([closed, open]);
  render(<ConsoleWorkspace api={api} identity={{ user_id: "admin", access_token: "synthetic", fixture_mode: true }} onLogout={async () => {}} />);
  const closedCard = within((await screen.findByRole("button", { name: "业务问答" })).closest("article")!);
  expect(closedCard.getByText("聊天已关闭")).toBeVisible();
  expect(closedCard.getByRole("button", { name: "试聊已发布版本" })).toBeDisabled();
  expect(closedCard.queryByRole("link", { name: "打开应用" })).toBeNull();
  fireEvent.click(closedCard.getByRole("button", { name: "试聊已发布版本" }));
  expect(post).not.toHaveBeenCalled();
  const openCard = within(screen.getByRole("button", { name: "正常问答" }).closest("article")!);
  expect(openCard.getByRole("button", { name: "试聊已发布版本" })).toBeEnabled();
  expect(openCard.getByRole("link", { name: "打开应用" })).toHaveAttribute("href", "/chat/public-b");
});

it.each(["live", "published"] as const)("blocks editor preview and publication links when the %s chat channel is disabled", async source => {
  const closed = { ...published, [source === "live" ? "live_channels" : "published_channels"]: { chat: false, embed: true } };
  const { api } = fixture([closed]);
  const preview = vi.fn();
  render(<AgentEditor api={api} workspace="ws" item={closed} functions={[]} onClose={vi.fn()} onSaved={async () => {}} onPreview={preview} />);
  expect(screen.getByRole("button", { name: "试聊已发布版本" })).toBeDisabled();
  expect(screen.getByText(/聊天已关闭；/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "分享与嵌入" }));
  expect(screen.getByRole("button", { name: "复制链接" })).toBeDisabled();
  expect(screen.queryByRole("link", { name: "打开应用" })).toBeNull();
});

it("keeps the published chat available when only a draft channel is changed by a non-publisher", async () => {
  const { api } = fixture([published]);
  const preview = vi.fn();
  render(<AgentEditor api={api} workspace="ws" item={published} functions={[]} publishable={false} onClose={vi.fn()} onSaved={async () => {}} onPreview={preview} />);
  fireEvent.click(screen.getByRole("button", { name: "访问控制" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "公开 Chat" }));
  expect(screen.getByRole("button", { name: "试聊已发布版本" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "试聊已发布版本" }));
  expect(preview).toHaveBeenCalledWith(published);
  fireEvent.click(screen.getByRole("button", { name: "分享与嵌入" }));
  expect(screen.getByRole("link", { name: "打开应用" })).toHaveAttribute("href", "/chat/public-a");
});
