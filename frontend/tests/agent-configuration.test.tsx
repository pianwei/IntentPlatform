import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../shared/api";
import { runtimeUI } from "../shared/application";
import { AgentEditor, clearAgentDrafts } from "../console/src/AgentEditor";
import { validatedOrigin } from "../console/src/AgentConfigurationPanels";
import { blankDefinition } from "../console/src/FunctionEditor";
import type { Agent, Capability, Release } from "../shared/types";

const capability: Capability = { id: "fn", workspace_id: "ws", name: "客户查询", description: "查询客户", revision: 1, status: "published", latest_version: 1, is_fixture: true, definition: { ...blankDefinition, name: "客户查询" } };
const agent: Agent = { id: "agent", workspace_id: "ws", name: "业务助手", revision: 3, current_release_id: "release-2", public_id: "public", routing_strategy: "three-layer-v1", capability_ids: ["fn"], fixture_mode: true, suggested_questions: ["查询客户"], runtime_ui: runtimeUI({ name: "业务助手", suggested_questions: ["查询客户"] }), channels: { chat: true, embed: true }, published_channels: { chat: true, embed: true }, live_channels: { chat: true, embed: true }, access: { access_mode: "workspace", allowed_users: [] }, allowed_origins: ["https://portal.internal"] };
const releases: Release[] = [{ release_id: "release-2", version: 2, capabilities: [{ capability_id: "fn", version: 2 }], fixture_mode: true }, { release_id: "release-1", version: 1, capabilities: [{ capability_id: "fn", version: 1 }], fixture_mode: true }];
afterEach(() => { cleanup(); clearAgentDrafts(); vi.restoreAllMocks(); });
function fixture(item = agent, options: { publishable?: boolean; initialTab?: string; onSaved?: () => Promise<void> } = {}) {
  const api = new ApiClient("management");
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.endsWith("/releases")) return releases as never;
    if (path.endsWith("/agents")) return [item] as never;
    if (path.endsWith("/versions")) return [{ id: "fv1", function_id: "fn", version: 1, definition: { ...capability.definition, name: "历史客户查询" } }, { id: "fv2", function_id: "fn", version: 2, definition: capability.definition }] as never;
    if (path.includes("/members/page?")) return { items: [], total: 0 } as never;
    return [] as never;
  });
  const put = vi.spyOn(api, "put").mockResolvedValue(item);
  const patch = vi.spyOn(api, "patch").mockResolvedValue({ ...item, revision: 4 });
  const post = vi.spyOn(api, "post").mockResolvedValue(item);
  render(<AgentEditor api={api} workspace="ws" item={item} functions={[capability]} admin onClose={vi.fn()} onSaved={async () => {}} {...options} />);
  return { api, put, patch, post };
}

it("has three configuration entries and preserves a legacy strategy while hiding technical controls", async () => {
  const { patch } = fixture({ ...agent, routing_strategy: "legacy", planning_enabled: false });
  expect(within(screen.getByRole("navigation", { name: "智能体配置页" })).getAllByRole("button").map(b => b.textContent)).toEqual(["业务能力", "对话界面", "访问控制"]);
  expect(screen.queryByText("运行策略")).toBeNull();
  expect(screen.getByText(/系统保护，不支持单独配置/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("智能体名称"), { target: { value: "修改名称" } });
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ routing_strategy: "legacy", planning_enabled: false })));
});

it("edits, reorders and removes recommendation rows, with a matching draft preview and no publication", async () => {
  const { patch, post } = fixture();
  fireEvent.click(screen.getByRole("button", { name: "对话界面" }));
  fireEvent.click(screen.getByRole("button", { name: "添加推荐问题" }));
  fireEvent.change(screen.getByLabelText("推荐问题 2"), { target: { value: "生成拜访准备" } });
  fireEvent.click(screen.getByRole("button", { name: "上移推荐问题 2" }));
  expect(screen.getByLabelText("推荐问题 1")).toHaveValue("生成拜访准备");
  expect(within(screen.getByLabelText("草稿视觉预览")).getByRole("button", { name: "生成拜访准备" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "删除推荐问题 2" }));
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ runtime_ui: expect.objectContaining({ suggested_questions: ["生成拜访准备"] }) })));
  expect(post).not.toHaveBeenCalled();
});

it("cancels a channel shutdown without changing live access or its draft", () => {
  const { put, patch } = fixture();
  fireEvent.click(screen.getByRole("button", { name: "访问控制" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "公开 Chat" }));
  expect(screen.getByRole("dialog", { name: "关闭公开聊天" })).toBeVisible();
  expect(put).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.getByRole("checkbox", { name: "公开 Chat" })).toBeChecked();
  expect(screen.getByRole("button", { name: "试聊已发布版本" })).toBeEnabled();
  expect(patch).not.toHaveBeenCalled();
});

