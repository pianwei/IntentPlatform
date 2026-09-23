import type { FunctionDefinition } from "../../shared/types";
import { Notice } from "../../shared/ui";

type OutputField = { type?: string | string[]; maxLength?: number; maxItems?: number; items?: Record<string, unknown>; [key: string]: unknown };
type OutputSchema = { type: "object"; properties: Record<string, OutputField>; required: string[]; additionalProperties: false; [key: string]: unknown };

function asSchema(value: FunctionDefinition["output_schema"]): OutputSchema | null {
  if (!value || value.type !== "object" || !value.properties || typeof value.properties !== "object" || Array.isArray(value.properties)) return null;
  return value as OutputSchema;
}

function renameKey<T>(items: Record<string, T>, oldKey: string, newKey: string): Record<string, T> {
  return Object.fromEntries(Object.entries(items).map(([key, value]) => [key === oldKey ? newKey : key, value]));
}

export function OutputMappingEditor({ definition, update }: { definition: FunctionDefinition; update: (patch: Partial<FunctionDefinition>) => void }) {
  const schema = asSchema(definition.output_schema);
  const display = definition.presentation.field_map ?? {};
  const entries = Object.entries(schema?.properties ?? {});
  const mapped = (field: string) => Object.entries(display).find(([, source]) => source === field)?.[0] ?? field;
  const change = (next: OutputSchema, fieldMap = display, extra: Partial<FunctionDefinition["presentation"]> = {}) =>
    update({ output_schema: next, presentation: { ...definition.presentation, ...extra, field_map: fieldMap } });
  const enable = () => change({ type: "object", properties: { result: { type: "string", maxLength: 2000 } }, required: ["result"], additionalProperties: false }, { 结果: "result" });
  const add = () => {
    if (!schema) return;
    let index = entries.length + 1;
    while (schema.properties[`field_${index}`]) ++index;
    const name = `field_${index}`;
    change({ ...schema, properties: { ...schema.properties, [name]: { type: "string", maxLength: 2000 } }, required: [...(schema.required ?? []), name] }, { ...display, [`字段${index}`]: name });
  };
  const remove = (name: string) => {
    if (!schema) return;
    const properties = { ...schema.properties }; delete properties[name];
    change({ ...schema, properties, required: (schema.required ?? []).filter(value => value !== name) }, Object.fromEntries(Object.entries(display).filter(([, value]) => value !== name)));
  };
  const changeName = (name: string, next: string) => {
    if (!schema || next !== name && Object.hasOwn(schema.properties, next)) return;
    change({ ...schema, properties: renameKey(schema.properties, name, next), required: (schema.required ?? []).map(value => value === name ? next : value) },
      Object.fromEntries(Object.entries(display).map(([label, value]) => [label, value === name ? next : value])));
  };
  const changeLabel = (name: string, next: string) => {
    const prior = mapped(name);
    if (next !== prior && Object.hasOwn(display, next)) return;
    const replacement = { ...display }; delete replacement[prior]; replacement[next] = name;
    update({ presentation: { ...definition.presentation, field_map: replacement } });
  };
  const changeField = (name: string, patch: Partial<OutputField>) => {
    if (!schema) return;
    change({ ...schema, properties: { ...schema.properties, [name]: { ...schema.properties[name], ...patch } } });
  };
  const changeType = (name: string, type: string) => {
    const field: OutputField = type === "string" ? { type, maxLength: 2000 } : type === "array" ? { type, maxItems: 100, items: { type: "string", maxLength: 2000 } } : { type };
    if (schema) change({ ...schema, properties: { ...schema.properties, [name]: field } });
  };
  return <section className="semantic-section" aria-label="受控业务输出">
    <div className="mapping-heading"><h3>业务输出契约与展示边界</h3>{schema && <button type="button" className="text-button" onClick={add}>添加展示字段</button>}</div>
    <Notice>新发布的真实功能版本需确认输出字段与展示标签。接口返回的额外字段不会自动展示；已发布旧版本保持原契约。数组和表格的复杂结构可在下方完整 JSON 映射中编辑。</Notice>
    {!schema ? <button type="button" className="secondary" onClick={enable}>配置输出契约</button> : <>
      <div className="form-grid"><label>结果组件<select value={definition.presentation.component} onChange={event => update({ presentation: { ...definition.presentation, component: event.target.value } })}>{["detail", "table", "text", "markdown", "citation", "file"].map(component => <option key={component} value={component}>{component}</option>)}</select></label>
        <label>展示标题<input value={definition.presentation.title} onChange={event => update({ presentation: { ...definition.presentation, title: event.target.value } })} /></label></div>
      {entries.map(([name, field]) => <div className="http-map-row" key={name}>
        <label>输出字段名<input aria-label={`输出字段名 ${name}`} value={name} onChange={event => changeName(name, event.target.value)} /></label>
        <label>展示标签<input aria-label={`展示标签 ${name}`} value={mapped(name)} onChange={event => changeLabel(name, event.target.value)} /></label>
        <label>数据类型<select aria-label={`数据类型 ${name}`} value={typeof field.type === "string" && ["string", "integer", "number", "boolean", "array"].includes(field.type) ? field.type : "other"} onChange={event => changeType(name, event.target.value)}><option value="other" disabled>复杂类型（完整 JSON 中编辑）</option>{["string", "integer", "number", "boolean", "array"].map(kind => <option key={kind} value={kind}>{kind}</option>)}</select></label>
        <button type="button" className="danger-link" aria-label={`移除输出字段 ${name}`} onClick={() => remove(name)}>移除</button>
        <label className="checkbox"><input type="checkbox" checked={(schema.required ?? []).includes(name)} onChange={event => change({ ...schema, required: event.target.checked ? [...(schema.required ?? []), name] : (schema.required ?? []).filter(value => value !== name) })} />必需字段</label>
        {field.type === "string" && <label>最大字符数<input type="number" min="0" max="16000" value={field.maxLength ?? ""} onChange={event => changeField(name, { maxLength: Number(event.target.value) })} /></label>}
        {field.type === "array" && <label>最大条数<input type="number" min="0" max="100" value={field.maxItems ?? ""} onChange={event => changeField(name, { maxItems: Number(event.target.value) })} /></label>}
      </div>)}
      <label className="checkbox"><input type="checkbox" checked={definition.presentation.files_field === "files"} onChange={event => update({ presentation: { ...definition.presentation, files_field: event.target.checked ? "files" : null } })} />允许接口返回内联文件字段 files；仅通过服务端内容与类型检查后生成授权下载</label>
      <p className="muted small">输出 Schema 限定字段类型、必需项和条数；字段映射限定展示内容。保存后须重新验证及确认来源，旧发布不会被草稿修改。</p>
    </>}
  </section>;
}
