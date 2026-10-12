import { useEffect, useState } from "react";
import { Notice } from "../../shared/ui";
import { ValueInput } from "../../shared/SchemaValueInput";
import type { FieldSchema } from "./SchemaFieldsEditor";
export function SchemaTestForm({ schema, value, onChange, onValidityChange }: { schema: Record<string, unknown>; value: string; onChange: (value: string) => void; onValidityChange?: (valid: boolean) => void }) {
  const [advanced, setAdvanced] = useState(false);
  let data: Record<string, unknown> = {}, valid = true;
  try { const parsed: unknown = JSON.parse(value); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(); data = parsed as Record<string, unknown>; } catch { valid = false; }
  useEffect(() => onValidityChange?.(valid), [valid, onValidityChange]);
  const fields = (schema.properties ?? {}) as Record<string, FieldSchema>, required = (schema.required ?? []) as string[];
  return <section className="schema-test-form">{valid ? <div className="business-mapping-grid">{Object.entries(fields).map(([key, field]) => <ValueInput key={key} field={field} value={data[key]} name={key} required={required.includes(key)} change={next => { const all = { ...data }; if (next === undefined) delete all[key]; else all[key] = next; onChange(JSON.stringify(all, null, 2)); }} />)}</div> : <Notice kind="error">测试输入需为有效参数对象，修正后恢复表单。</Notice>}
    {!Object.keys(fields).length && <p className="muted">此功能无需输入参数。</p>}
    <details className="advanced" open={advanced || !valid} onToggle={e => setAdvanced(e.currentTarget.open)}><summary>使用 JSON 编辑测试参数</summary><label>契约测试参数（JSON）<textarea rows={5} value={value} onChange={e => onChange(e.target.value)} /></label></details>
  </section>;
}
