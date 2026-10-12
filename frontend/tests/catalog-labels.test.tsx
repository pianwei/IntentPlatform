import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../shared/api";
import type { Capability, FunctionDefinition, FunctionOptions } from "../shared/types";
import { ConsoleWorkspace } from "../console/src/App";
import { blankDefinition } from "../console/src/FunctionEditor";
import { SemanticMappingEditor } from "../console/src/SemanticMappingEditor";

afterEach(() => cleanup());
const options: FunctionOptions = {
  default_domain_ref: null, default_result_type: "business:Detail", compiler_version: "finite-json-v2", http_connector_version: "2",
  entities: [{ id: "zhiyu:Customer", label: "客户", source_name: "智驭模拟业务实体" }, { id: "business:Customer", label: "客户", source_name: "基础业务对象" }],
  result_types: [], actions: [{ key: "ZY_READ", name: "查询", effect: "READ_ONLY" }, { key: "READ", name: "查询", effect: "READ_ONLY" }, { key: "ZY_CREATE", name: "创建", effect: "WRITE" }, { key: "CREATE", name: "创建", effect: "WRITE" }],
  relations: [], resolvers: [{ entity_type: "zhiyu:Customer", reference: "customers@1", version: "1" }],
};
const definition: FunctionDefinition = { ...structuredClone(blankDefinition), object_type: "zhiyu:Customer", action: "ZY_READ",
  input_schema: { type: "object", properties: { customer_id: { type: "string" } } },
  output_schema: { type: "object", properties: { customers: { type: "array", items: { type: "object", properties: { id: { type: "string" } } } } } },
};

it("groups duplicate entity labels by their source without changing selected IDs or resolver availability", async () => {
  const api = new ApiClient("management"), update = vi.fn(); vi.spyOn(api, "get").mockResolvedValue(options);
  render(<SemanticMappingEditor api={api} workspace="s" definition={definition} update={update} />);
  const select = screen.getByLabelText("业务对象");
  const source = await within(select).findByRole("group", { name: "智驭模拟业务实体" });
  expect(within(source).getByRole("option", { name: "客户" })).toHaveValue("zhiyu:Customer");
  expect(within(within(select).getByRole("group", { name: "基础业务对象" })).getByRole("option", { name: "客户" })).toHaveValue("business:Customer");
  expect(select).toHaveValue("zhiyu:Customer"); expect(update).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("参数与业务对象如何关联（选填）"));
  const parameter = screen.getByLabelText("参数对应对象 customer_id");
  expect(within(within(parameter).getByRole("group", { name: "基础业务对象" })).getByRole("option", { name: /暂不可自动识别/ })).toBeDisabled();
  expect(within(within(parameter).getByRole("group", { name: "智驭模拟业务实体" })).getByRole("option", { name: "客户" })).toBeEnabled();
  fireEvent.change(parameter, { target: { value: "zhiyu:Customer" } });
  expect(update).toHaveBeenLastCalledWith(expect.objectContaining({ argument_bindings: { customer_id: { role: "target", resolver_ref: "customers@1" } }, participant_roles: [expect.objectContaining({ type: "zhiyu:Customer" })] }));
  const result = screen.getByLabelText("对象类型");
  expect(within(result).getByRole("group", { name: "智驭模拟业务实体" })).toBeInTheDocument();
  expect(within(within(result).getByRole("group", { name: "基础业务对象" })).getByRole("option", { name: "客户" })).toBeDisabled();
});

it("distinguishes same-name business actions by source while retaining effect and confirmation semantics", async () => {
  const api = new ApiClient("management"), update = vi.fn(); vi.spyOn(api, "get").mockResolvedValue(options);
  render(<SemanticMappingEditor api={api} workspace="s" definition={definition} update={update} />);
  const select = screen.getByLabelText("动作");
  const source = await within(select).findByRole("group", { name: "智驭业务动作" });
  expect(within(source).getByRole("option", { name: "查询" })).toHaveValue("ZY_READ");
  expect(within(within(select).getByRole("group", { name: "基础业务动作" })).getByRole("option", { name: "查询" })).toHaveValue("READ");
  expect(select).toHaveValue("ZY_READ"); expect(update).not.toHaveBeenCalled();
  fireEvent.change(select, { target: { value: "READ" } });
  expect(update).toHaveBeenLastCalledWith({ action: "READ", effect: "READ_ONLY", requires_confirmation: false, authoring_version: "function-v2" });
  fireEvent.change(select, { target: { value: "ZY_CREATE" } });
  expect(update).toHaveBeenLastCalledWith({ action: "ZY_CREATE", effect: "WRITE", requires_confirmation: true, authoring_version: "function-v2" });
});

it("supports options without source metadata and disambiguates custom actions without replacing their keys", async () => {
  const api = new ApiClient("management"), update = vi.fn();
  vi.spyOn(api, "get").mockResolvedValue({ ...options, entities: [{ id: "old:Customer", label: "旧客户" }], actions: [{ key: "CUSTOM_A", name: "查看", effect: "READ_ONLY", description: "客户资料" }, { key: "CUSTOM_B", name: "查看", effect: "READ_ONLY", description: "产品资料" }] });
  render(<SemanticMappingEditor api={api} workspace="s" definition={{ ...definition, object_type: "old:Customer", action: "CUSTOM_A" }} update={update} />);
  expect(await within(screen.getByLabelText("业务对象")).findByRole("group", { name: "业务对象" })).toHaveTextContent("旧客户");
  expect(screen.getByLabelText("业务对象")).toHaveValue("old:Customer");
  const select = screen.getByLabelText("动作"); expect(within(select).getByRole("option", { name: "查看（产品资料）" })).toHaveValue("CUSTOM_B");
  fireEvent.change(select, { target: { value: "CUSTOM_B" } }); expect(update).toHaveBeenLastCalledWith(expect.objectContaining({ action: "CUSTOM_B" }));
});

