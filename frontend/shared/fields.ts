import type { Field } from "./types";
export function usesJsonInput(field: Field) {
  return (
    Array.isArray(field.type) ||
    field.type === "array" ||
    field.type === "object"
  );
}
export function parseFieldValues(
  fields: Field[],
  values: Record<string, string>,
) {
  const result: Record<string, unknown> = {};
  for (const field of fields) {
    const raw = values[field.name];
    if (raw === undefined || raw === "") {
      if (field.required) throw new Error(`请填写 ${field.label || field.name}`);
      continue;
    }
    if (usesJsonInput(field)) {
      try {
        result[field.name] = JSON.parse(raw);
      } catch {
        throw new Error(
          `${field.name} 需要有效 JSON（数组、数值、字符串或 null）`,
        );
      }
    } else if (field.type === "integer" || field.type === "number") {
      const number = Number(raw);
      if (
        !Number.isFinite(number) ||
        (field.type === "integer" && !Number.isInteger(number))
      )
        throw new Error(`${field.name} 的数值格式不正确`);
      result[field.name] = number;
    } else if (field.type === "boolean") {
      if (!["true", "false"].includes(raw))
        throw new Error(`请选择 ${field.name}`);
      result[field.name] = raw === "true";
    } else result[field.name] = raw;
  }
  return result;
}
