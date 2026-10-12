import { useState } from "react";
import type { FunctionDefinition } from "../../shared/types";
import { Notice } from "../../shared/ui";
import { httpEditorError } from "../../shared/httpMapping";
import "./SourceDocuments.css";
type Location = "path" | "query" | "headers" | "body";
const locations: Location[] = ["path", "query", "headers", "body"];
const labels = { path: "地址路径", query: "查询参数", headers: "请求头", body: "请求内容" };
export function HttpMappingEditor({ definition, update }: { definition: FunctionDefinition; update: (patch: Partial<FunctionDefinition>) => void }) {
  const [error, setError] = useState("");
  if (definition.connector.kind !== "http") return null;
  const connector = definition.connector;
  const structureError = httpEditorError(connector);
  if (structureError) return <Notice kind="error">接口参数配置无效：{structureError}。原始内容已保留，可在完整定义中修复。</Notice>;
  const args = Object.keys((definition.input_schema.properties ?? {}) as Record<string, unknown>);
  const mapping = connector.http_mapping ?? { path: {}, query: {}, headers: {}, body: {} };
  const current = (patch: Partial<FunctionDefinition["connector"]>) => update({ connector: { ...connector, version: "2", http_mapping: mapping, ...patch } });
  function change(from: Location, wire: string, location: Location, nextWire: string, argument: string) {
    const next = structuredClone(mapping);
    if ((from !== location || wire !== nextWire) && Object.hasOwn(next[location], nextWire)) { setError("该位置已有同名字段，未覆盖原映射；请使用不同名称。"); return; }
    if (from === location) next[from] = Object.fromEntries(Object.entries(next[from]).map(([key, value]) => key === wire ? [nextWire, argument] : [key, value]));
    else { delete next[from][wire]; next[location][nextWire] = argument; }
    setError(""); current({ http_mapping: next });
  }
  return <section className="semantic-section"><h3>接口参数</h3>
    {error && <Notice kind="error">{error}</Notice>}
    {locations.flatMap(location => Object.entries(mapping[location]).map(([wire, argument], index) => <div className="http-map-row" key={`${location}-${index}`}>
      <label>参数位置<select value={location} onChange={e => change(location, wire, e.target.value as Location, wire, argument)}>{locations.map(item => <option key={item} value={item}>{labels[item]}</option>)}</select></label>
      <label>接口字段名<input value={wire} onChange={e => change(location, wire, location, e.target.value, argument)} /></label>
      <label>输入参数<select value={argument} onChange={e => change(location, wire, location, wire, e.target.value)}><option value="">选择输入参数</option>{argument && !args.includes(argument) && <option disabled value={argument}>已移除参数（需要修正）</option>}{args.map(name => <option key={name}>{name}</option>)}</select></label>
      <button type="button" className="danger-link" aria-label={`删除映射 ${location} ${wire}`} onClick={() => { const next = structuredClone(mapping); delete next[location][wire]; current({ http_mapping: next }); }}>移除</button>
    </div>))}
    <button type="button" className="text-button" disabled={Object.hasOwn(mapping.query, "")} onClick={() => current({ http_mapping: { ...mapping, query: { ...mapping.query, "": "" } } })}>添加位置映射</button>
    <details className="advanced"><summary>请求与失败规则（选填）</summary><label className="checkbox"><input type="checkbox" checked={!!connector.http_body_required} onChange={e => current({ http_body_required: e.target.checked })} />即使没有参数也发送请求内容</label><p className="muted small">仅选择接口明确约定的业务失败状态。</p><div className="button-row">{[400, 404, 409, 422].map(status => <label className="checkbox" key={status}><input type="checkbox" checked={(connector.http_failure_statuses ?? []).includes(status)} onChange={e => current({ http_failure_statuses: e.target.checked ? [...(connector.http_failure_statuses ?? []), status].sort() : (connector.http_failure_statuses ?? []).filter(code => code !== status) })} />业务失败 HTTP {status}</label>)}</div></details>
  </section>;
}