it("confirms an immediate shutdown before saving the disabled draft, never publishing implicitly", async () => {
  const { put, patch, post } = fixture();
  fireEvent.click(screen.getByRole("button", { name: "访问控制" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "公开 Chat" }));
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "公开 Chat" })).not.toBeChecked());
  expect(put).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/agents/agent/channels", { chat: false, embed: true });
  expect(screen.getByRole("button", { name: "试聊已发布版本" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ channels: { chat: false, embed: true } })));
  expect(post).not.toHaveBeenCalled();
});

it("keeps a rejected immediate shutdown open and preserves the original checked channel", async () => {
  const { put } = fixture(); put.mockRejectedValue(new Error("渠道修改失败"));
  fireEvent.click(screen.getByRole("button", { name: "访问控制" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "公开 Chat" }));
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  const dialog = screen.getByRole("dialog", { name: "关闭公开聊天" });
  await waitFor(() => expect(within(dialog).getByText("渠道修改失败")).toBeVisible());
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(screen.getByRole("checkbox", { name: "公开 Chat" })).toBeChecked();
});

it("does not restore an emergency stopped channel when publishing a saved draft", async () => {
  const stopped = { ...agent, live_channels: { chat: false, embed: true } };
  const { put, post } = fixture(stopped);
  fireEvent.click(screen.getByRole("button", { name: "发布" }));
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/agents/agent/publish", { revision: 3 }));
  expect(put).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "试聊已发布版本" })).toBeDisabled();
});

it("shows real version function names before rollback, uses only a release pointer, and preserves draft access", async () => {
  const { post, patch, put } = fixture();
  await waitFor(() => expect(screen.getByRole("button", { name: "版本与变更" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "版本与变更" }));
  fireEvent.click(await screen.findByRole("button", { name: /v1/ }));
  expect(await screen.findByText("历史客户查询")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "用于新会话" }));
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认回退，用于新会话" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/agents/agent/rollback", { release_id: "release-1" }));
  expect(patch).not.toHaveBeenCalled(); expect(put).not.toHaveBeenCalled();
});

it("maps an old publication deep link to share while keeping only the three configuration tabs", async () => {
  fixture(agent, { initialTab: "publish" });
  expect(await screen.findByRole("dialog", { name: "分享与嵌入" })).toBeVisible();
  expect(screen.getByLabelText("iframe 代码")).not.toHaveValue(expect.stringMatching(/secret|ticket|api_key/));
  fireEvent.click(screen.getByRole("button", { name: "配置访问方式" }));
  expect(screen.getByRole("checkbox", { name: "公开 Chat" })).toBeVisible();
});

it("keeps a successful publication successful when list refresh fails, so confirmation cannot resubmit it", async () => {
  const { api, post } = fixture(agent, { onSaved: async () => { throw new Error("列表刷新失败"); } });
  const originalGet = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(async path => { if (path.endsWith("/agents")) throw new Error("列表刷新失败"); return originalGet(path) as never; });
  post.mockResolvedValue(releases[0]);
  fireEvent.click(screen.getByRole("button", { name: "发布" }));
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "发布智能体" })).toBeNull());
  expect(post).toHaveBeenCalledOnce();
  expect(screen.getByText(/不必重复发布/)).toBeVisible();
});

it("does not substitute current draft names or allow rollback when its immutable function version is missing", async () => {
  const { api, post } = fixture();
  const originalGet = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(async path => path.endsWith("/versions") ? [] as never : originalGet(path) as never);
  fireEvent.click(screen.getByRole("button", { name: "版本与变更" }));
  fireEvent.click(await screen.findByRole("button", { name: /v1/ }));
  const dialog = screen.getByRole("dialog", { name: "版本与变更" });
  expect(await within(dialog).findByText(/不能用草稿替代/)).toBeVisible();
  expect(within(dialog).queryByText("客户查询")).toBeNull();
  expect(within(dialog).getByRole("button", { name: "用于新会话" })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

it("rejects website paths and duplicates locally, and persists a normalized origin only on draft save", async () => {
  const { patch, put, post } = fixture();
  fireEvent.click(screen.getByRole("button", { name: "访问控制" }));
  const input = screen.getByLabelText("新增嵌入网站");
  fireEvent.change(input, { target: { value: "https://portal.internal/path" } });
  fireEvent.click(screen.getByRole("button", { name: "添加网站" }));
  expect(screen.getByRole("alert")).toHaveTextContent("不包含页面路径");
  fireEvent.change(input, { target: { value: "https://new.internal/" } });
  fireEvent.click(screen.getByRole("button", { name: "添加网站" }));
  expect(screen.getByText("https://new.internal")).toBeVisible();
  expect(put).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ allowed_origins: ["https://portal.internal", "https://new.internal"] })));
});

it.each(["javascript:alert(1)", "https://user:password@host.internal", "https://host.internal/page", "https://host.internal?ticket=x", "https://host.internal#secret", "*"])("does not accept %s as an iframe host", value => {
  expect(() => validatedOrigin(value)).toThrow();
});
