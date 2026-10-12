import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CapabilityPicker } from "../console/src/CapabilityPicker";
import { AgentCompilationPreview } from "../console/src/AgentCompilationPreview";
import { blankDefinition } from "../console/src/FunctionEditor";
import { ApiClient } from "../shared/api";
import type { AgentCompilation, Capability, PublishedFunction } from "../shared/types";

afterEach(cleanup);
const balance: Capability = { id: "balance", workspace_id: "ws", name: "余额查询", description: "查询已获准账户的余额", revision: 2, status: "published", latest_version: 3, is_fixture: true,
  definition: { ...blankDefinition, name: "余额查询", object_type: "Account", action: "READ_BALANCE", description: "查询已获准账户的余额", input_schema: { properties: { account_id: { type: "string", title: "账户" } } }, connector: { ...blankDefinition.connector, workflow_id: "account.balance" } } as Capability["definition"] };
const accounts: Capability = { ...balance, id: "accounts", name: "账户列表", description: "查看客户名下账户", latest_version: 2,
  definition: { ...balance.definition, name: "账户列表", action: "LIST_ACCOUNTS", description: "查看客户名下账户", connector: { ...blankDefinition.connector, workflow_id: "customer.accounts" } } as Capability["definition"] };
const overview: Capability = { ...balance, id: "overview", name: "客户资金概览", description: "关联账户与余额形成资金概览", latest_version: 1,
  definition: { ...balance.definition, name: "客户资金概览", execution_kind: "composite", composite: { skill_version_id: "sv-pinned", dependencies: [{ function_id: "accounts", version: 2 }, { function_id: "balance", version: 3 }], final_output_mapping: {} } } };
const unpublished: Capability = { ...balance, id: "draft", name: "未发布功能", status: "draft", latest_version: null };
const disabled: Capability = { ...balance, id: "disabled", name: "已停用功能", status: "disabled" };
const projection: AgentCompilation = { valid: true, issues: [], projection: { version: "agent-projection-v1", entities: [
  { id: "Account", label: "账户", function_ids: ["accounts", "balance", "overview"] },
  { id: "Product", label: "产品", function_ids: ["product"] },
], actions: [
  { key: "READ_BALANCE", name: "查询余额", effect: "READ_ONLY", function_ids: ["balance"] },
  { key: "LIST_ACCOUNTS", name: "查询账户", effect: "READ_ONLY", function_ids: ["accounts"] },
  { key: "READ_PRODUCT", name: "查询产品", effect: "READ_ONLY", function_ids: ["product"] },
], skills: [{ skill_id: "skill-a", version_id: "sv-pinned", name: "资金概览规则", function_id: "overview" }], explicit_function_ids: ["overview"], dependency_function_ids: ["accounts", "balance"] } };
const published = (f: Capability): PublishedFunction => ({ id: `version-${f.id}`, function_id: f.id, version: f.latest_version!, definition: f.definition });

it("stages choices and applies them only on confirmation, while cancel and Escape leave the editor unchanged", () => {
  const change = vi.fn();
  render(<CapabilityPicker functions={[balance, accounts, unpublished]} selected={["accounts"]} onChange={change}/>);
  fireEvent.click(screen.getByRole("button", { name: "添加功能" }));
  const dialog = screen.getByRole("dialog", { name: "添加业务能力" });
  expect(within(dialog).queryByRole("checkbox", { name: "选择功能 未发布功能" })).toBeNull();
  fireEvent.click(within(dialog).getByRole("checkbox", { name: "选择功能 余额查询" }));
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "添加功能" }));
  expect(screen.getByRole("checkbox", { name: "选择功能 余额查询" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "选择功能 余额查询" }));
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull(); expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "添加功能" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "选择功能 余额查询" }));
  fireEvent.click(screen.getByRole("button", { name: "确认添加（2）" }));
  expect(change).toHaveBeenCalledOnce(); expect(change).toHaveBeenCalledWith(["accounts", "balance"]);
});

it("supports searchable bulk selection by category and keeps disabled existing choices visible and removable", () => {
  const change = vi.fn();
  render(<CapabilityPicker functions={[balance, accounts, overview, disabled, unpublished]} selected={["disabled"]} onChange={change}/>);
  expect(screen.getByText("已停用，请移除后发布", { exact: false })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "添加功能" }));
  fireEvent.click(screen.getByRole("button", { name: /^组合功能$/ }));
  expect(screen.queryByRole("checkbox", { name: "选择功能 余额查询" })).toBeNull();
  fireEvent.change(screen.getByLabelText("查找可用功能"), { target: { value: "资金" } });
  fireEvent.click(screen.getByRole("button", { name: "选择当前结果" }));
  fireEvent.click(screen.getByRole("button", { name: "从待选中移除 已停用功能" }));
  expect(screen.queryByRole("checkbox", { name: "选择功能 已停用功能" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "确认添加（1）" }));
  expect(change).toHaveBeenCalledWith(["overview"]);
});

