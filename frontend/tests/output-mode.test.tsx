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
