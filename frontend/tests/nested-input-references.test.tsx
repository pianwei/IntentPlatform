import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { InputParameters } from "../console/src/InputParameters";
import { blankDefinition } from "../console/src/FunctionEditor";
import type { FunctionDefinition } from "../shared/types";

afterEach(cleanup);
const closedObject = (properties: Record<string, unknown>) => ({ type: "object", properties, required: Object.keys(properties), additionalProperties: false });
function Harness({ initial }: { initial: FunctionDefinition }) {
  const [definition, setDefinition] = useState(initial);
  return <><InputParameters definition={definition} update={patch => setDefinition(previous => ({ ...previous, ...patch }))} /><output data-testid="definition">{JSON.stringify(definition)}</output></>;
}
const current = () => JSON.parse(screen.getByTestId("definition").textContent!) as FunctionDefinition;

it("renames a nested input in conditions and both example sets without changing sibling data", () => {
  const initial: FunctionDefinition = { ...structuredClone(blankDefinition),
    input_schema: closedObject({ filter: closedObject({ active: { type: "boolean" }, region: { type: "string", maxLength: 100 } }) }),
    preconditions: [{ field: "filter.active", operator: "eq", value: true }, { field: "filter.region", operator: "eq", value: "华东" }],
    positive_examples: [{ text: "查有效客户", arguments: { filter: { active: true, region: "华东" } } }],
    negative_examples: [{ text: "边界案例", arguments: { filter: { active: false, region: "华东" } } }],
  };
  render(<Harness initial={initial} />);
  fireEvent.change(screen.getByLabelText("参数名称 filter.active"), { target: { value: "enabled" } });
  expect(current().preconditions).toEqual([{ field: "filter.enabled", operator: "eq", value: true }, { field: "filter.region", operator: "eq", value: "华东" }]);
  expect(current().positive_examples[0]).toEqual({ text: "查有效客户", arguments: { filter: { enabled: true, region: "华东" } } });
  expect(current().negative_examples[0]).toEqual({ text: "边界案例", arguments: { filter: { enabled: false, region: "华东" } } });
  expect(initial.positive_examples[0].arguments).toEqual({ filter: { active: true, region: "华东" } });
});

it("renames a nested ancestor and preserves descendants in condition paths and examples", () => {
  const initial: FunctionDefinition = { ...structuredClone(blankDefinition),
    input_schema: closedObject({ filter: closedObject({ status: closedObject({ active: { type: "boolean" } }) }) }),
    preconditions: [{ field: "filter.status.active", operator: "eq", value: true }],
    positive_examples: [{ arguments: { filter: { status: { active: true } } } }],
  };
  render(<Harness initial={initial} />);
  fireEvent.change(screen.getByLabelText("参数名称 filter.status"), { target: { value: "state" } });
  expect(current().preconditions).toEqual([{ field: "filter.state.active", operator: "eq", value: true }]);
  expect(current().positive_examples[0].arguments).toEqual({ filter: { state: { active: true } } });
  expect(screen.getByLabelText("参数名称 filter.state.active")).toBeInTheDocument();
});

it("refuses deletion of a referenced nested input and its ancestor without changing examples", () => {
  const initial: FunctionDefinition = { ...structuredClone(blankDefinition),
    input_schema: closedObject({ filter: closedObject({ active: { type: "boolean" } }) }),
    preconditions: [{ field: "filter.active", operator: "exists" }],
    positive_examples: [{ arguments: { filter: { active: true } } }],
  };
  render(<Harness initial={initial} />);
  fireEvent.click(screen.getByRole("button", { name: "删除参数 filter.active" }));
  expect(screen.getByRole("alert")).toHaveTextContent("仍被前置条件使用");
  expect(current()).toEqual(initial);
  fireEvent.click(screen.getByRole("button", { name: "删除参数 1" }));
  expect(current()).toEqual(initial);
  expect(screen.getByLabelText("参数名称 filter.active")).toBeInTheDocument();
});

it("updates every array item example on nested rename and removes only the selected example field", () => {
  const initial: FunctionDefinition = { ...structuredClone(blankDefinition),
    input_schema: closedObject({ rows: { type: "array", maxItems: 5, items: closedObject({ code: { type: "string", maxLength: 100 }, active: { type: "boolean" } }) } }),
    positive_examples: [{ arguments: { rows: [{ code: "A", active: true }, { code: "B", active: false }] } }],
    negative_examples: [{ arguments: { rows: [{ active: false }] } }],
  };
  render(<Harness initial={initial} />);
  fireEvent.click(screen.getAllByText("约束与示例")[0]);
  fireEvent.change(screen.getByLabelText("参数名称 rows.code"), { target: { value: "product_code" } });
  expect(current().positive_examples[0].arguments).toEqual({ rows: [{ product_code: "A", active: true }, { product_code: "B", active: false }] });
  expect(current().negative_examples).toEqual(initial.negative_examples);
  fireEvent.click(screen.getByRole("button", { name: "删除参数 rows.product_code" }));
  expect(current().positive_examples[0].arguments).toEqual({ rows: [{ active: true }, { active: false }] });
  expect(current().negative_examples).toEqual(initial.negative_examples);
  expect(initial.positive_examples[0].arguments).toEqual({ rows: [{ code: "A", active: true }, { code: "B", active: false }] });
});
