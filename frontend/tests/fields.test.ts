import { describe, expect, it } from "vitest";
import { parseFieldValues } from "../shared/fields";
describe("typed question values", () => {
  it("preserves arrays, explicit null, numbers and booleans while omitting absent optional fields", () => {
    expect(
      parseFieldValues(
        [
          { name: "ids", type: "array", required: true },
          { name: "optional", type: "string", required: false },
          { name: "nullable", type: ["string", "null"], required: true },
          { name: "amount", type: "number", required: true },
          { name: "enabled", type: "boolean", required: true },
        ],
        {
          ids: '["one","two"]',
          nullable: "null",
          amount: "12.5",
          enabled: "false",
        },
      ),
    ).toEqual({
      ids: ["one", "two"],
      nullable: null,
      amount: 12.5,
      enabled: false,
    });
  });
  it("rejects invalid JSON and fractional integers before asking the server to act", () => {
    expect(() =>
      parseFieldValues([{ name: "ids", type: "array", required: true }], {
        ids: "not json",
      }),
    ).toThrow("JSON");
    expect(() =>
      parseFieldValues([{ name: "count", type: "integer", required: true }], {
        count: "1.5",
      }),
    ).toThrow("数值格式");
  });
});
