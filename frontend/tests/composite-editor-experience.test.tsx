import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../shared/api";
import type { Capability, FunctionDefinition, PublishedFunction } from "../shared/types";
import { blankDefinition } from "../console/src/FunctionEditor";
import { CompositeRuleEditor, type CompositeCommit } from "../console/src/CompositeRuleEditor";
import { makeSkillDocumentTemplate, skillDocumentTemplates } from "../console/src/skillDocumentTemplates";

afterEach(() => cleanup());

function fixture({ document = "维护者自定义规则", selected = ["child-0"], writable = true, unavailable = false } = {}) {
  const api = new ApiClient("management"), update = vi.fn(), pending = vi.fn();
  const functions: Capability[] = Array.from({ length: 11 }, (_, index) => ({ id: `child-${index}`, workspace_id: "ws", name: `未发布草稿 ${index}`, description: "草稿不应进入选择器", revision: 3, latest_version: index === 0 ? 2 : 1, status: unavailable && index === 0 ? "disabled" : "published", is_fixture: true,
    definition: { ...structuredClone(blankDefinition), name: `未发布草稿 ${index}`, execution_kind: "direct" },
  }));
  const versions: PublishedFunction[] = functions.flatMap((item, index) => [{ id: `${item.id}-v1`, function_id: item.id, version: 1,
    definition: { ...item.definition, name: index === 0 ? "客户资料查询" : index === 1 ? "创建核查任务" : `资料查询 ${index}`, description: index === 0 ? "返回获准的客户资料" : `已发布用途 ${index}`, effect: index === 1 ? "WRITE" as const : "READ_ONLY" as const, requires_confirmation: index === 1 },
  }, ...(index === 0 ? [{ id: `${item.id}-v2`, function_id: item.id, version: 2, definition: { ...item.definition, name: "客户资料查询新版", description: "新版会办理写入", effect: "WRITE" as const, requires_confirmation: true } }] : [])]);
  const definition: FunctionDefinition = { ...structuredClone(blankDefinition), name: "客户资料概览", description: "收集并汇总获准客户资料", object_type: "business:Customer", action: "READ", execution_kind: "composite",
    input_schema: { type: "object", properties: { customer_id: { type: "string" } } },
    composite: { mode: "document", skill_version_id: "skill-v1", dependencies: selected.map(id => ({ function_id: id, version: 1 })), final_output_mapping: {} },
  };
  const skillDefinition = { key: "function.overview", name: definition.name, description: definition.description, mode: "document", document, input_fields: ["customer_id"], applicable_entities: ["business:Customer"], applicable_actions: ["READ"], triggers: [], steps: [] };
  vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("/business-skills") ? [{ id: "skill", revision: 1, latest_version: 1, disabled: false, definition: skillDefinition }] as never : path.endsWith("/business-skills/skill/versions") ? [{ id: "skill-v1", skill_id: "skill", version: 1, definition: skillDefinition }] as never : versions.filter(version => path.endsWith(`/functions/${version.function_id}/versions`)) as never);
  const post = vi.spyOn(api, "post"), patch = vi.spyOn(api, "patch");
  let commit: CompositeCommit | null = null;
  render(<CompositeRuleEditor api={api} workspace="ws" definition={definition} functions={functions} writable={writable} publishable update={update} onPending={pending} onCommit={value => { commit = value; }} />);
  const ready = async () => { await waitFor(() => expect(screen.getByLabelText("Skill 说明")).toHaveValue(document)); await waitFor(() => expect(screen.queryByText("正在读取已发布子功能…")).toBeNull()); };
  return { api, update, pending, post, patch, ready, definition, commit: () => commit!() };
}

it("does not label existing dependencies unavailable while their published versions are loading", async () => {
  const e = fixture();
  expect(screen.getByText("正在读取已发布子功能…")).toBeVisible();
  expect(screen.queryByText("暂不可用，请移除或恢复此依赖后再保存。")).toBeNull();
  await e.ready();
  expect(within(screen.getByLabelText("已选子功能")).getByText("客户资料查询")).toBeVisible();
});

it("shows only selected fixed-version children and keeps the available catalog out of the default view", async () => {
  const e = fixture(); await e.ready();
  expect(within(screen.getByLabelText("已选子功能")).getByText("客户资料查询")).toBeVisible();
  expect(screen.getByText("v1")).toBeVisible(); expect(screen.queryByText("创建核查任务")).toBeNull(); expect(screen.queryByRole("checkbox")).toBeNull(); expect(screen.queryByLabelText("搜索组合子功能")).toBeNull();
  expect(e.pending).toHaveBeenLastCalledWith(false); expect(e.post).not.toHaveBeenCalled(); expect(e.patch).not.toHaveBeenCalled();
});

