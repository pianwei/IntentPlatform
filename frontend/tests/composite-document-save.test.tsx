import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../shared/api";
import type { Capability, FunctionDefinition } from "../shared/types";
import { blankDefinition } from "../console/src/FunctionEditor";
import { CompositeRuleEditor } from "../console/src/CompositeRuleEditor";

afterEach(() => cleanup());
const functionDefinition: FunctionDefinition = { ...structuredClone(blankDefinition), name: "客户资料概览", description: "按获准资料返回客户概览", object_type: "business:Customer", action: "READ", execution_kind: "composite",
  input_schema: { type: "object", properties: { customer_id: { type: "string" } } },
  composite: { mode: "document", skill_version_id: "skill-v1", dependencies: [{ function_id: "child", version: 1 }], final_output_mapping: {} },
};
const child: Capability = { id: "child", workspace_id: "ws", name: "新草稿名称", description: "新草稿", latest_version: 2, revision: 3, status: "published", is_fixture: true,
  definition: { ...structuredClone(blankDefinition), execution_kind: "direct", effect: "WRITE", requires_confirmation: true },
};
function documentDefinition(document: string) {
  return { key: "function.overview", name: functionDefinition.name, description: functionDefinition.description, mode: "document" as const, document,
    input_fields: ["customer_id"], applicable_entities: ["business:Customer"], applicable_actions: ["READ"], triggers: [], steps: [] };
}
function editor() {
  const api = new ApiClient("management"), update = vi.fn(), pending = vi.fn();
  const state = {
    skill: { id: "skill", revision: 1, latest_version: 1, disabled: false, definition: documentDefinition("说明 A") },
    versions: [{ id: "skill-v1", skill_id: "skill", version: 1, definition: documentDefinition("说明 A") }],
    afterPublish: (() => {}) as () => void,
    versionReadError: (() => false) as () => boolean,
  };
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.endsWith("/functions/child/versions")) return [
      { id: "child-v2", function_id: "child", version: 2, definition: { ...child.definition, name: "已发布写入", effect: "WRITE" } },
      { id: "child-v1", function_id: "child", version: 1, definition: { ...child.definition, name: "获准客户查询", effect: "READ_ONLY", requires_confirmation: false } },
    ] as never;
    if (path.endsWith("/business-skills")) return structuredClone([state.skill]) as never;
    if (path.endsWith("/business-skills/skill/versions")) {
      if (state.versionReadError()) throw new Error("版本读取失败");
      return structuredClone(state.versions) as never;
    }
    return [] as never;
  });
  const patch = vi.spyOn(api, "patch").mockImplementation(async (_path, body) => {
    const value = body as { revision: number; definition: ReturnType<typeof documentDefinition> };
    expect(value.revision).toBe(state.skill.revision);
    state.skill = { ...state.skill, revision: state.skill.revision + 1, definition: structuredClone(value.definition) };
    return structuredClone(state.skill) as never;
  });
  function publish() {
    state.skill.latest_version++;
    state.versions.unshift({ id: `skill-v${state.skill.latest_version}`, skill_id: "skill", version: state.skill.latest_version, definition: structuredClone(state.skill.definition) });
    const response = structuredClone(state.skill); state.afterPublish(); return response;
  }
  const post = vi.spyOn(api, "post").mockImplementation(async (path, body) => {
    expect(path).toBe("/api/v2/management/workspaces/ws/business-skills/skill/publish");
    expect(body).toEqual({ revision: state.skill.revision }); return publish() as never;
  });
  function mount() { return render(<CompositeRuleEditor api={api} workspace="ws" definition={functionDefinition} functions={[child]} writable publishable update={update} onPending={pending} />); }
  async function edit(document = "说明 B") { await waitFor(() => expect(screen.getByLabelText("Skill 说明")).toHaveValue("说明 A")); fireEvent.change(screen.getByLabelText("Skill 说明"), { target: { value: document } }); }
  function save() { fireEvent.click(screen.getByRole("button", { name: "应用组合配置" })); }
  return { state, api, get, patch, post, update, pending, mount, edit, save, publish };
}

it("publishes the edited document on retry after the draft save succeeds but publication fails", async () => {
  const e = editor(); let attempts = 0;
  e.post.mockImplementation(async () => { if (++attempts === 1) throw new Error("发布失败"); return e.publish() as never; });
  e.mount(); await e.edit(); e.save();
  expect(await screen.findByText("发布失败")).toBeVisible(); expect(screen.getByLabelText("Skill 说明")).toHaveValue("说明 B");
  expect(e.update).not.toHaveBeenCalled(); expect(e.pending).toHaveBeenLastCalledWith(true); expect(e.state.skill.definition.document).toBe("说明 B");
  e.save(); await waitFor(() => expect(e.update).toHaveBeenCalledWith(expect.objectContaining({ effect: "READ_ONLY", requires_confirmation: false, composite: { mode: "document", skill_version_id: "skill-v2", dependencies: [{ function_id: "child", version: 1 }], final_output_mapping: {} } })));
  expect(e.patch).toHaveBeenCalledTimes(1); expect(e.post).toHaveBeenCalledTimes(2); expect(e.state.versions[0].definition.document).toBe("说明 B");
});

