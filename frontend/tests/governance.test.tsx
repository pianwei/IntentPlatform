import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../shared/api";
import { GovernancePage, MembersPanel, LimitsPanel, AttemptsPanel } from "../console/src/GovernancePage";
import { CredentialSelector } from "../console/src/CredentialSelector";
import { AgentEditor } from "../console/src/AgentEditor";
import { blankDefinition } from "../console/src/FunctionEditor";
import type { Agent, Capability } from "../shared/types";
import type { GovernanceCatalog, WorkspaceLimits, WorkspaceMember } from "../shared/governance";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const catalog: GovernanceCatalog = { integrations: [{ ref: "trusted-a", label: "获准集成 A", version: "v1", channels: ["chat", "embed"], fixture_mode: false }, { ref: "trusted-b", label: "获准集成 B", version: "v2", channels: ["chat"], fixture_mode: false }], resolvers: [], credentials: [{ ref: "http-reader", connector_kind: "http" }, { ref: "dify-only", connector_kind: "dify" }] };
const agent: Agent = { id: "a", workspace_id: "ws", name: "本地合成助手", revision: 2, capability_ids: ["f"], suggested_questions: [], fixture_mode: false, integration_refs: ["trusted-a"], published_integration_refs: ["trusted-a"], revoked_integration_refs: [], channels: { chat: true, embed: false } };
const limits: WorkspaceLimits = { revision: 0, limits: { max_queued_runs: 100, max_routing: 4, max_execution: 8 }, ceiling: { max_queued_runs: 100, max_routing: 4, max_execution: 8 }, usage: { queued_runs: 0, routing: null, execution: 2 } };
function agentApi() { const api = new ApiClient("management"); vi.spyOn(api, "get").mockImplementation(async path => (path.endsWith("/catalogs") ? catalog : path.endsWith("/agents") ? [agent] : []) as never); return api; }

