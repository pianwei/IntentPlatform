import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ConsoleWorkspace } from "../console/src/App";
import { AgentEditor } from "../console/src/AgentEditor";
import { AgentCompilationPreview } from "../console/src/AgentCompilationPreview";
import { CompositeRuleEditor } from "../console/src/CompositeRuleEditor";
import { FunctionEditor, blankDefinition } from "../console/src/FunctionEditor";
import { WorkspaceSettingsPage } from "../console/src/WorkspaceSettingsPage";
import { RunTracePage } from "../console/src/RunTracePage";
import { ApiClient } from "../shared/api";
import type { AgentCompilation, Capability, FunctionDefinition } from "../shared/types";
vi.mock("../shared/environment", () => ({ DEVELOPMENT_UI: true }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const direct: Capability = { id: "f1", workspace_id: "ws", name: "余额查询", description: "读取账户余额", revision: 1, status: "published", latest_version: 3, is_fixture: true, definition: { ...blankDefinition, name: "余额查询", object_type: "Account", action: "READ_BALANCE", result_type: "Balance" } };
const projection: AgentCompilation = { valid: true, issues: [], projection: { version: "agent-projection-v1", entities: [{ id: "Account", label: "账户", function_ids: ["f1"] }], actions: [{ key: "READ_BALANCE", name: "查询余额", effect: "READ_ONLY", function_ids: ["f1"] }], skills: [{ skill_id: "s1", version_id: "sv1", name: "资金概览", function_id: "composite" }], explicit_function_ids: ["composite"], dependency_function_ids: ["f1"] } };
const rule = { key: "balance.overview", name: "余额规则", description: "查询指定账户余额", input_fields: ["account_id"], applicable_entities: ["Account"], applicable_actions: ["READ_BALANCE"], triggers: ["余额概览"], steps: [{ step_id: "balance", capability_id: "f1", arguments: { account_id: { $input: "account_id" } }, depends_on: [] }] };
function apiFixture() {
  const api = new ApiClient("management");
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/v2/management/workspaces") return [{ id: "ws", name: "业务空间", roles: ["admin"] }] as never;
    if (path.endsWith("/functions")) return [direct] as never;
    if (path.endsWith("/functions/f1/versions")) return [{ id: "fv3", function_id: "f1", version: 3, definition: direct.definition }] as never;
    if (path.endsWith("/catalogs")) return { integrations: [], credentials: [], resolvers: [] } as never;
    return [] as never;
  }); return api;
}

it("opens the agent workspace with exactly three primary entries and two settings, without engineering modules", async () => {
  const api = apiFixture();
  render(<ConsoleWorkspace api={api} identity={{ access_token: "x", user_id: "admin", fixture_mode: false }} onLogout={async () => {}} />);
  await waitFor(()=>expect(screen.getByRole("button",{name:"切换工作空间"})).toHaveTextContent("业务空间"));
  expect(within(screen.getByRole("navigation", { name: "主要功能" })).getAllByRole("button").map(item => item.textContent)).toEqual(["智能体", "功能库", "运行日志"]);
  expect(screen.getByRole("button",{name:"空间设置"})).toBeVisible();expect(screen.getByRole("button",{name:"模型配置"})).toBeVisible();expect(screen.queryByRole("button",{name:"平台管理"})).toBeNull();
  expect(screen.getByRole("heading", { level: 1, name: "智能体" })).toBeVisible();
  for (const name of ["领域管理", "来源文档", "操作审计", "接入准备", "评测诊断", "业务动作与 Skills"]) expect(screen.queryByRole("button", { name })).toBeNull();
  expect(vi.mocked(api.get).mock.calls.some(([path]) => /domains|documents|audit|business-skills/.test(path))).toBe(false);
});