it("checks the immutable published document even if the current draft already matches the editor", async () => {
  const e = editor(); e.state.skill = { ...e.state.skill, revision: 2, definition: documentDefinition("说明 B") };
  e.mount(); await e.edit(); e.save();
  await waitFor(() => expect(e.update).toHaveBeenCalledWith(expect.objectContaining({ composite: expect.objectContaining({ skill_version_id: "skill-v2" }) })));
  expect(e.patch).not.toHaveBeenCalled(); expect(e.post).toHaveBeenCalledOnce(); expect(e.state.versions[0].definition.document).toBe("说明 B");
});

it("retains an unchanged pinned document and dependency even when a later document and child are available", async () => {
  const e = editor(); e.state.skill = { ...e.state.skill, revision: 2, latest_version: 2, definition: documentDefinition("其他作者说明") };
  e.state.versions.unshift({ id: "skill-v2", skill_id: "skill", version: 2, definition: documentDefinition("其他作者说明") });
  e.mount(); await waitFor(() => expect(screen.getByLabelText("Skill 说明")).toHaveValue("说明 A")); e.save();
  await waitFor(() => expect(e.update).toHaveBeenCalledWith(expect.objectContaining({ composite: { mode: "document", skill_version_id: "skill-v1", dependencies: [{ function_id: "child", version: 1 }], final_output_mapping: {} }, effect: "READ_ONLY" })));
  expect(e.patch).not.toHaveBeenCalled(); expect(e.post).not.toHaveBeenCalled();
});

it("binds the exact version returned by this publication instead of a later concurrent publication", async () => {
  const e = editor(); e.state.afterPublish = () => {
    e.state.skill = { ...e.state.skill, revision: 3, latest_version: 3, definition: documentDefinition("其他作者说明") };
    e.state.versions.unshift({ id: "skill-v3", skill_id: "skill", version: 3, definition: documentDefinition("其他作者说明") });
  };
  e.mount(); await e.edit(); e.save();
  await waitFor(() => expect(e.update).toHaveBeenCalledWith(expect.objectContaining({ composite: expect.objectContaining({ skill_version_id: "skill-v2" }) })));
  expect(e.state.versions[0].id).toBe("skill-v3"); expect(e.post).toHaveBeenCalledOnce();
});

it("recovers a successful publication after a version-read failure without creating another publication", async () => {
  const e = editor(); e.state.versionReadError = () => e.state.skill.latest_version === 2;
  e.mount(); await e.edit(); e.save();
  expect(await screen.findByText("版本读取失败")).toBeVisible(); expect(e.update).not.toHaveBeenCalled(); expect(screen.getByLabelText("Skill 说明")).toHaveValue("说明 B");
  e.state.versionReadError = () => false; e.save();
  await waitFor(() => expect(e.update).toHaveBeenCalledWith(expect.objectContaining({ composite: expect.objectContaining({ skill_version_id: "skill-v2" }) })));
  expect(e.post).toHaveBeenCalledOnce(); expect(e.patch).toHaveBeenCalledOnce();
});

it("recovers a lost publication response from immutable server evidence without publishing again", async () => {
  const e = editor(); e.post.mockImplementation(async () => { e.publish(); throw new Error("响应中断"); });
  e.mount(); await e.edit(); e.save(); expect(await screen.findByText("响应中断")).toBeVisible(); expect(e.update).not.toHaveBeenCalled();
  e.save(); await waitFor(() => expect(e.update).toHaveBeenCalledWith(expect.objectContaining({ composite: expect.objectContaining({ skill_version_id: "skill-v2" }) })));
  expect(e.post).toHaveBeenCalledOnce(); expect(e.patch).toHaveBeenCalledOnce();
});

it("does not substitute another version when the confirmed publication is missing", async () => {
  const e = editor(); e.state.afterPublish = () => { e.state.versions = [{ id: "skill-v3", skill_id: "skill", version: 3, definition: documentDefinition("说明 B") }]; };
  e.mount(); await e.edit(); e.save();
  expect(await screen.findByText(/本次发布的文档尚未核对/)).toBeVisible(); expect(e.update).not.toHaveBeenCalled(); expect(screen.getByLabelText("Skill 说明")).toHaveValue("说明 B"); expect(e.pending).toHaveBeenLastCalledWith(true);
});

it("does not save a confirmed version whose document differs from the current desired content", async () => {
  const e = editor(); e.state.afterPublish = () => { e.state.versions[0].definition = documentDefinition("其他说明"); };
  e.mount(); await e.edit(); e.save();
  expect(await screen.findByText(/本次发布的文档尚未核对/)).toBeVisible(); expect(e.update).not.toHaveBeenCalled(); expect(e.pending).toHaveBeenLastCalledWith(true);
});

it("preserves edited input and rejects a concurrent draft revision instead of overwriting it on retry", async () => {
  const e = editor(); e.post.mockRejectedValue(new Error("发布失败")); e.mount(); await e.edit(); e.save(); await screen.findByText("发布失败");
  e.state.skill = { ...e.state.skill, revision: 3, definition: documentDefinition("其他作者说明") }; e.save();
  expect(await screen.findByText(/组合说明已被修改或不可用/)).toBeVisible(); expect(screen.getByLabelText("Skill 说明")).toHaveValue("说明 B");
  expect(e.update).not.toHaveBeenCalled(); expect(e.patch).toHaveBeenCalledOnce(); expect(e.post).toHaveBeenCalledOnce();
});