it("stages checkbox changes and discards them on cancel without changing the parent's effect or pending state", async () => {
  const e = fixture(); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "添加子功能" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "组合子功能 创建核查任务" })); expect(e.update).not.toHaveBeenCalled(); expect(e.pending).toHaveBeenLastCalledWith(false);
  fireEvent.click(screen.getByRole("button", { name: "取消选择" })); expect(screen.queryByText("创建核查任务")).toBeNull(); expect(screen.getByLabelText("Skill 说明")).toHaveValue("维护者自定义规则");
  fireEvent.click(screen.getByRole("button", { name: "添加子功能" })); expect(screen.getByRole("checkbox", { name: "组合子功能 创建核查任务" })).not.toBeChecked(); expect(screen.getByRole("checkbox", { name: "组合子功能 客户资料查询" })).toBeChecked();
});

it("applies confirmed children and derives write confirmation without replacing an existing pinned version", async () => {
  const e = fixture(); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "添加子功能" })); fireEvent.click(screen.getByRole("checkbox", { name: "组合子功能 创建核查任务" })); fireEvent.click(screen.getByRole("button", { name: "确认选择" }));
  expect(e.update).toHaveBeenCalledWith({ effect: "WRITE", requires_confirmation: true }); expect(e.pending).toHaveBeenLastCalledWith(true); expect(screen.getAllByText("v1")).toHaveLength(2);
  const result = await e.commit(); expect(result.composite?.dependencies).toEqual([{ function_id: "child-0", version: 1 }, { function_id: "child-1", version: 1 }]); expect(e.post).not.toHaveBeenCalled();
});

it("bounds the picker to six rows and retains staged choices across search and pagination", async () => {
  const e = fixture(); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "添加子功能" })); expect(screen.getAllByRole("checkbox")).toHaveLength(6);
  fireEvent.click(screen.getByRole("button", { name: "下一页" })); fireEvent.click(screen.getByRole("checkbox", { name: "组合子功能 资料查询 10" }));
  fireEvent.change(screen.getByLabelText("搜索组合子功能"), { target: { value: "客户资料" } }); expect(screen.getAllByRole("checkbox")).toHaveLength(1); expect(screen.queryByText("未发布草稿 0")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "确认选择" })); expect(within(screen.getByLabelText("已选子功能")).getByText("资料查询 10")).toBeVisible();
});

it("does not mark an unchanged confirmed selection as an edit", async () => {
  const e = fixture(); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "添加子功能" })); fireEvent.click(screen.getByRole("button", { name: "确认选择" }));
  expect(e.update).not.toHaveBeenCalled(); expect(e.pending).toHaveBeenLastCalledWith(false);
});

it("closes the selection view with Escape, discards staged changes and returns focus", async () => {
  const e = fixture(); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "添加子功能" })); fireEvent.click(screen.getByRole("checkbox", { name: "组合子功能 创建核查任务" }));
  fireEvent.keyDown(screen.getByLabelText("搜索组合子功能"), { key: "Escape" }); expect(screen.queryByLabelText("搜索组合子功能")).toBeNull(); expect(screen.getByRole("button", { name: "添加子功能" })).toHaveFocus(); expect(e.update).not.toHaveBeenCalled();
});

it("keeps an unavailable dependency visible and rejects saving until it is explicitly removed", async () => {
  const e = fixture({ unavailable: true }); await e.ready(); expect(screen.getByText("暂不可用，请移除或恢复此依赖后再保存。")).toBeVisible();
  await expect(e.commit()).rejects.toThrow("所选子功能不可用"); expect(e.post).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "移除不可用子功能 未发布草稿 0" })); expect(e.update).toHaveBeenCalledWith({ effect: "READ_ONLY", requires_confirmation: false }); expect(screen.getByText("还没有子功能，先添加，再编写办理规则。")).toBeVisible();
});

it("updates a pinned child only through the explicit update action", async () => {
  const e = fixture(); await e.ready(); expect(e.update).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: "更新子功能" }));
  expect(screen.getByText("客户资料查询新版")).toBeVisible(); expect(screen.getByText("v2")).toBeVisible(); expect(e.update).toHaveBeenCalledWith({ effect: "WRITE", requires_confirmation: true });
});

it("previews templates without changing custom text and cancellation leaves the draft untouched", async () => {
  const e = fixture(); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "使用模板" })); expect(screen.getAllByRole("button", { pressed: true })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: /对比分析/ })); expect(within(screen.getByLabelText("模板预览")).getByText("客户资料概览")).toBeVisible(); expect(e.pending).toHaveBeenLastCalledWith(false); expect(e.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "取消模板" })); expect(screen.getByLabelText("Skill 说明")).toHaveValue("维护者自定义规则"); expect(e.pending).toHaveBeenLastCalledWith(false); expect(screen.getByRole("button", { name: "使用模板" })).toHaveFocus();
});

