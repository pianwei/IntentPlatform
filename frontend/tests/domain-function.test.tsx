import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DomainEditor } from "../console/src/DomainPage";
import { FunctionEditor, blankDefinition } from "../console/src/FunctionEditor";
import { SemanticMappingEditor } from "../console/src/SemanticMappingEditor";
import { ApiClient } from "../shared/api";
import { blankDomain, type DomainDraft, type DomainVersion } from "../shared/domains";
import type { Capability, FunctionDefinition } from "../shared/types";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const definition = { ...structuredClone(blankDomain), name: "测试领域", description: "合成测试数据", source: { reference: "test-source", owner: "test-owner" }, concepts: [{ id: "test:Customer", label: "客户", kind: "object" as const, parents: [] }] };
const domain: DomainDraft = { id: "domain-a", workspace_id: "ws", key: "test", revision: 2, latest_version: 1, definition, validation: { valid: true, revision: 2, errors: [], checks: {} } };
const respond = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
function reordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(reordered);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).reverse().map(([key, nested]) => [key, reordered(nested)]));
  return value;
}
it("remains clean after a publish response reorders JSONB keys and retains local input on later refresh", async () => {
  const d = { ...structuredClone(blankDefinition), name: "功能", description: "说明", object_type: "Obj", result_type: "Result", aliases: ["a", "b"] };
  const item: Capability = { id: "f", workspace_id: "ws", revision: 3, status: "draft", name: d.name, description: d.description, definition: d, latest_version: null, is_fixture: false };
  let server = item;
  const api = new ApiClient("management");
  vi.spyOn(api, "get").mockImplementation(async () => ({ revision: server.revision, required: false, ready: true, issues: [], citations: [] }) as never);
  vi.spyOn(api, "patch").mockImplementation(async (_path, body) => { server = { ...item, revision: 4, definition: (body as { definition: FunctionDefinition }).definition }; return server as never; });
  const post = vi.spyOn(api, "post").mockImplementation(async path => {
    if (path.endsWith("/validate")) return { valid: true, revision: 4, errors: [], checks: {}, execution_test:{passed:true,revision:4,status:"SUCCEEDED"} } as never;
    server = { ...server, latest_version: 1, status: "published", definition: reordered(server.definition) as FunctionDefinition }; return server as never;
  });
  let refresh: (value: Capability) => void = () => {};
  function Harness() { const [current, setCurrent] = useState(item); refresh = setCurrent; return <FunctionEditor api={api} workspace="ws" item={current} writable onClose={vi.fn()} onSaved={async next => setCurrent(next)} />; }
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", {name:/3 验证与发布/}));
  await waitFor(() => expect(screen.getByRole("button", { name: "执行测试" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("功能名称"), { target: { value: "新名称" } });
  fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "执行测试" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "执行测试" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "发布功能版本" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "发布功能版本" }));
  await waitFor(() => { expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/functions/f/publish", { revision: 4 }); expect(screen.getByRole("button", { name: "执行测试" })).toBeEnabled(); });
  expect(screen.queryByText("有尚未保存的映射，请先保存草稿后验证。")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("完整 JSON 与低频配置"));
  const raw = screen.getByLabelText("FunctionDefinition");
  const local = JSON.stringify({ ...server.definition, aliases: ["b", "a"] }, null, 4);
  fireEvent.change(raw, { target: { value: local } });
  act(() => refresh({ ...server, definition: reordered(server.definition) as FunctionDefinition }));
  expect(raw).toHaveValue(local); expect(screen.getByRole("button", { name: "发布功能版本" })).toBeDisabled();
});
it("retains domain edits after stale revision and forbids validation/publish of unsaved input", async () => {
  const fetch = vi.fn((path: string, init?: RequestInit) => Promise.resolve(path.endsWith("/versions") ? respond([]) : respond({ detail: "stale revision" }, 409)));
  vi.stubGlobal("fetch", fetch);
  render(<DomainEditor api={new ApiClient("management")} workspace="ws" item={domain} writable publishable onClose={vi.fn()} onChanged={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("领域名称"), { target: { value: "未保存的新名称" } });
  expect(screen.getByRole("button", { name: "验证领域草稿" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "发布领域版本" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存领域草稿" }));
  await screen.findByText(/stale revision/);
  expect(screen.getByLabelText("领域名称")).toHaveValue("未保存的新名称");
  const call = fetch.mock.calls.find(([, init]) => init?.method === "PATCH")!;
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({ revision: 2, definition: { name: "未保存的新名称" } });
  expect(fetch.mock.calls.some(([path]) => path.endsWith("/publish"))).toBe(false);
});
it("saves, validates the returned revision then publishes exact domain revision", async () => {
  const fetch = vi.fn((path: string, init?: RequestInit) => Promise.resolve(path.endsWith("/versions") ? respond([]) : path.endsWith("/draft") ? respond({ ...domain, revision: 3, definition: { ...definition, name: "修订" }, validation: null }) : path.endsWith("/validate") ? respond({ valid: true, revision: 3, errors: [], checks: {} }) : respond({})));
  vi.stubGlobal("fetch", fetch);
  render(<DomainEditor api={new ApiClient("management")} workspace="ws" item={domain} writable publishable onClose={vi.fn()} onChanged={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("领域名称"), { target: { value: "修订" } }); fireEvent.click(screen.getByRole("button", { name: "保存领域草稿" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "验证领域草稿" })).toBeEnabled());
  expect(screen.getByRole("button", { name: "发布领域版本" })).toBeDisabled(); fireEvent.click(screen.getByRole("button", { name: "验证领域草稿" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "发布领域版本" })).toBeEnabled()); fireEvent.click(screen.getByRole("button", { name: "发布领域版本" }));
  await waitFor(() => expect(fetch.mock.calls.some(([path]) => path.endsWith("/publish"))).toBe(true));
  expect(JSON.parse(String(fetch.mock.calls.find(([path]) => path.endsWith("/publish"))![1]?.body))).toEqual({ revision: 3 });
});
it("uses one function draft and blocks invalid advanced JSON without losing it", async () => {
  const d = { ...structuredClone(blankDefinition), name: "功能", description: "测试", object_type: "Obj", result_type: "Result" };
  const item: Capability = { id: "f", workspace_id: "ws", revision: 4, status: "draft", name: d.name, description: d.description, definition: d, latest_version: null, is_fixture: false };
  const api = new ApiClient("management"); const patch = vi.spyOn(api, "patch").mockResolvedValue(item);
  render(<FunctionEditor api={api} workspace="ws" item={item} writable onClose={vi.fn()} onSaved={async () => {}} />);
  fireEvent.click(screen.getByText("完整 JSON 与低频配置"));
  const raw = screen.getByLabelText("FunctionDefinition");
  fireEvent.change(raw, { target: { value: JSON.stringify({ ...d, aliases: ["保留别名"], presentation: { ...d.presentation, title: "高级标题" } }) } });
  fireEvent.change(screen.getByLabelText("功能名称"), { target: { value: "普通表单新名称" } });
  expect(JSON.parse((raw as HTMLTextAreaElement).value)).toMatchObject({ name: "普通表单新名称", aliases: ["保留别名"], presentation: { title: "高级标题" } });
  fireEvent.change(raw, { target: { value: "{broken" } });
  expect(screen.getByLabelText("功能名称")).toBeDisabled(); expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled(); expect(raw).toHaveValue("{broken"); expect(patch).not.toHaveBeenCalled();
  fireEvent.change(raw, { target: { value: JSON.stringify({ ...d, aliases: ["保留别名"] }) } }); fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/functions/f/draft", { revision: 4, definition: { ...d, aliases: ["保留别名"] } }));
});
it("reads current entity options without silently editing the draft, and pins their latest references on explicit selection", async () => {
  const options={default_domain_ref:{package_id:domain.id,version:2},domain_refs:[{package_id:domain.id,version:2}],default_result_type:"test:CustomerResult",entities:[{id:"test:Customer",label:"客户"}],result_types:[],actions:[],relations:[],resolvers:[]};
  const api = new ApiClient("management"); const get = vi.spyOn(api, "get").mockResolvedValue(options);
  let latest: FunctionDefinition = blankDefinition;
  function Harness() { const [d, setD] = useState<FunctionDefinition>(blankDefinition); latest = d; return <SemanticMappingEditor api={api} workspace="ws" definition={d} update={patch => setD({ ...d, ...patch })} />; }
  render(<Harness />); await screen.findByRole("option",{name:"客户"});expect(latest.compiler_version).toBeUndefined();
  expect(screen.queryByLabelText("编译器版本")).toBeNull();expect(screen.queryByLabelText("精确发布版本")).toBeNull();
  fireEvent.change(screen.getByLabelText("业务对象"),{target:{value:"test:Customer"}});
  expect(latest.domain_refs).toEqual([{ package_id: "domain-a", version: 2 }]);
  expect(get.mock.calls.every(([path]) => path.includes("/workspaces/ws/"))).toBe(true);
});
it("uses a Unicode sets compatible HTML pattern for domain keys", () => {
  const api = new ApiClient("management");
  render(<DomainEditor api={api} workspace="ws" item={null} writable publishable onClose={vi.fn()} onChanged={vi.fn()} />);
  const pattern = screen.getByLabelText("领域标识").getAttribute("pattern")!;
  const expression = new RegExp(`^(?:${pattern})$`, "v");
  expect(expression.test("test-domain_1")).toBe(true); expect(expression.test("test/domain")).toBe(false);
});
it("retains structurally invalid HTTP mappings in advanced JSON without crashing the Console", async () => {
  const d = { ...structuredClone(blankDefinition), name: "功能", description: "说明", object_type: "Obj", result_type: "Result" };
  const item: Capability = { id: "f", workspace_id: "ws", revision: 1, status: "draft", name: d.name, description: d.description, definition: d, latest_version: null, is_fixture: false };
  const api = new ApiClient("management"); vi.spyOn(api, "get").mockResolvedValue({ revision: 1, required: false, ready: true, issues: [], citations: [] });
  render(<FunctionEditor api={api} workspace="ws" item={item} writable onClose={vi.fn()} onSaved={async () => {}} />);
  fireEvent.click(screen.getByText("完整 JSON 与低频配置")); const raw = screen.getByLabelText("FunctionDefinition");
  for (const http_mapping of [{}, { path: {}, query: "invalid", headers: {}, body: {} }, { path: { id: { nested: 1 } }, query: {}, headers: {}, body: {} }]) {
    const text = JSON.stringify({ ...d, connector: { ...d.connector, version: "2", http_mapping } });
    fireEvent.change(raw, { target: { value: text } }); expect(raw).toHaveValue(text); expect(screen.getByLabelText("功能名称")).toBeDisabled(); expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
  }
  fireEvent.change(raw, { target: { value: JSON.stringify(d) } }); expect(screen.getByLabelText("功能名称")).toBeEnabled();
});
it("preserves invalid initial HTTP fields and rejects failure-code and boolean edits until fixed", () => {
  const bad = { ...structuredClone(blankDefinition), name: "待修复定义", description: "说明", object_type: "Obj", result_type: "Result", connector: { ...blankDefinition.connector, version: "2", http_failure_statuses: 42 } };
  const item = { id: "bad", workspace_id: "ws", revision: 1, status: "draft", name: bad.name, description: bad.description, definition: bad, latest_version: null, is_fixture: false } as unknown as Capability;
  const api = new ApiClient("management"); vi.spyOn(api, "get").mockResolvedValue({ revision: 1, required: false, ready: true, issues: [], citations: [] });
  render(<FunctionEditor api={api} workspace="ws" item={item} writable onClose={vi.fn()} onSaved={async () => {}} />);
  const raw = screen.getByLabelText("FunctionDefinition"); expect(raw).toHaveValue(JSON.stringify(bad, null, 2));
  for (const patch of [{ http_failure_statuses: {} }, { http_failure_statuses: [401] }, { http_failure_statuses: [404, "422"] }, { http_failure_statuses: [404, 404] }, { http_failure_statuses: [], http_body_required: "false" }, { http_failure_statuses: [], http_body_required: null }]) {
    const text = JSON.stringify({ ...bad, connector: { ...bad.connector, ...patch } }); fireEvent.change(raw, { target: { value: text } }); expect(raw).toHaveValue(text); expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
  }
  fireEvent.change(raw, { target: { value: JSON.stringify({ ...bad, connector: { ...bad.connector, http_failure_statuses: [404], http_body_required: false } }) } }); expect(screen.getByRole("button", { name: "保存草稿" })).toBeEnabled();
});
it("opens a server draft with an empty HTTP mapping directly as repairable raw JSON", () => {
  const bad = { ...structuredClone(blankDefinition), name: "旧坏草稿", connector: { ...blankDefinition.connector, kind: "http", version: "2", http_mapping: {} } };
  const item = { id: "bad-map", workspace_id: "ws", revision: 1, status: "draft", name: bad.name, description: "", definition: bad, latest_version: null, is_fixture: false } as unknown as Capability;
  const api = new ApiClient("management"); vi.spyOn(api, "get").mockResolvedValue({ revision: 1, required: false, ready: true, issues: [], citations: [] });
  render(<FunctionEditor api={api} workspace="ws" item={item} writable onClose={vi.fn()} onSaved={async () => {}} />);
  const raw = screen.getByLabelText("FunctionDefinition"); expect(raw).toHaveValue(JSON.stringify(bad, null, 2));
  expect(screen.queryByLabelText("HTTP 连接器版本")).not.toBeInTheDocument(); expect(screen.queryByLabelText("编译器版本")).not.toBeInTheDocument(); expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
  fireEvent.change(raw, { target: { value: JSON.stringify({ ...bad, connector: { ...bad.connector, http_mapping: { path: {}, query: {}, headers: {}, body: {} } } }) } });
  expect(screen.queryByLabelText("HTTP 连接器版本")).toBeNull(); expect(screen.getByRole("button", { name: "保存草稿" })).toBeEnabled();
});
