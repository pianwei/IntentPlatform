// Finite editor boundary; business mapping validity remains a server decision.
export function httpEditorError(connector: { http_mapping?: unknown; http_failure_statuses?: unknown; http_body_required?: unknown }): string | null {
  const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
  if (connector.http_mapping != null) {
    if (!object(connector.http_mapping)) return "http_mapping 必须是位置映射对象";
    for (const location of ["path", "query", "headers", "body"]) {
      const group = connector.http_mapping[location];
      if (!object(group) || Object.values(group).some(value => typeof value !== "string")) return `http_mapping.${location} 必须是字段名到参数名的对象`;
    }
  }
  if (connector.http_body_required !== undefined && typeof connector.http_body_required !== "boolean") return "http_body_required 必须是布尔值";
  const statuses = connector.http_failure_statuses;
  if (statuses !== undefined && (!Array.isArray(statuses) || statuses.some(code => !Number.isInteger(code) || ![400, 404, 409, 422].includes(code)) || new Set(statuses).size !== statuses.length)) return "http_failure_statuses 必须是由 400、404、409、422 组成的不重复整数数组";
  return null;
}