it("ignores a prior workspace's delayed semantic catalogue without modifying the draft", async () => {
  const api = new ApiClient("management"), update = vi.fn(); let finish!: (value: FunctionOptions) => void;
  const pending = new Promise<FunctionOptions>(resolve => { finish = resolve; });
  vi.spyOn(api, "get").mockImplementation(path => path.includes("/a/") ? pending as never : Promise.resolve({ ...options, entities: [{ id: "b:Customer", label: "当前客户", source_name: "当前目录" }] }) as never);
  const view = render(<SemanticMappingEditor api={api} workspace="a" definition={definition} update={update} />);
  view.rerender(<SemanticMappingEditor api={api} workspace="b" definition={definition} update={update} />);
  await within(screen.getByLabelText("业务对象")).findByRole("option", { name: "当前客户" });
  await act(async () => { finish(options); await pending; });
  expect(within(screen.getByLabelText("业务对象")).getByRole("group", { name: "当前目录" })).toBeInTheDocument();
  expect(within(screen.getByLabelText("业务对象")).queryByRole("group", { name: "智驭模拟业务实体" })).toBeNull(); expect(update).not.toHaveBeenCalled();
});

const capability = (space: string, type: string, id = "f"): Capability => ({ id, workspace_id: space, name: `功能 ${id}`, description: "业务资料", revision: 1, latest_version: 1, status: "published", is_fixture: true, definition: { ...structuredClone(blankDefinition), object_type: type } });
function library(catalogue: (space: string) => Promise<FunctionOptions>, functions?: Capability[], roles = ["admin"]) {
  const api = new ApiClient("management"), spaces = ["a", "b"].map(id => ({ id, name: `空间 ${id}`, roles }));
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/v2/me") return { user_id: "u", username: "test", display_name: "测试用户", preferences: {} } as never;
    if (path === "/api/v2/management/workspaces") return spaces as never;
    const space = path.split("/workspaces/")[1]?.split("/")[0];
    if (path.endsWith("/function-options")) return catalogue(space) as never;
    if (path.endsWith("/functions")) return (functions || [capability(space, "shared:Customer", space)]) as never;
    if (space && !path.slice(path.indexOf("/workspaces/") + 12).includes("/")) return { ...spaces.find(item => item.id === space), status: "active" } as never;
    return [] as never;
  });
  const router = createMemoryRouter([{ path: "*", element: <ConsoleWorkspace api={api} identity={{ user_id: "u", access_token: "test", fixture_mode: false }} onLogout={async () => {}} /> }], { initialEntries: ["/spaces/a/functions"] });
  render(<RouterProvider router={router} />); return { get, router };
}

it("uses exact registered Chinese labels in the function list with friendly legacy and missing-type fallbacks", async () => {
  library(async () => ({ ...options, entities: [{ id: "zhiyu:Customer", label: "管户客户", source_name: "智驭业务" }] }), [capability("a", "zhiyu:Customer", "current"), capability("a", "Product", "legacy"), capability("a", "custom:Unregistered", "unknown"), capability("a", "", "empty")]);
  expect(await screen.findByRole("cell", { name: "管户客户" })).toBeVisible(); expect(screen.getByRole("cell", { name: "产品" })).toBeVisible();
  expect(screen.getByRole("cell", { name: "未登记对象" })).toBeVisible(); expect(screen.getByRole("cell", { name: "未设置" })).toBeVisible();
  expect(screen.queryByRole("cell", { name: "Customer" })).toBeNull(); expect(screen.queryByRole("cell", { name: "Product" })).toBeNull();
});

it("keeps the function list usable when catalogue retrieval fails", async () => {
  library(async () => { throw new Error("目录暂不可用"); }, [capability("a", "zhiyu:Customer")]);
  expect(await screen.findByRole("cell", { name: "客户" })).toBeVisible(); expect(screen.getByRole("button", { name: /功能 f/ })).toBeEnabled();
  expect(screen.queryByText("目录暂不可用")).toBeNull();
});

it("does not mix function labels across spaces when an old catalogue arrives late", async () => {
  let finish!: (value: FunctionOptions) => void; const pending = new Promise<FunctionOptions>(resolve => { finish = resolve; });
  const { router, get } = library(space => space === "a" ? pending : Promise.resolve({ ...options, entities: [{ id: "shared:Customer", label: "乙空间客户" }] }));
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/v2/management/workspaces/a/function-options"));
  await act(async () => { await router.navigate("/spaces/b/functions"); });
  expect(await screen.findByRole("cell", { name: "乙空间客户" })).toBeVisible();
  await act(async () => { finish({ ...options, entities: [{ id: "shared:Customer", label: "甲空间客户" }] }); await pending; });
  expect(screen.getByRole("cell", { name: "乙空间客户" })).toBeVisible(); expect(screen.queryByRole("cell", { name: "甲空间客户" })).toBeNull();
  expect(screen.queryByRole("button", { name: /功能 a/ })).toBeNull();
});

it("does not fetch maintainer-only catalogue options for a read-only member", async () => {
  const { get } = library(async () => options, [capability("a", "Product")], ["user"]);
  expect(await screen.findByRole("cell", { name: "产品" })).toBeVisible(); expect(screen.getByRole("button", { name: "新建功能" })).toBeDisabled();
  expect(get.mock.calls.some(([path]) => path.endsWith("/function-options"))).toBe(false);
});