it("fills an empty document from the selected template using fixed child names and real input names", async () => {
  const e = fixture({ document: "" }); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "使用模板" })); fireEvent.click(screen.getByRole("button", { name: /资料草稿/ })); fireEvent.click(screen.getByRole("button", { name: "使用此模板" }));
  const text = (screen.getByLabelText("Skill 说明") as HTMLTextAreaElement).value; expect(text).toContain("客户资料查询"); expect(text).toContain("customer_id"); expect(text).toContain("待补充"); expect(text).not.toContain("child-0"); expect(text).not.toContain("客户资料查询新版"); expect(e.pending).toHaveBeenLastCalledWith(true); expect(e.post).not.toHaveBeenCalled();
});

it("requires an explicit replace action for custom text", async () => {
  const e = fixture(); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "使用模板" })); expect(screen.queryByRole("button", { name: "使用此模板" })).toBeNull(); expect(screen.getByText("当前已有说明。选择替换或追加后才会修改。")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /确认办理/ })); fireEvent.click(screen.getByRole("button", { name: "替换当前说明" })); expect(screen.getByLabelText("Skill 说明")).not.toHaveValue("维护者自定义规则"); expect((screen.getByLabelText("Skill 说明") as HTMLTextAreaElement).value).toContain("取得确认后才执行"); expect(e.post).not.toHaveBeenCalled();
});

it("appends a template while preserving the existing custom document", async () => {
  const e = fixture(); await e.ready(); fireEvent.click(screen.getByRole("button", { name: "使用模板" })); fireEvent.click(screen.getByRole("button", { name: "追加到当前说明" }));
  expect((screen.getByLabelText("Skill 说明") as HTMLTextAreaElement).value).toMatch(/^维护者自定义规则\n\n## 适用目标/); expect(e.pending).toHaveBeenLastCalledWith(true);
});

it("switches between editing and safe Markdown preview without dirtying or losing input", async () => {
  const e = fixture({ document: "## 自定义目标\n\n按已授权资料回答。" }); await e.ready(); fireEvent.click(screen.getByRole("tab", { name: "预览" })); expect(within(screen.getByRole("tabpanel")).getByRole("heading", { name: "自定义目标" })).toBeVisible();
  expect(screen.queryByLabelText("Skill 说明")).toBeNull(); expect(e.pending).toHaveBeenLastCalledWith(false); fireEvent.click(screen.getByRole("tab", { name: "编辑" })); expect(screen.getByLabelText("Skill 说明")).toHaveValue("## 自定义目标\n\n按已授权资料回答。");
});

it("keeps read-only selection and templates disabled", async () => {
  const e = fixture({ writable: false }); await e.ready(); expect(screen.getByRole("button", { name: "添加子功能" })).toBeDisabled(); expect(screen.getByRole("button", { name: "使用模板" })).toBeDisabled(); expect(screen.getByLabelText("Skill 说明")).toBeDisabled(); expect(e.pending).toHaveBeenLastCalledWith(false);
});

it("supports document tab arrow keys and template Escape with focus return", async () => {
  const e = fixture(); await e.ready(); fireEvent.keyDown(screen.getByRole("tab", { name: "编辑" }), { key: "ArrowRight" }); expect(screen.getByRole("tab", { name: "预览" })).toHaveFocus(); expect(screen.getByRole("tab", { name: "预览" })).toHaveAttribute("tabindex", "0");
  fireEvent.keyDown(screen.getByRole("tab", { name: "预览" }), { key: "Home" }); expect(screen.getByRole("tab", { name: "编辑" })).toHaveFocus(); expect(screen.getByLabelText("Skill 说明")).toHaveValue("维护者自定义规则");
  fireEvent.click(screen.getByRole("button", { name: "使用模板" })); expect(screen.getByRole("button", { name: /查询汇总/ })).toHaveFocus(); fireEvent.keyDown(screen.getByRole("button", { name: /查询汇总/ }), { key: "Escape" }); expect(screen.getByRole("button", { name: "使用模板" })).toHaveFocus(); expect(e.pending).toHaveBeenLastCalledWith(false);
});

it("provides placeholders when template context is absent instead of inventing parameter paths", () => {
  const definition = { ...structuredClone(blankDefinition), name: "", description: "", input_schema: { type: "object", properties: {} } };
  expect(skillDocumentTemplates).toHaveLength(4);
  for (const template of skillDocumentTemplates) { const text = makeSkillDocumentTemplate(template.id, definition, []); expect(text).toContain("[填写组合功能名称]"); expect(text).toContain("[先添加已发布子功能"); expect(text).not.toContain("$step"); }
});
