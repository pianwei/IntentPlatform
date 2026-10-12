import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { blankDefinition } from "../console/src/FunctionEditor";
import { OutputMappingEditor } from "../console/src/OutputMappingEditor";

afterEach(() => { cleanup(); });

it("selects status-only output when there are no result fields", () => {
  const update = vi.fn();
  render(<OutputMappingEditor definition={blankDefinition} update={update} />);
  fireEvent.change(screen.getByLabelText("返回方式"), { target: { value: "none" } });
  expect(update).toHaveBeenCalledWith(expect.objectContaining({ output_mode: "none" }));
});

it("requires explicit confirmation before clearing fields or blocked composite mappings", () => {
  const update = vi.fn();
  const definition = { ...blankDefinition, output_schema: { type: "object", properties: { result: { type: "string", maxLength: 20 } }, required: ["result"], additionalProperties: false }, presentation: { ...blankDefinition.presentation, field_map: { 结果: "result" } } };
  render(<OutputMappingEditor definition={definition} update={update} />);
  fireEvent.change(screen.getByLabelText("返回方式"), { target: { value: "none" } });
  expect(screen.getByText("当前返回字段将从草稿中移除。已发布版本不受影响。")).toBeVisible();
  expect(update).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认仅返回执行状态" }));
  expect(update).toHaveBeenCalledWith(expect.objectContaining({ output_mode: "none", result_bindings: [] }));
});

it("lets a function select one approved Markdown string instead of summarizing its JSON wrapper", () => {
  const update = vi.fn();
  const definition = { ...blankDefinition,
    output_schema: { type: "object", properties: { brief: { type: "object", properties: {
      title: { type: "string" }, markdown: { type: "string" }, draft_only: { type: "boolean" },
    }, required: ["title", "markdown", "draft_only"], additionalProperties: false } }, required: ["brief"], additionalProperties: false },
    presentation: { ...blankDefinition.presentation, field_map: { "访前准备": "brief" } },
  } as typeof blankDefinition;
  render(<OutputMappingEditor definition={definition} update={update} />);
  fireEvent.change(screen.getByLabelText("用户答复方式"), { target: { value: "content" } });
  expect(update).toHaveBeenCalledWith(expect.objectContaining({ presentation: expect.objectContaining({
    response_mode: "content", content_path: "brief.markdown", component: "markdown",
  }) }));
});

it("preserves the inferred content mode when an older Markdown function is edited", () => {
  const update = vi.fn();
  const definition = { ...blankDefinition,
    output_schema: { type: "object", properties: { brief: { type: "object", properties: {
      markdown: { type: "string" }, title: { type: "string" },
    }, required: ["markdown", "title"], additionalProperties: false } }, required: ["brief"], additionalProperties: false },
    presentation: { component: "markdown", title: "访前准备", field_map: { brief: "brief" } },
  } as typeof blankDefinition;
  render(<OutputMappingEditor definition={definition} update={update} />);
  expect(screen.getByRole("combobox", { name: "用户答复方式" })).toHaveValue("content");
  expect(screen.getByRole("combobox", { name: "展示字段" })).toHaveValue("brief.markdown");
});
