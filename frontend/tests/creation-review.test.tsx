import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OpenAPIWizard } from "../console/src/OpenAPIWizard";
import { CreationFlow } from "../console/src/CreationFlow";
import { blankDefinition } from "../console/src/FunctionEditor";
import { ApiClient } from "../shared/api";
import type { SourceDocument, OpenAPIImport } from "../shared/sources";
import type { Capability } from "../shared/types";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const doc: SourceDocument = { id: "doc", filename: "api.yaml", revision: 1, text: "synthetic", sections: [], original_available: true, source: { document_id: "doc", revision: 1 }, fixture_mode: true };
const definition = { ...structuredClone(blankDefinition), name: "导入账户查询", description: "来自已解析的接口定义", input_schema: { type: "object", properties: { account_number: { type: "string" } }, required: ["account_number"] } };
const saved: Capability = { id: "imported", workspace_id: "ws", name: definition.name, description: definition.description, definition, revision: 1, status: "draft", latest_version: null, is_fixture: true };
const report: OpenAPIImport = { id: "parsed", document_id: "doc", document_revision: 1, parser_version: "test", valid: true, diagnostics: [], operations: [{ key: "GET /accounts", method: "GET", path: "/accounts", operation_id: "account", summary: "读取账户", source_pointer: "#/paths/~1accounts", supported: true, diagnostics: [], definition, facts: {} }] };
function fixture() {
  const api = new ApiClient("management");
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.endsWith("/catalogs")) return { integrations: [], credentials: [], resolvers: [] } as never;
    if (path.endsWith("/source-review")) return { revision: 1, ready: true, required: false, issues: [], citations: [] } as never;
    if (path.endsWith("/documents")) return [{ id: "doc", filename: "api.yaml", latest_revision: 1 }] as never;
    if (path.endsWith("/documents/doc/versions")) return [doc] as never;
    if (path.endsWith("/documents/doc/versions/1")) return doc as never;
    if (path.endsWith("/openapi-imports/parsed")) return report as never;
    if (path.endsWith("/ingest-jobs/parse")) return { job_id: "parse", status: "SUCCEEDED", result: { import_id: "parsed" }, error: null } as never;
    return [] as never;
  });
  const request = vi.spyOn(api, "request").mockResolvedValue({ job_id: "parse", status: "PENDING" });
  return { api, get, request };
}
async function selectDocument() {
  await screen.findByRole("option", { name: "api.yaml · 最新 r1" });
  fireEvent.change(screen.getByLabelText("来源文档"), { target: { value: "doc" } });
  fireEvent.click(await screen.findByRole("button", { name: "使用此文档修订" }));
}
async function parseOperation() {
  await selectDocument(); fireEvent.click(screen.getByRole("button", { name: "服务端解析此修订" }));
  fireEvent.change(await screen.findByLabelText("选择接口操作"), { target: { value: "GET /accounts" } });
}

it("holds upload busy through background polling and preserves dirty input when upload fails", async () => {
  const { api, get, request } = fixture(), busy = vi.fn(), dirty = vi.fn();
  let fail!: (reason: Error) => void;
  request.mockResolvedValue({ job_id: "upload", status: "PENDING" });
  const original = get.getMockImplementation()!;
  get.mockImplementation((path, signal) => path.endsWith("/ingest-jobs/upload") ? new Promise((_, reject) => { fail = reject; }) : original(path, signal));
  render(<OpenAPIWizard api={api} workspace="ws" writable onCreated={vi.fn()} onBusyChange={busy} onDirtyChange={dirty} />);
  fireEvent.change(screen.getByLabelText("上传资料文件"), { target: { files: [new File(["synthetic"], "new.yaml")] } });
  await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(true));
  fireEvent.click(screen.getByRole("button", { name: "上传为新文档" }));
  await waitFor(() => expect(fail).toBeDefined());
  expect(busy).toHaveBeenLastCalledWith(true);
  await act(async () => { fail(new Error("上传状态读取失败")); });
  await screen.findByText(/上传状态读取失败/);
  expect(busy).toHaveBeenLastCalledWith(false);
  expect(dirty).toHaveBeenLastCalledWith(true);
});

it("tracks parse, draft creation and its parent handoff as busy without losing edits", async () => {
  const { api, request } = fixture(), busy = vi.fn(), dirty = vi.fn();
  let parsed!: (value: unknown) => void, created!: (value: unknown) => void, handedOff!: () => void;
  request.mockImplementation(() => new Promise(resolve => { parsed = resolve; }));
  vi.spyOn(api, "post").mockImplementation(() => new Promise(resolve => { created = resolve; }));
  const onCreated = vi.fn(() => new Promise<void>(resolve => { handedOff = resolve; }));
  render(<OpenAPIWizard api={api} workspace="ws" writable onCreated={onCreated} onBusyChange={busy} onDirtyChange={dirty} />);
  await selectDocument(); expect(dirty).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByRole("button", { name: "服务端解析此修订" }));
  await waitFor(() => expect(busy).toHaveBeenLastCalledWith(true));
  await act(async () => { parsed({ job_id: "parse", status: "PENDING" }); });
  fireEvent.change(await screen.findByLabelText("选择接口操作"), { target: { value: "GET /accounts" } });
  await waitFor(() => expect(busy).toHaveBeenLastCalledWith(false));
  fireEvent.change(screen.getByLabelText("导入功能名称"), { target: { value: "导入后的名称" } });
  expect(dirty).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByRole("button", { name: "生成待审查功能草稿" }));
  expect(busy).toHaveBeenLastCalledWith(true);
  await act(async () => { created(saved); });
  expect(onCreated).toHaveBeenCalledWith(saved); expect(busy).toHaveBeenLastCalledWith(true);
  await act(async () => { handedOff(); });
  expect(busy).toHaveBeenLastCalledWith(false); expect(dirty).toHaveBeenLastCalledWith(false);
});

it("adopts the imported definition in the same function form and preserves its Schema", async () => {
  const { api } = fixture(); vi.spyOn(api, "post").mockResolvedValue(saved);
  const onSaved = vi.fn().mockResolvedValue(undefined);
  render(<CreationFlow api={api} workspace="ws" session={{ key: 1, kind: "function", origin: "/spaces/ws/functions", expanded: false }} functions={[]} admin publishable onStay={vi.fn()} onClose={vi.fn()} onAgent={vi.fn()} onDirtyChange={vi.fn()} onBusyChange={vi.fn()} onExpand={vi.fn()} onRoute={vi.fn()} onSaved={onSaved} />);
  fireEvent.click(screen.getByRole("button", { name: "从 OpenAPI 接口资料导入" }));
  await parseOperation(); fireEvent.click(screen.getByRole("button", { name: "生成待审查功能草稿" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(screen.getByLabelText("功能名称")).toHaveValue(definition.name);
  expect(screen.getByLabelText("业务描述")).toHaveValue(definition.description);
  expect(JSON.parse((screen.getByLabelText("FunctionDefinition") as HTMLTextAreaElement).value).input_schema).toEqual(definition.input_schema);
});
