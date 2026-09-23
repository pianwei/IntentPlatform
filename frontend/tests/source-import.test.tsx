import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../shared/api";
import { DocumentLibrary } from "../console/src/DocumentLibrary";
import { OpenAPIWizard } from "../console/src/OpenAPIWizard";
import { HttpMappingEditor } from "../console/src/HttpMappingEditor";
import { SourceReviewPanel } from "../console/src/SourceReviewPanel";
import { FunctionEditor, blankDefinition } from "../console/src/FunctionEditor";
import type { SourceDocument, SourceReview, OpenAPIImport } from "../shared/sources";
import type { Capability, FunctionDefinition } from "../shared/types";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const document: SourceDocument = { id: "doc", filename: "source.yaml", revision: 2, text: "query + body source", sections: [{ id: "section", heading: "业务说明", start_line: 1, end_line: 1, text: "query + body source" }], original_available: true, source: { document_id: "doc", revision: 2 }, fixture_mode: false };
const legacy = { ...document, revision: 1, original_available: false };
const definition: FunctionDefinition = { ...structuredClone(blankDefinition), name: "导入测试", description: "合成接口", object_type: "Record", result_type: "Detail", connector: { ...blankDefinition.connector, version: "2", endpoint: "https://bank.example/items/{id}", method: "POST", http_mapping: { path: { id: "path_id" }, query: { item: "query_item" }, headers: {}, body: { item: "body_item" } } }, input_schema: { type: "object", properties: { path_id: { type: "string" }, query_item: { type: "string" }, body_item: { type: "string" } } } };
const capability: Capability = { id: "f", workspace_id: "ws", revision: 3, status: "draft", name: definition.name, description: definition.description, definition, latest_version: null, is_fixture: false };
const readyReview: SourceReview = { revision: 3, required: true, ready: true, issues: [], api_source: { document_id: "doc", revision: 2, import_id: "import", operation_key: "POST /items/{id}", pointer: "/paths/items", parser_version: "test" }, api_facts: {}, citations: [] };
it("does not invalidate source review when an edited candidate only changes object key order", async () => {
  const api = new ApiClient("management");
  const review: SourceReview = { ...readyReview, citations: [{ field_path: "presentation", document_id: "doc", revision: 2, section_id: "section", quote: "query + body source", proposed_value: { title: "结果", field_map: { name: "名称", id: "编号" } }, claimed_by: "reviewer" }] };
  vi.spyOn(api, "get").mockImplementation(async path => (path.endsWith("/source-review") ? review : path.endsWith("/documents") ? [] : document) as never);
  const onReview = vi.fn();
  render(<SourceReviewPanel api={api} workspace="ws" item={capability} writable dirty={false} busy={false} onReview={onReview} onChanged={async () => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑引用 1" }));
  const raw = await screen.findByLabelText("候选解释值（JSON，字符串需双引号）");
  fireEvent.change(raw, { target: { value: '{"field_map":{"id":"编号","name":"名称"},"title":"结果"}' } });
  await waitFor(() => expect(screen.getByRole("button", { name: "更新待保存引用" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "更新待保存引用" }));
  expect(screen.getByRole("button", { name: "保存完整引用列表" })).toBeDisabled();
  expect(onReview).toHaveBeenLastCalledWith(review);
});
function documentsGet(api: ApiClient) { return vi.spyOn(api, "get").mockImplementation(async path => (path.endsWith("/documents") ? [{ id: "doc", filename: "source.yaml", latest_revision: 2 }] : path.endsWith("/versions") ? [document, legacy] : document) as never); }
it("pins old document text while uploading a new revision against the latest revision; stale failure retains file", async () => {
  const api = new ApiClient("management"); documentsGet(api); const request = vi.spyOn(api, "request").mockRejectedValue(new ApiError(409, "Document revision changed"));
  render(<DocumentLibrary api={api} workspace="ws" writable />);
  await screen.findByRole("option", { name: "source.yaml · 最新 r2" }); fireEvent.change(screen.getByLabelText("来源文档"), { target: { value: "doc" } });
  await screen.findByRole("option", { name: "r1 · source.yaml" }); fireEvent.change(screen.getByLabelText("原文修订"), { target: { value: "1" } });
  expect(screen.getByRole("button", { name: "下载原始文件" })).toBeDisabled();
  const file = new File(["new text"], "new.md"); fireEvent.change(screen.getByLabelText("上传资料文件"), { target: { files: [file] } }); fireEvent.click(screen.getByLabelText(/为所选文档追加修订/)); fireEvent.click(screen.getByRole("button", { name: "上传新修订" }));
  await screen.findByText(/Document revision changed/); const body = request.mock.calls[0][1]?.body as FormData;
  expect(request.mock.calls[0][0]).toBe("/api/v2/management/workspaces/ws/documents/doc/versions"); expect(body.get("expected_revision")).toBe("2"); expect(body.get("file")).toBe(file); expect(screen.getByRole("button", { name: "上传新修订" })).toBeEnabled();
});
it("parses the selected immutable document on the server and creates only a supported operation", async () => {
  const api = new ApiClient("management"); documentsGet(api);
  const report: OpenAPIImport = { id: "import", document_id: "doc", document_revision: 1, parser_version: "finite-test", valid: true, diagnostics: [], operations: [{ key: "POST /items/{id}", method: "POST", path: "/items/{id}", operation_id: "test", summary: "合成接口", source_pointer: "/paths/items", supported: true, diagnostics: [], definition, facts: {} }, { key: "DELETE /items", method: "DELETE", path: "/items", operation_id: null, summary: "不支持操作", source_pointer: "/paths/items/delete", supported: false, diagnostics: [{ code: "METHOD", message: "Only GET/POST", pointer: "/paths/items/delete", severity: "error" }], definition: null, facts: {} }] };
  const post = vi.spyOn(api, "post").mockImplementation(async path => (path.endsWith("/openapi-imports") ? report : capability) as never); const created = vi.fn().mockResolvedValue(undefined);
  render(<OpenAPIWizard api={api} workspace="ws" writable onCreated={created} />);
  await screen.findByRole("option", { name: "source.yaml · 最新 r2" }); fireEvent.change(screen.getByLabelText("来源文档"), { target: { value: "doc" } }); await screen.findByRole("option", { name: "r1 · source.yaml" }); fireEvent.change(screen.getByLabelText("原文修订"), { target: { value: "1" } }); fireEvent.click(screen.getByRole("button", { name: "使用此文档修订" })); fireEvent.click(screen.getByRole("button", { name: "服务端解析此修订" }));
  await screen.findByLabelText("选择接口操作"); expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/openapi-imports", { document_id: "doc", revision: 1 }); expect(screen.getByRole("option", { name: /DELETE/ })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("选择接口操作"), { target: { value: "POST /items/{id}" } }); fireEvent.click(screen.getByRole("button", { name: "生成待审查功能草稿" }));
  await waitFor(() => expect(created).toHaveBeenCalledWith(capability)); expect(post.mock.calls[1]).toEqual(["/api/v2/management/workspaces/ws/openapi-imports/import/functions", { operation_key: "POST /items/{id}", name: "导入测试", description: "合成接口" }]);
});
it("keeps colliding query/body names as separate positions and refuses an overwrite on moving rows", () => {
  let current = definition;
  function Harness() { const [d, setD] = useState(definition); current = d; return <HttpMappingEditor definition={d} update={patch => setD({ ...d, ...patch })} />; }
  render(<Harness />); const locations = screen.getAllByLabelText("参数位置"); fireEvent.change(locations[2], { target: { value: "query" } });
  expect(screen.getByText(/未覆盖原映射/)).toBeInTheDocument(); expect(current.connector.http_mapping?.query).toEqual({ item: "query_item" }); expect(current.connector.http_mapping?.body).toEqual({ item: "body_item" });
  fireEvent.change(screen.getAllByLabelText("接口字段名")[2], { target: { value: "payload" } }); expect(current.connector.http_mapping?.body).toEqual({ payload: "body_item" }); expect(current.connector.http_mapping?.query).toEqual({ item: "query_item" });
  expect(screen.getByLabelText("业务失败 HTTP 404")).not.toBeChecked();
  fireEvent.click(screen.getByLabelText("业务失败 HTTP 404"));
  fireEvent.click(screen.getByLabelText("必须发送 JSON body（即使空对象）"));
  expect(current.connector).toMatchObject({ http_failure_statuses: [404], http_body_required: true });
  expect(current.connector.http_mapping?.body).toEqual({ payload: "body_item" });
  expect(screen.getByRole("option", { name: "v1 · 原有连接器" })).toBeDisabled();
});
it("keeps the edited mapping row in place when a wire name changes", () => {
  const d = { ...definition, connector: { ...definition.connector, http_mapping: { ...definition.connector.http_mapping!, query: { first: "query_item", second: "body_item" } } } };
  function Harness() { const [value, setValue] = useState(d); return <HttpMappingEditor definition={value} update={patch => setValue({ ...value, ...patch } as typeof d)} />; }
  render(<Harness />); const fields = screen.getAllByLabelText("接口字段名");
  fireEvent.change(fields[1], { target: { value: "renamed" } });
  expect(screen.getAllByLabelText("接口字段名")[1]).toHaveValue("renamed");
  expect(screen.getAllByLabelText("接口字段名")[2]).toHaveValue("second");
});
it("defends the HTTP mapping component against malformed failure/status/body fields", () => {
  const update = vi.fn(); const { rerender } = render(<HttpMappingEditor definition={{ ...definition, connector: { ...definition.connector, http_failure_statuses: 42 } } as unknown as FunctionDefinition} update={update} />);
  expect(screen.getByText(/http_failure_statuses 必须/)).toBeInTheDocument();
  rerender(<HttpMappingEditor definition={{ ...definition, connector: { ...definition.connector, http_body_required: "false" } } as unknown as FunctionDefinition} update={update} />);
  expect(screen.getByText(/http_body_required 必须/)).toBeInTheDocument(); expect(update).not.toHaveBeenCalled();
});
it("preserves all unchanged citations on PUT and binds the edited source list to the current revision", async () => {
  const api = new ApiClient("management"); const first = { field_path: "description", document_id: "doc", revision: 1, section_id: "section", quote: "source", proposed_value: "A", claimed_by: "original-author" }; const second = { ...first, field_path: "name", proposed_value: "B" };
  const review: SourceReview = { ...readyReview, api_source: null, citations: [first, second] };
  vi.spyOn(api, "get").mockResolvedValue(review); const put = vi.spyOn(api, "put").mockResolvedValue({ ...review, revision: 4, citations: [first], ready: false }); const changed = vi.fn().mockResolvedValue(undefined); const onReview = vi.fn();
  render(<SourceReviewPanel api={api} workspace="ws" item={capability} writable dirty={false} busy={false} onReview={onReview} onChanged={changed} />);
  await screen.findByRole("button", { name: "移除引用 2" }); fireEvent.click(screen.getByRole("button", { name: "移除引用 2" })); expect(onReview).toHaveBeenLastCalledWith(null); fireEvent.click(screen.getByRole("button", { name: "保存完整引用列表" }));
  await waitFor(() => expect(put).toHaveBeenCalled()); expect(put.mock.calls[0]).toEqual(["/api/v2/management/workspaces/ws/functions/f/sources", { revision: 3, citations: [{ field_path: "description", document_id: "doc", revision: 1, section_id: "section", quote: "source", proposed_value: "A" }] }]); expect(changed.mock.calls[0][0]).toMatchObject({ revision: 4, status: "draft", definition });
});
it("retains a reason after stale confirmation and never substitutes candidate values into the draft", async () => {
  const api = new ApiClient("management"); vi.spyOn(api, "get").mockResolvedValue({ ...readyReview, ready: false, issues: [{ field_path: "effect", kind: "conflict", current_value: "READ_ONLY", candidates: [{ kind: "api", value: "WRITE", source_pointer: "/description", ref_targets: [] }], resolved: false, reason: null }] });
  const post = vi.spyOn(api, "post").mockRejectedValue(new ApiError(409, "Function revision changed")); const patch = vi.spyOn(api, "patch");
  render(<SourceReviewPanel api={api} workspace="ws" item={capability} writable dirty={false} busy={false} onReview={vi.fn()} onChanged={vi.fn()} />);
  await screen.findByLabelText("确认理由：effect"); fireEvent.change(screen.getByLabelText("确认理由：effect"), { target: { value: "已核对真实业务只读规则" } }); fireEvent.click(screen.getByRole("button", { name: "确认当前值：effect" }));
  await screen.findByText(/Function revision changed/); expect(screen.getByLabelText("确认理由：effect")).toHaveValue("已核对真实业务只读规则"); expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/functions/f/source-review/confirm", { revision: 3, resolutions: [{ field_path: "effect", reason: "已核对真实业务只读规则" }] }); expect(patch).not.toHaveBeenCalled();
});
it("requires an exact quote in the pinned section before adding and saving an attributed citation", async () => {
  const api = new ApiClient("management"); const review = { ...readyReview, api_source: null };
  vi.spyOn(api, "get").mockImplementation(async path => (path.endsWith("/source-review") ? review : path.endsWith("/documents") ? [{ id: "doc", filename: "source.yaml", latest_revision: 2 }] : [document]) as never);
  const put = vi.spyOn(api, "put").mockResolvedValue({ ...review, revision: 4 });
  render(<SourceReviewPanel api={api} workspace="ws" item={capability} writable dirty={false} busy={false} onReview={vi.fn()} onChanged={vi.fn().mockResolvedValue(undefined)} />);
  await screen.findByRole("button", { name: "添加文档引用" }); fireEvent.click(screen.getByRole("button", { name: "添加文档引用" })); await screen.findByRole("option", { name: "source.yaml · 最新 r2" }); fireEvent.change(screen.getByLabelText("来源文档"), { target: { value: "doc" } }); await screen.findByRole("button", { name: "使用此文档修订" }); fireEvent.click(screen.getByRole("button", { name: "使用此文档修订" }));
  fireEvent.change(screen.getByLabelText("原文章节"), { target: { value: "section" } }); fireEvent.change(screen.getByLabelText("逐字原文摘录"), { target: { value: "missing words" } }); fireEvent.click(screen.getByRole("button", { name: "加入待保存引用" })); expect(screen.getByText(/引用摘录必须逐字/)).toBeInTheDocument(); expect(screen.getByRole("button", { name: "保存完整引用列表" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("逐字原文摘录"), { target: { value: "body source" } }); fireEvent.change(screen.getByLabelText("候选解释值（JSON，字符串需双引号）"), { target: { value: '"业务解释"' } }); fireEvent.click(screen.getByRole("button", { name: "加入待保存引用" })); fireEvent.click(screen.getByRole("button", { name: "保存完整引用列表" }));
  await waitFor(() => expect(put).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/functions/f/sources", { revision: 3, citations: [{ field_path: "description", document_id: "doc", revision: 2, section_id: "section", quote: "body source", proposed_value: "业务解释" }] }));
});
it("guards imported write probes and sends explicit success/failure parameters and revision before publish", async () => {
  const api = new ApiClient("management"); vi.spyOn(api, "get").mockResolvedValue(readyReview); const post = vi.spyOn(api, "post").mockResolvedValue({ valid: true, revision: 3, errors: [], checks: {}, probe: { status: "SUCCEEDED" } });
  render(<FunctionEditor api={api} workspace="ws" item={{ ...capability, definition: { ...definition, effect: "WRITE", requires_confirmation: true } }} writable onClose={vi.fn()} onSaved={async () => {}} />);
  await screen.findByLabelText("预期失败样例参数（JSON）"); fireEvent.click(screen.getByLabelText(/调用真实测试接口/)); expect(screen.getByRole("button", { name: "验证当前草稿" })).toBeDisabled(); expect(screen.getByRole("button", { name: "发布功能版本" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("契约测试参数（JSON）"), { target: { value: '{"case":"ok"}' } }); fireEvent.change(screen.getByLabelText("预期失败样例参数（JSON）"), { target: { value: '{"case":"failed"}' } }); fireEvent.click(screen.getByLabelText(/确认本次测试允许/)); fireEvent.click(screen.getByRole("button", { name: "验证当前草稿" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/functions/f/validate", { revision: 3, arguments: { case: "ok" }, failure_arguments: { case: "failed" }, probe: true, allow_effects: true })); await waitFor(() => expect(screen.getByRole("button", { name: "发布功能版本" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "发布功能版本" })); await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/functions/f/publish", { revision: 3 }));
});
it("allows only one draft kind at a time without locking both saves or losing pending citations", async () => {
  const api = new ApiClient("management");
  const citation = { field_path: "description", document_id: "doc", revision: 1, section_id: "section", quote: "source", proposed_value: "说明" };
  const review = { ...readyReview, api_source: null, ready: false, citations: [citation], issues: [{ field_path: "description", kind: "review", current_value: "合成接口", candidates: [], resolved: false, reason: null }] };
  vi.spyOn(api, "get").mockResolvedValue(review);
  let rejectConfirm!: (value: unknown) => void;
  const pending = new Promise((_, reject) => { rejectConfirm = reject; }); vi.spyOn(api, "post").mockReturnValue(pending);
  render(<FunctionEditor api={api} workspace="ws" item={capability} writable onClose={vi.fn()} onSaved={async () => {}} />);
  await screen.findByRole("button", { name: "移除引用 1" });
  fireEvent.change(screen.getByLabelText("功能名称"), { target: { value: "定义未保存" } });
  expect(screen.getByRole("button", { name: "添加文档引用" })).toBeDisabled(); expect(screen.getByRole("button", { name: "保存新草稿" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("功能名称"), { target: { value: definition.name } });
  fireEvent.click(screen.getByRole("button", { name: "移除引用 1" }));
  expect(screen.getByLabelText("功能名称")).toBeDisabled(); expect(screen.getByRole("button", { name: "保存新草稿" })).toBeDisabled(); expect(screen.getByRole("button", { name: "保存完整引用列表" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "还原引用修改" })); expect(screen.getByLabelText("功能名称")).toBeEnabled(); expect(screen.getByRole("button", { name: "移除引用 1" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("确认理由：description"), { target: { value: "核对现有说明" } }); fireEvent.click(screen.getByRole("button", { name: "确认当前值：description" }));
  expect(screen.getByLabelText("功能名称")).toBeDisabled();
  rejectConfirm(new ApiError(409, "source changed")); await screen.findByText(/source changed/);
  expect(screen.getByLabelText("功能名称")).toBeEnabled(); expect(screen.getByLabelText("确认理由：description")).toHaveValue("核对现有说明");
});