it("creates an agent with the new three-layer strategy and keeps tests within its details", async () => {
  const api = apiFixture(); const post = vi.spyOn(api, "post").mockResolvedValue({ id: "agent", workspace_id: "ws", name: "新助手", revision: 1 });
  render(<AgentEditor api={api} workspace="ws" item={null} functions={[direct]} onClose={() => {}} onSaved={async () => {}} />);
  fireEvent.change(screen.getByLabelText("智能体名称"), { target: { value: "新助手" } });
  fireEvent.click(screen.getByRole("button", { name: "业务能力" }));
  fireEvent.click(screen.getByRole("button",{name:"添加功能"}));
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择功能 余额查询" }));
  fireEvent.click(screen.getByRole("button", { name: /确认添加/ }));
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/agents", expect.objectContaining({ routing_strategy: "three-layer-v1", planning_enabled: true, capability_ids: ["f1"] })));
  expect(screen.getByRole("button", { name: "试聊已发布版本" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "测试" })).toBeNull();
});

it("compiles only a saved revision and separates explicit candidates from internal dependencies", async () => {
  const api = apiFixture(); const post = vi.spyOn(api, "post").mockResolvedValue(projection);
  const { rerender } = render(<AgentCompilationPreview api={api} path="/agent" revision={4} dirty functions={[direct]} />);
  expect(screen.getByRole("button", { name: "检查并预览三层结构" })).toBeDisabled();
  rerender(<AgentCompilationPreview api={api} path="/agent" revision={4} dirty={false} functions={[direct]} />);
  fireEvent.click(screen.getByRole("button", { name: "检查并预览三层结构" }));
  await screen.findByText("三层结构校验通过，可按当前草稿发布。");fireEvent.click(screen.getByText("查看自主规划与组合依赖范围"));
  expect(post).toHaveBeenCalledOnce(); expect(post).toHaveBeenCalledWith("/agent/compile-preview", { revision: 4 });
  expect(screen.getByRole("button", {name: /^账户/})).toHaveAttribute("aria-pressed", "true"); expect(screen.getByText("资金概览")).toBeVisible();
  expect(screen.getByText("组合内部依赖：").parentElement).toHaveTextContent("余额查询");
  rerender(<AgentCompilationPreview api={api} path="/agent" revision={4} dirty functions={[direct]} />);
  expect(screen.queryByText("资金概览")).not.toBeInTheDocument();
});

it("shows compile rejection with the affected function instead of offering a successful preview", async () => {
  const api = apiFixture(); vi.spyOn(api, "post").mockResolvedValue({ valid: false, issues: [{ code: "missing_dependency", message: "依赖版本已不可用", function_id: "f1" }], projection: null });
  render(<AgentCompilationPreview api={api} path="/agent" revision={2} dirty={false} functions={[direct]} />);
  fireEvent.click(screen.getByRole("button", { name: "检查并预览三层结构" }));
  await screen.findByText("依赖版本已不可用"); expect(screen.getByText("余额查询：")).toBeVisible();
});

it("does not expose development fixture setup in the default Console", async () => {
  const api=apiFixture(); const post=vi.spyOn(api,"post");
  render(<ConsoleWorkspace api={api} identity={{access_token:"x",user_id:"admin",fixture_mode:true}} onLogout={async()=>{}}/>);
  await waitFor(()=>expect(screen.getByRole("button",{name:"切换工作空间"})).toHaveTextContent("业务空间"));
  expect(screen.queryByText("本地模拟演示")).toBeNull();
  expect(screen.queryByRole("button",{name:"安装或核对演示数据"})).toBeNull();expect(post).not.toHaveBeenCalled();
});

it("uses real chat testing for a published three-layer agent instead of the legacy route score", async () => {
  const api = apiFixture();
  render(<AgentEditor api={api} workspace="ws" item={{ id: "a", workspace_id: "ws", name: "三层助手", current_release_id: "release-1", public_id: "public-a", published_channels: { chat: true, embed: true }, live_channels: { chat: true, embed: true }, revision: 1, routing_strategy: "three-layer-v1", fixture_mode: true, suggested_questions: [] }} functions={[direct]} onClose={() => {}} onSaved={async () => {}} />);
  expect(screen.getByRole("button", { name: "试聊已发布版本" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "测试" })).toBeNull();
  expect(screen.queryByRole("button", { name: "开始路由诊断" })).toBeNull();
});