it("reads immutable published definitions instead of exposing an edited draft as executable configuration", async () => {
  const api = new ApiClient("management");
  vi.spyOn(api, "get").mockResolvedValue([published(balance)]);
  const draft = { ...balance, definition: { ...balance.definition, name: "未发布的组合改动", execution_kind: "composite" as const, description: "未发布说明" } };
  render(<CapabilityPicker api={api} functions={[draft]} selected={[]} onChange={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button", { name: "添加功能" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "确认添加" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /^直接调用$/ }));
  expect(screen.getByRole("checkbox", { name: "选择功能 余额查询" })).toBeEnabled();
  expect(screen.queryByText("未发布说明")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看说明与依赖" }));
  expect(screen.getByText("账户", { exact: true })).toBeVisible();
});

it("does not permit newly selected unavailable published versions and never substitutes a draft after a read failure", async () => {
  const api = new ApiClient("management");
  vi.spyOn(api, "get").mockRejectedValue(new Error("版本读取失败"));
  render(<CapabilityPicker api={api} functions={[balance]} selected={[]} onChange={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button", { name: "添加功能" }));
  expect(await screen.findByText("版本读取失败")).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "选择功能 余额查询" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "查看说明与依赖" })).toBeNull();
});

it("ignores late version reads from a closed selection modal and retries failed reads when reopened", async () => {
  const api = new ApiClient("management"); let finish!: (v: PublishedFunction[]) => void;
  const pending = new Promise<PublishedFunction[]>(resolve => { finish = resolve; });
  const get = vi.spyOn(api, "get").mockReturnValueOnce(pending).mockResolvedValue([published(balance)]);
  render(<CapabilityPicker api={api} functions={[balance]} selected={[]} onChange={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button", { name: "添加功能" }));
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  fireEvent.click(screen.getByRole("button", { name: "添加功能" }));
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "选择功能 余额查询" })).toBeEnabled());
  await act(async () => { finish([]); await pending; });
  expect(screen.queryByText(/已发布版本未能读取/)).toBeNull(); expect(get).toHaveBeenCalledTimes(2);
});

it("explains the three layers before compilation and groups actual actions under their related business object", async () => {
  const api = new ApiClient("management"), post = vi.spyOn(api, "post").mockResolvedValue(projection);
  render(<AgentCompilationPreview api={api} path="/api/v2/management/workspaces/ws/agents/a" revision={4} dirty={false} functions={[accounts, balance, overview]}/>);
  expect(screen.getByText("理解客户、产品等业务对象")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "检查并预览三层结构" }));
  const relation = await screen.findByRole("region", { name: "账户的能力关系" });
  expect(within(relation).getByText("查询余额")).toBeVisible(); expect(within(relation).queryByText("查询产品")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^产品/ }));
  expect(within(screen.getByRole("region", { name: "产品的能力关系" })).getByText("查询产品")).toBeVisible();
  expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/agents/a/compile-preview", { revision: 4 });
  expect(screen.queryByText("sv-pinned")).toBeNull();
});

it("loads the pinned workflow rule, shows real dependencies and parameter sources, and uses pinned function versions", async () => {
  const api = new ApiClient("management");
  vi.spyOn(api, "post").mockResolvedValue(projection);
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.endsWith("/business-skills/skill-a/versions")) return [
      { id: "sv-new", skill_id: "skill-a", version: 8, definition: { name: "错误新规则", description: "不应展示", steps: [] } },
      { id: "sv-pinned", skill_id: "skill-a", version: 2, definition: { name: "固定资金规则", description: "先查询账户，再查询余额", steps: [
        { step_id: "accounts", capability_key: "customer.accounts", arguments: { customer_id: { $input: "customer_id" } }, depends_on: [] },
        { step_id: "balance", capability_key: "account.balance", arguments: { account_id: { $step: "accounts", path: "data.account_id" } }, depends_on: ["accounts"], foreach: { step: "accounts" } },
      ] } },
    ] as never;
    const f = [balance, accounts, overview].find(item => path.endsWith(`/functions/${item.id}/versions`));
    return f ? [published(f)] as never : [] as never;
  });
  render(<AgentCompilationPreview api={api} path="/api/v2/management/workspaces/ws/agents/a" revision={4} dirty={false} functions={[accounts, balance, overview]}/>);
  fireEvent.click(screen.getByRole("button", { name: "检查并预览三层结构" }));
  expect(get).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: /资金概览规则/ }));
  expect(await screen.findByText("先查询账户，再查询余额")).toBeVisible();
  expect(screen.getByText("固定规则 v2 · 2 步")).toBeVisible(); expect(screen.queryByText("不应展示")).toBeNull();
  expect(screen.getByText("在「账户列表」完成后执行")).toBeVisible();
  const parameterDetails = screen.getAllByText("参数来源"); fireEvent.click(parameterDetails[1]);
  expect(screen.getByText("使用「账户列表」的结果")).toBeVisible();
  expect(screen.getByText("对「账户列表」的结果逐项处理")).toBeVisible();
  expect(get.mock.calls.every(([path]) => path.includes("/versions"))).toBe(true);
});

