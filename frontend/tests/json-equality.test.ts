import { expect, it } from "vitest";
import { jsonEqual } from "../shared/jsonEquality";

it("compares JSON object content recursively while retaining arrays, types and missing fields", () => {
  const draft = { body: { a: 1, b: [{ x: true, y: null }] }, query: {} };
  const original = JSON.stringify(draft);
  expect(jsonEqual(draft, { query: {}, body: { b: [{ y: null, x: true }], a: 1 } })).toBe(true);
  expect(jsonEqual({ options: ["a", "b"] }, { options: ["b", "a"] })).toBe(false);
  expect(jsonEqual({ value: 1 }, { value: "1" })).toBe(false);
  expect(jsonEqual({ value: null }, {})).toBe(false);
  expect(jsonEqual([], {})).toBe(false);
  expect(JSON.stringify(draft)).toBe(original);
});