it("ignores a delayed successful compile after the saved draft changes", async () => {
  const api = apiFixture(); let finish!: (value: AgentCompilation) => void;
  const response = new Promise<AgentCompilation>(resolve => { finish = resolve; });
  vi.spyOn(api, "post").mockReturnValue(response as never);
  const { rerender } = render(<AgentCompilationPreview api={api} path="/agent" revision={2} dirty={false} functions={[direct]} />);
  fireEvent.click(screen.getByRole("button", { name: "检查并预览三层结构" }));
  rerender(<AgentCompilationPreview api={api} path="/agent" revision={3} dirty={false} functions={[direct]} />);
  await act(async () => { finish(projection); await response; });
  expect(screen.queryByText("三层结构校验通过，可按当前草稿发布。")).toBeNull();
  expect(screen.getByRole("button", { name: "检查并预览三层结构" })).toBeEnabled();
});

it("supports a composite function within the function editor without a connector form or nested composite dependency", async () => {
  const api = apiFixture();
  render(<FunctionEditor api={api} workspace="ws" item={null} functions={[direct]} writable onClose={() => {}} onSaved={async () => {}} />);
  fireEvent.change(screen.getByLabelText("执行方式"), { target: { value: "composite" } });
  expect(screen.queryByLabelText("接口地址")).not.toBeInTheDocument();
  expect(screen.getByRole("heading",{name:"组合设置"})).toBeVisible();
  fireEvent.click(await screen.findByRole("button", { name: "添加子功能" }));
  expect(await screen.findByRole("checkbox", { name: "组合子功能 余额查询" })).toBeInTheDocument();
  expect(screen.queryByLabelText("步骤规则 JSON")).toBeNull();
});

it("commits a document rule with concrete dependency versions and keeps unsaved rule input pending", async () => {
  const api = apiFixture(); const pending = vi.fn(); let authored:unknown = {};
  vi.mocked(api.get).mockImplementation(async path => (path.endsWith("/functions/f1/versions") ? [{ id: "fv3", function_id: "f1", version: 3, definition: direct.definition }] : path.endsWith("/versions") ? [{ id: "sv1", skill_id: "s1", version: 2, definition: authored }] : []) as never);
  vi.spyOn(api,"post").mockImplementation(async (path,body)=>{if(!path.endsWith("/publish"))authored=body;return {id:"s1",revision:1,latest_version:path.endsWith("/publish")?2:0,definition:authored} as never;});
  function Harness() { const [definition, setDefinition] = useState<FunctionDefinition>({ ...blankDefinition,name:"余额概览",description:"查询并汇总余额", execution_kind: "composite", composite: { skill_version_id: "", dependencies: [], final_output_mapping: {} } }); return <><CompositeRuleEditor api={api} workspace="ws" definition={definition} functions={[direct]} writable publishable update={patch => setDefinition(old => ({ ...old, ...patch }))} onPending={pending} /><output data-testid="definition">{JSON.stringify(definition.composite)}</output></>; }
  render(<Harness />);
  fireEvent.click(await screen.findByRole("button", { name: "添加子功能" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "组合子功能 余额查询" }));
  fireEvent.click(screen.getByRole("button", { name: "确认选择" }));
  fireEvent.change(screen.getByLabelText("Skill 说明"), { target: { value: "先查询余额，再汇总回答。" } });
  await waitFor(() => expect(pending).toHaveBeenLastCalledWith(true));
  fireEvent.click(screen.getByRole("button", { name: "应用组合配置" }));
  await waitFor(() => expect(pending).toHaveBeenLastCalledWith(false));
  expect(JSON.parse(screen.getByTestId("definition").textContent!)).toEqual({mode:"document",skill_version_id:"sv1",dependencies:[{function_id:"f1",version:3}],final_output_mapping:{}});
});