it("shows safe catalogs but never loads admin resources for a maintainer", async () => {
  const api = agentApi();
  render(<GovernancePage api={api} workspace="ws" userId="maintainer" admin={false} canManage />);
  await screen.findByText("获准集成 A"); fireEvent.click(screen.getByRole("button", { name: "空间成员 · 需管理员" }));
  expect(screen.getByText(/成员、限额和运行耗时需要/)).toBeVisible();
  expect(vi.mocked(api.get).mock.calls.every(([path]) => path.endsWith("/catalogs"))).toBe(true);
});
it("rejects runtime-token access to each governance management route before sending a request", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch); const api = new ApiClient("runtime"); api.setToken("runtime-test");
  for (const suffix of ["catalogs", "members", "limits", "runs/run-a/attempts"]) await expect(api.get(`/api/v2/management/workspaces/ws/${suffix}`)).rejects.toMatchObject({ status: 403 });
  await expect(api.put("/api/v2/management/workspaces/ws/members/user", {})).rejects.toMatchObject({ status: 403 });
  expect(fetch).not.toHaveBeenCalled();
});
it("enforces member ceiling and self restrictions, retains a stale choice, then explicitly rebases the revision", async () => {
  const own: WorkspaceMember = { user_id: "self", active: true, roles: ["admin"], revision: 1, ceiling_roles: ["admin"], server_granted: true };
  const member: WorkspaceMember = { user_id: "bob", active: false, roles: [], revision: 0, ceiling_roles: ["user", "maintainer"], server_granted: true };
  const api = new ApiClient("management"); const get = vi.spyOn(api, "get").mockResolvedValue([own, member]);
  const put = vi.spyOn(api, "put").mockRejectedValueOnce(new ApiError(409, "Member revision changed")).mockResolvedValue({ ...member, revision: 4, active: true, roles: ["maintainer"] });
  render(<MembersPanel api={api} workspace="ws" userId="self" />);
  const self = within(await screen.findByRole("form", { name: "成员 self" })); const bob = within(screen.getByRole("form", { name: "成员 bob" }));
  expect(self.getByRole("checkbox", { name: "成员有效" })).toBeDisabled(); expect(self.getByRole("button", { name: "保存成员权限" })).toBeDisabled();
  expect(bob.getByRole("checkbox", { name: "管理员（未获准）" })).toBeDisabled();
  fireEvent.click(bob.getByRole("checkbox", { name: "成员有效" })); fireEvent.click(bob.getByRole("checkbox", { name: "维护者" })); fireEvent.click(bob.getByRole("button", { name: "保存成员权限" }));
  await bob.findByText(/Member revision changed/); expect(bob.getByRole("checkbox", { name: "维护者" })).toBeChecked();
  expect(put).toHaveBeenLastCalledWith("/api/v2/management/workspaces/ws/members/bob", { revision: 0, roles: ["maintainer"], active: true });
  get.mockResolvedValue([own, { ...member, revision: 3 }]); fireEvent.click(bob.getByRole("button", { name: "读取最新成员并保留选择" }));
  await waitFor(() => expect(bob.queryByText(/Member revision changed/)).not.toBeInTheDocument()); expect(bob.getByRole("checkbox", { name: "维护者" })).toBeChecked();
  fireEvent.click(bob.getByRole("button", { name: "保存成员权限" })); await waitFor(() => expect(put).toHaveBeenLastCalledWith("/api/v2/management/workspaces/ws/members/bob", { revision: 3, roles: ["maintainer"], active: true }));
});
it("checks finite limit ceilings and preserves input on 409 and usage refresh; unknown usage differs from zero", async () => {
  const api = new ApiClient("management"); const get = vi.spyOn(api, "get").mockResolvedValue(limits); const put = vi.spyOn(api, "put").mockRejectedValue(new ApiError(409, "Capacity revision changed"));
  render(<LimitsPanel api={api} workspace="ws" />);
  const input = await screen.findByLabelText(/同时路由上限/); expect(screen.getByText("未观测")).toBeVisible(); expect(screen.getByText("0")).toBeVisible();
  fireEvent.change(input, { target: { value: "5" } }); expect(screen.getByRole("button", { name: "保存运行限额" })).toBeDisabled();
  fireEvent.change(input, { target: { value: "2" } }); fireEvent.click(screen.getByRole("button", { name: "保存运行限额" }));
  await screen.findByText(/Capacity revision changed/); expect(input).toHaveValue(2); expect(put).toHaveBeenLastCalledWith("/api/v2/management/workspaces/ws/limits", { revision: 0, limits: { ...limits.limits, max_routing: 2 } });
  get.mockResolvedValue({ ...limits, revision: 3, limits: { ...limits.limits, max_routing: 1 }, usage: { queued_runs: 3, routing: 1, execution: 2 } });
  fireEvent.click(screen.getByRole("button", { name: "刷新占用" })); await waitFor(() => expect(screen.getByRole("button", { name: "刷新占用" })).toBeEnabled()); expect(input).toHaveValue(2);
  fireEvent.change(input, { target: { value: "0" } }); expect(screen.getByRole("button", { name: "保存运行限额" })).toBeDisabled();
});
it("renders separate attempts with null timing as unobserved and a measured zero as zero", async () => {
  const api = new ApiClient("management"); const get = vi.spyOn(api, "get").mockResolvedValue([{ id: "attempt-a", run_id: "run-a", revision: 1, kind: "execution", call_id: "call-a", status: "expired", queue_ms: null, context_ms: null, model_ms: null, validation_ms: 0, connector_ms: null, total_ms: null, queued_at: null, started_at: "2026-09-22T00:00:00Z", finished_at: null, lease_until: "2026-09-22T00:01:00Z", error_stage: null }, { id: "attempt-b", run_id: "run-a", revision: 2, kind: "reconciliation", call_id: "call-a", status: "completed", queue_ms: null, context_ms: null, model_ms: null, validation_ms: null, connector_ms: 0.6003919988870621, total_ms: 456.3176609972285 }]);
  render(<AttemptsPanel api={api} workspace="ws" />); fireEvent.change(screen.getByLabelText("运行标识"), { target: { value: "run-a" } }); fireEvent.click(screen.getByRole("button", { name: "查询执行耗时" }));
  await screen.findByText("attempt-a"); expect(screen.getByText("attempt-b")).toBeVisible(); expect(screen.getByText("0 ms")).toBeVisible(); expect(screen.getAllByText("未观测").length).toBeGreaterThan(3); expect(screen.getByText("0.6 ms")).toBeVisible(); expect(screen.getByText("456.32 ms")).toBeVisible(); expect(screen.queryByText("0.6003919988870621 ms")).not.toBeInTheDocument();
  expect(get).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/runs/run-a/attempts");
});
it("offers only matching approved credential references while retaining unavailable old references", async () => {
  const api = agentApi(), change = vi.fn();
  render(<CredentialSelector api={api} workspace="ws" kind="http" value="old-ref" onChange={change} />);
  await screen.findByRole("option", { name: "http-reader" }); expect(screen.queryByRole("option", { name: "dify-only" })).not.toBeInTheDocument(); expect(screen.getByLabelText("凭据引用")).toHaveValue("old-ref");
  fireEvent.change(screen.getByLabelText("凭据引用"), { target: { value: "http-reader" } }); expect(change).toHaveBeenLastCalledWith("http-reader");
});
it("separates selected draft integrations from live revocation and preserves an unsaved draft", async () => {
  const api = agentApi(); const post = vi.spyOn(api, "post").mockResolvedValue({ ...agent, revision: 99, revoked_integration_refs: ["trusted-a"] }); const patch = vi.spyOn(api, "patch").mockRejectedValue(new ApiError(409, "Agent draft revision changed"));
  render(<AgentEditor api={api} workspace="ws" item={agent} functions={[]} onClose={vi.fn()} onSaved={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "访问控制" })); const a = await screen.findByLabelText("身份集成 trusted-a"); fireEvent.click(a); fireEvent.click(screen.getByLabelText("身份集成 trusted-b"));
  fireEvent.change(screen.getByLabelText("允许的 iframe 宿主 Origin（一行一个）"), { target: { value: "https://draft.example" } });
  fireEvent.click(screen.getByRole("button", { name: "立即撤销集成 trusted-a" })); await screen.findByText("已即时撤销");
  expect(screen.getByLabelText("身份集成 trusted-b")).toBeChecked(); expect(a).not.toBeChecked(); expect(a).toBeDisabled(); expect(screen.getByLabelText("允许的 iframe 宿主 Origin（一行一个）")).toHaveValue("https://draft.example"); expect(post).toHaveBeenLastCalledWith("/api/v2/management/workspaces/ws/agents/a/revoke-integration", { integration_ref: "trusted-a" });
  fireEvent.click(screen.getByRole("button", { name: "保存智能体草稿" })); await screen.findByText(/Agent draft revision changed/);
  expect(patch).toHaveBeenLastCalledWith("/api/v2/management/workspaces/ws/agents/a/draft", expect.objectContaining({ revision: 2, integration_refs: ["trusted-b"] })); expect(screen.getByLabelText("身份集成 trusted-b")).toBeChecked();
});
it("preserves legacy undefined integration selection and lets users remove disabled or missing selected functions", async () => {
  const api = agentApi(); const patch = vi.spyOn(api, "patch").mockResolvedValue(agent);
  const legacy = { ...agent, integration_refs: undefined, published_integration_refs: null, capability_ids: ["f", "missing"] };
  const disabled: Capability = { id: "f", workspace_id: "ws", name: "停用合成功能", description: "合成", revision: 2, status: "disabled", latest_version: 1, is_fixture: false, definition: { ...blankDefinition, name: "停用合成功能" } };
  render(<AgentEditor api={api} workspace="ws" item={legacy} functions={[disabled]} onClose={vi.fn()} onSaved={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "访问控制" })); await screen.findByText(/当前发布为旧版身份规则/); expect(screen.getByText(/当前响应未提供集成草稿选择/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "发布与嵌入" })); expect(screen.getByLabelText("选择功能 停用合成功能")).toBeChecked(); fireEvent.click(screen.getByLabelText("选择功能 停用合成功能")); expect(screen.queryByLabelText("选择功能 停用合成功能")).not.toBeInTheDocument();
  expect(screen.getByLabelText("移除不可用功能 missing")).toBeChecked(); fireEvent.click(screen.getByRole("button", { name: "保存智能体草稿" }));
  await waitFor(() => expect(patch).toHaveBeenCalled()); expect(patch.mock.calls[0][1]).not.toHaveProperty("integration_refs"); expect(patch.mock.calls[0][1]).toMatchObject({ capability_ids: ["missing"] });
});