it("prioritizes real business objects and leaves result-only types available in collapsed details", async () => {
  const api = new ApiClient("management");
  const compiled = { ...projection, projection: { ...projection.projection!, function_semantics: { accounts: { object_type: "Account", participant_roles: [] }, product: { object_type: "Product", participant_roles: [] } }, entities: [
    { id: "AccountResult", label: "账户查询结果", function_ids: ["accounts"] }, ...projection.projection!.entities,
  ] } };
  vi.spyOn(api, "post").mockResolvedValue(compiled);
  render(<AgentCompilationPreview api={api} path="/api/v2/management/workspaces/ws/agents/a" revision={4} dirty={false} functions={[accounts, balance, overview]}/>);
  fireEvent.click(screen.getByRole("button", { name: "检查并预览三层结构" }));
  const objects = await screen.findByLabelText("业务对象");
  expect(within(objects).queryByRole("button", { name: /账户查询结果/ })).toBeNull();
  expect(screen.getByRole("region", { name: "账户的能力关系" })).toBeVisible();
  fireEvent.click(screen.getByText("其他结果对象（1）"));
  fireEvent.click(screen.getByRole("button", { name: /账户查询结果/ }));
  expect(screen.getByRole("region", { name: "账户查询结果的能力关系" })).toBeVisible();
});

it("does not substitute a newer workflow version when a pinned rule is missing", async () => {
  const api = new ApiClient("management");
  vi.spyOn(api, "post").mockResolvedValue(projection);
  vi.spyOn(api, "get").mockResolvedValue([{ id: "other", skill_id: "skill-a", version: 8, definition: { name: "新规则", description: "不应代用", steps: [] } }]);
  render(<AgentCompilationPreview api={api} path="/api/v2/management/workspaces/ws/agents/a" revision={4} dirty={false} functions={[balance, accounts, overview]}/>);
  fireEvent.click(screen.getByRole("button", { name: "检查并预览三层结构" }));
  fireEvent.click(await screen.findByRole("button", { name: /资金概览规则/ }));
  expect(await screen.findByText("固定规则版本不可用，未使用其他版本替代。")).toBeVisible();
  expect(screen.queryByText("不应代用")).toBeNull();
});

it("shows document rules as dynamically planned goals with pinned child capabilities, without inventing a fixed step count",async()=>{
  const api=new ApiClient("management");vi.spyOn(api,"post").mockResolvedValue(projection);
  vi.spyOn(api,"get").mockImplementation(async path=>{
    if(path.endsWith("/business-skills/skill-a/versions"))return [{id:"sv-pinned",skill_id:"skill-a",version:2,definition:{name:"资金概览规则",description:"根据客户账户汇总余额",mode:"document",document:"## 办理规则\n先查询账户，再逐个查询余额。"}}] as never;
    const f=[balance,accounts,overview].find(item=>path.endsWith(`/functions/${item.id}/versions`));
    return f?[published(f)] as never:[] as never;
  });
  render(<AgentCompilationPreview api={api} path="/api/v2/management/workspaces/ws/agents/a" revision={4} dirty={false} functions={[accounts,balance,overview]}/>);
  fireEvent.click(screen.getByRole("button",{name:"检查并预览三层结构"}));fireEvent.click(await screen.findByRole("button",{name:/资金概览规则/}));
  expect(await screen.findByText("固定规则 v2 · 文档规则")).toBeVisible();expect(screen.queryByText(/固定规则.*0 步/)).toBeNull();
  expect(screen.getByText("每轮根据说明生成计划，实际执行步骤可在运行日志查看。")).toBeVisible();
  const children=screen.getByText("可调用的子功能").nextElementSibling!;expect(children).toHaveTextContent("账户列表");expect(children).toHaveTextContent("余额查询");
  fireEvent.click(screen.getByText("查看 Skill 说明"));expect(screen.getByText("先查询账户，再逐个查询余额。")).toBeVisible();
});