it("uses published definitions for dependencies even when the unpublished function draft changes execution kind", async () => {
  const api = apiFixture();
  const changingDraft = { ...direct, definition: { ...direct.definition, name: "未发布名称", execution_kind: "composite" as const } };
  const composite = { ...blankDefinition, execution_kind: "composite" as const, composite: { skill_version_id: "", dependencies: [], final_output_mapping: {} } };
  render(<CompositeRuleEditor api={api} workspace="ws" definition={composite} functions={[changingDraft]} writable publishable update={() => {}} onPending={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "添加子功能" }));
  expect(await screen.findByRole("checkbox", { name: "组合子功能 余额查询" })).toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: /未发布名称/ })).toBeNull();
});

it("protects the parent form while a delayed document rule publication is being saved", async () => {
  const api = apiFixture(); let finish!: () => void;
  const published = new Promise<void>(resolve => { finish = resolve; }); let authored:unknown = {};
  const definition: FunctionDefinition = { ...direct.definition,name:"资金概览",description:"先查询余额", execution_kind: "composite", composite: { skill_version_id: "sv1", dependencies: [{ function_id: "f1", version: 3 }], final_output_mapping: {} } };
  const item = { ...direct, id: "combined", definition, name: "资金概览" };
  vi.mocked(api.get).mockImplementation(async path => {
    if (path.endsWith("/source-review")) return { revision: 1, required: false, ready: true, issues: [], citations: [] } as never;
    if (path.endsWith("/functions/f1/versions")) return [{ id: "fv3", function_id: "f1", version: 3, definition: direct.definition }] as never;
    if (path.endsWith("/business-skills/s1/versions")) return [{ id: "sv1", skill_id: "s1", version: 1, definition: rule }] as never;
    if (path.endsWith("/business-skills/new/versions")) return [{id:"sv2",skill_id:"new",version:1,definition:authored}] as never;
    if (path.endsWith("/business-skills")) return [{ id: "s1", revision: 1, latest_version: 1, disabled: false, definition: rule }] as never;
    return [] as never;
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (path,body) => {if(path.endsWith("/business-skills")){authored=body;return {id:"new",revision:1,latest_version:0,definition:authored} as never;} await published; return {id:"new",revision:1,latest_version:1,definition:authored} as never; });
  const patch=vi.spyOn(api,"patch").mockResolvedValue({...item,revision:2,definition} as never);
  render(<FunctionEditor api={api} workspace="ws" item={item} functions={[direct]} writable onClose={() => {}} onSaved={async () => {}} />);
  await waitFor(()=>expect((screen.getByLabelText("Skill 说明") as HTMLTextAreaElement).value).toContain("适用目标"));
  fireEvent.change(screen.getByLabelText("功能名称"), { target: { value: "保存前新名称" } });
  fireEvent.change(screen.getByLabelText("Skill 说明"),{target:{value:"更新后的办理说明"}});
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(post.mock.calls.some(([path])=>path.endsWith("/publish"))).toBe(true));
  expect(screen.getByLabelText("功能名称")).toBeDisabled();
  expect(screen.getByLabelText("执行方式")).toBeDisabled();
  await act(async () => { finish(); await published; });
  await waitFor(()=>expect(patch).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/functions/combined/draft",expect.objectContaining({definition:expect.objectContaining({name:"保存前新名称",composite:expect.objectContaining({skill_version_id:"sv2"})})})));
});

it("never lets an older conversation detail replace the most recently selected conversation", async()=>{
  const api=apiFixture();let finishA!:(v:unknown)=>void;const pending=new Promise(resolve=>{finishA=resolve;});
  const detail=(id:string)=>({id,title:id+"会话",agent_name:"业务助手",turn_count:1,offset:0,limit:25,turns:[{run_id:id,question:id+"问题",status:"SUCCEEDED",timing:{total_ms:12,processing_ms:12,user_wait_ms:0},results:[{component:"text",data:id+"最终答复"}],prompts:[],interactions:[],phases:[]}]});
  vi.mocked(api.get).mockImplementation(async path=>{
    if(path.includes("/conversations/a?"))return await pending as never;
    if(path.includes("/conversations/b?"))return detail("b") as never;
    return {items:["a","b"].map(id=>({id,title:id+"会话",agent_name:"业务助手",updated_at:"2026-09-28T00:00:00Z",turn_count:1,status:"SUCCEEDED"})),total:2} as never;
  });
  render(<RunTracePage api={api} workspace="ws" admin/>);
  fireEvent.click(await screen.findByRole("button",{name:/a会话/}));fireEvent.click(screen.getByRole("button",{name:/b会话/}));
  await screen.findByText("b最终答复");await act(async()=>{finishA(detail("a"));await pending;});
  expect(screen.getByText("b最终答复")).toBeVisible();expect(screen.queryByText("a最终答复")).toBeNull();
});

it("allows only the platform administrator to create a workspace using a trusted identity", async () => {
  const api = apiFixture(); const created = { id: "new", name: "新业务空间", roles: ["admin"] }; const onCreated = vi.fn().mockResolvedValue(undefined);
  vi.mocked(api.get).mockResolvedValue({ can_manage_workspaces: true, identities: [{ user_id: "alice", label: "管理员甲" }] });
  const post = vi.spyOn(api, "post").mockResolvedValue(created);
  render(<WorkspaceSettingsPage api={api} workspace="" userId="root" admin={false} onCreated={onCreated} />);
  await screen.findByLabelText("空间管理员");
  fireEvent.change(screen.getByLabelText("空间名称"), { target: { value: "新业务空间" } });
  fireEvent.change(screen.getByLabelText("空间管理员"), { target: { value: "alice" } });
  fireEvent.click(screen.getByRole("button", { name: "创建空间并分配管理员" }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
  expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces", { name: "新业务空间", administrator_id: "alice" });
});

it("adds an approved identity as a space member without requesting platform roles", async () => {
  const api = apiFixture();
  vi.mocked(api.get).mockImplementation(async path => (path.endsWith("platform-context") ? { can_manage_workspaces: false, identities: [{ user_id: "alice", label: "成员甲" }] } : []) as never);
  const put = vi.spyOn(api, "put").mockResolvedValue({});
  render(<WorkspaceSettingsPage api={api} workspace="ws" userId="admin" admin onCreated={async () => {}} />);
  await screen.findByRole("option", { name: "成员甲" });
  expect(screen.queryByRole("button", { name: "创建空间并分配管理员" })).toBeNull();
  fireEvent.change(screen.getByLabelText("已确认用户"), { target: { value: "alice" } });
  fireEvent.click(screen.getByRole("button", { name: "添加成员" }));
  await waitFor(() => expect(put).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/members/alice", { revision: 0, roles: ["user"], active: true }));
  expect(screen.queryByRole("option", { name: "平台管理员" })).toBeNull();
});

it("does not fetch a member list for an identity without space administration rights", async () => {
  const api = apiFixture(); vi.mocked(api.get).mockResolvedValue({ can_manage_workspaces: false, identities: [] });
  render(<WorkspaceSettingsPage api={api} workspace="ws" userId="reader" admin={false} onCreated={async () => {}} />);
  await act(async () => {});
  expect(screen.getByText("当前身份没有该空间的成员管理权限。")).toBeVisible();
  expect(vi.mocked(api.get).mock.calls).toEqual([["/api/v2/management/platform-context"]]);
});
