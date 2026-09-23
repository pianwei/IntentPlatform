import { useState } from "react";
import type { FunctionDefinition } from "../../shared/types";
import { Notice } from "../../shared/ui";
import { httpEditorError } from "../../shared/httpMapping";
import "./SourceDocuments.css";
type Location = "path" | "query" | "headers" | "body";
const locations: Location[] = ["path", "query", "headers", "body"];
const labels = { path: "路径 path", query: "查询 query", headers: "请求头 header", body: "JSON body" };
export function HttpMappingEditor({ definition, update }: { definition: FunctionDefinition; update: (patch: Partial<FunctionDefinition>) => void }) {
  const [error, setError] = useState("");
  if (definition.connector.kind !== "http") return null;
  const connector = definition.connector;
  const structureError = httpEditorError(connector);
  if (structureError) return <Notice kind="error">HTTP 编辑数据无效：{structureError}。请在完整 JSON 中修复，原始草稿未被改写。</Notice>;
  const mapping = connector.http_mapping ?? { path: {}, query: {}, headers: {}, body: {} };
  const args = Object.keys((definition.input_schema.properties ?? {}) as Record<string, unknown>);
  function change(from: Location, wire: string, location: Location, nextWire: string, argument: string) {
    const next = structuredClone(mapping);
    if ((from !== location || wire !== nextWire) && Object.hasOwn(next[location], nextWire)) { setError("该位置已有同名字段，未覆盖原映射。请使用不同名称。"); return; }
    if (from === location) next[from] = Object.fromEntries(Object.entries(next[from]).map(([key, value]) => key === wire ? [nextWire, argument] : [key, value]));
    else { delete next[from][wire]; next[location][nextWire] = argument; }
    setError(""); update({ connector: { ...connector, http_mapping: next } });
  }
  return <section className="semantic-section"><h3>HTTP 参数位置映射</h3><label>HTTP 连接器版本<select value={connector.version ?? "1"} onChange={e => update({ connector: { ...connector, version: e.target.value, ...(e.target.value === "2" && !connector.http_mapping ? { http_mapping: mapping } : {}) } })}><option value="1" disabled={connector.version === "2"}>v1 · 原有连接器</option><option value="2">v2 · 显式 path/query/header/body</option></select></label>{connector.version !== "2" ? <p className="muted small">原有 v1 保持原行为。服务端 OpenAPI 导入会生成 v2 的显式位置映射。</p> : <><Notice>v2 不能直接降级为 v1，以免丢失参数位置和传输约定。需要 v1 时请创建独立功能草稿并重新映射。</Notice><><label className="checkbox"><input type="checkbox" checked={!!connector.http_body_required} onChange={e => update({ connector: { ...connector, http_body_required: e.target.checked } })} />必须发送 JSON body（即使空对象）</label><p className="muted small">仅将接口契约明确约定的下列 HTTP 状态认定为业务失败。默认不选；401/403、5xx、超时不能作为预期业务失败。</p><div className="button-row">{[400, 404, 409, 422].map(status => <label className="checkbox" key={status}><input type="checkbox" checked={(connector.http_failure_statuses ?? []).includes(status)} onChange={e => update({ connector: { ...connector, http_failure_statuses: e.target.checked ? [...(connector.http_failure_statuses ?? []), status].sort() : (connector.http_failure_statuses ?? []).filter(code => code !== status) } })} />业务失败 HTTP {status}</label>)}</div></>{error && <Notice kind="error">{error}</Notice>}<p className="muted small">每个输入参数必须有唯一目的位置。请求头不允许映射凭据、Cookie、Host 等保留字段；路径安全与真实目标授权仍由服务端校验。</p>{locations.flatMap(location => Object.entries(mapping[location]).map(([wire, argument], index) => <div className="http-map-row" key={`${location}-${index}`}><label>参数位置<select value={location} onChange={e => change(location, wire, e.target.value as Location, wire, argument)}>{locations.map(l => <option key={l} value={l}>{labels[l]}</option>)}</select></label><label>接口字段名<input value={wire} onChange={e => change(location, wire, location, e.target.value, argument)} /></label><label>输入参数<select value={argument} onChange={e => change(location, wire, location, wire, e.target.value)}><option value="">选择输入参数</option>{argument && !args.includes(argument) && <option disabled value={argument}>{argument}（Schema 未定义）</option>}{args.map(name => <option key={name}>{name}</option>)}</select></label><button type="button" className="danger-link" aria-label={`删除映射 ${location} ${wire}`} onClick={() => { const next = structuredClone(mapping); delete next[location][wire]; update({ connector: { ...connector, http_mapping: next } }); }}>移除</button></div>))}<button type="button" className="text-button" disabled={Object.hasOwn(mapping.query, "")} onClick={() => update({ connector: { ...connector, http_mapping: { ...mapping, query: { ...mapping.query, "": "" } } } })}>添加位置映射</button><p className="muted small">输入 Schema、成功条件、状态和结果路径在完整 JSON 中编辑；更改后旧验证与审查失效。</p></>}</section>;
}
