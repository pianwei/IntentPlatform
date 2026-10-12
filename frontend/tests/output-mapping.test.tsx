import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { OutputMappingEditor } from "../console/src/OutputMappingEditor";
import { blankDefinition } from "../console/src/FunctionEditor";
import type { FunctionDefinition } from "../shared/types";

afterEach(cleanup);

it("records typed approved output fields and explicit file permission in the function draft", () => {
  let latest = structuredClone(blankDefinition);
  function Harness() {
    const [definition, setDefinition] = useState<FunctionDefinition>(structuredClone(blankDefinition));
    latest = definition;
    return <OutputMappingEditor definition={definition} update={patch => setDefinition(current => ({ ...current, ...patch }))} />;
  }
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "添加输出字段" }));
  fireEvent.change(screen.getByLabelText("输出字段名 field_1"), { target: { value: "result" } });
  fireEvent.click(screen.getByLabelText("输出必填 result"));
  expect(latest.output_schema).toMatchObject({ type: "object", properties: { result: { type: "string", maxLength: 2000 } }, required: ["result"] });
  fireEvent.change(screen.getByLabelText("输出字段字段说明 result"), { target: { value: "办理结果" } });
  fireEvent.click(screen.getByRole("button", { name: "添加输出字段" }));
  fireEvent.change(screen.getByLabelText("数据类型 field_2"), { target: { value: "array" } });
  fireEvent.click(screen.getByLabelText("包含可下载文件"));
  expect(Object.values(latest.presentation.field_map)).toEqual(["result", "field_2"]);
  expect(latest.presentation.files_field).toBe("files");
  expect(latest.output_schema?.properties).toMatchObject({ field_2: { type: "array", maxItems: 100 } });
});
