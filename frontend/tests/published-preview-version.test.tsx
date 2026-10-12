import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { LivePreview } from "../console/src/LivePreview";
import { ApiClient } from "../shared/api";
import { runtimeUI } from "../shared/application";
import type { Agent } from "../shared/types";
vi.mock("../agent-web/src/AgentWorkspace", () => ({ AgentWorkspace: () => <div>已发布试聊内容</div> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const agent: Agent = { id: "a", workspace_id: "s", name: "业务助手", public_id: "public", current_release_id: "r9", suggested_questions: [], fixture_mode: true };
function setup() {
  const api = new ApiClient("management");
  vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(path.endsWith("/sessions") ? { access_token: "synthetic-runtime", user_id: "u", runtime_session_id: "rs" } : path.endsWith("/logout") ? {} : { public_id: "public", agent_id: "a", release_id: "r3", runtime_ui: runtimeUI(), channels: { chat: true, embed: true }, fixture_mode: true })))));
  return api;
}
it("labels preview from the public release id instead of the first version or current draft record", async () => {
  const api = setup(); vi.spyOn(api, "get").mockResolvedValue([{ release_id: "r9", version: 9 }, { release_id: "r3", version: 3 }]);
  render(<LivePreview api={api} agent={agent} onClose={vi.fn()} />);
  expect(await screen.findByRole("dialog", { name: "业务助手 · 当前发布 v3" })).toBeVisible();
  expect(screen.queryByText(/当前发布 v9/)).toBeNull();
  expect(screen.getByText("已发布试聊内容")).toBeVisible();
});
it("keeps authorized preview usable without inventing a version when metadata cannot be read", async () => {
  const api = setup(); vi.spyOn(api, "get").mockRejectedValue(new Error("metadata unavailable"));
  render(<LivePreview api={api} agent={agent} onClose={vi.fn()} />);
  expect(await screen.findByText("发布版本号暂时无法读取。")).toBeVisible();
  expect(screen.getByText("已发布试聊内容")).toBeVisible();
  expect(screen.queryByText(/当前发布 v/)).toBeNull();
});
