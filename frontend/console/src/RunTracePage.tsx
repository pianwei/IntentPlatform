import { useEffect, useState, type FormEvent } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import type { RuntimeStepSummary, RuntimeTimingSummary } from "../../shared/types";
import "./RunTracePage.css";

type TraceRun = { run_id: string; agent_id: string; status: string; decision: string | null;
  created_at: string; finished_at: string | null; elapsed_ms: number | null };
type TraceEntry = { layer: string; kind: string; detail: Record<string, unknown>; created_at: string };
type Attempt = { id: string; revision: number; kind: string; status: string;
  total_ms?: number | null; model_ms?: number | null; connector_ms?: number | null; error_stage?: string | null };
type TraceDetail = { run: TraceRun; layers: Record<string, TraceEntry[]>; steps: RuntimeStepSummary[];
  timing: RuntimeTimingSummary; attempts: Attempt[]; plan_source: string | null };
const layers = [["entity", "实体层"], ["dynamics", "动力层"], ["planning", "策划层"], ["execution", "执行结果"]] as const;
function elapsed(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(0)} ms` : "未记录";
}
function readable(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function RunTracePage({ api, workspace, admin }: { api: ApiClient; workspace: string; admin: boolean }) {
  const [runs, setRuns] = useState<TraceRun[]>([]);
  const [selected, setSelected] = useState<TraceDetail | null>(null);
  const [runId, setRunId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [status, setStatus] = useState("");
  const [createdAfter, setCreatedAfter] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const base = `${management(workspace)}/runs`;
  useEffect(() => { let live = true; setRuns([]); setSelected(null); setError("");
    if (admin) api.get<TraceRun[]>(base).then(value => { if (live) setRuns(value); }).catch(e => { if (live) setError(errorText(e)); });
    return () => { live = false; };
  }, [api, base, admin]);
  if (!admin) return <Notice>三层运行日志仅供当前空间管理员查看。</Notice>;
  async function search(e?: FormEvent) {
    e?.preventDefault(); setLoading(true); setError(""); setSelected(null);
    const params = new URLSearchParams();
    if (runId.trim()) params.set("run_id", runId.trim());
    if (agentId.trim()) params.set("agent_id", agentId.trim());
    if (status) params.set("status", status);
    if (createdAfter) params.set("created_after", new Date(createdAfter).toISOString());
    try { setRuns(await api.get<TraceRun[]>(`${base}?${params.toString()}`)); }
    catch (cause) { setError(errorText(cause)); }
    finally { setLoading(false); }
  }
  async function open(id: string) {
    setLoading(true); setError("");
    try { setSelected(await api.get<TraceDetail>(`${base}/${encodeURIComponent(id)}/trace`)); }
    catch (cause) { setError(errorText(cause)); }
    finally { setLoading(false); }
  }
  return <div className="run-trace-page">
    <section className="panel run-trace-search"><h2>运行日志</h2><p className="muted">按智能体与运行状态查看实体、动力和策划决定。输入输出仅显示获准字段；记录不包含模型隐藏推理或凭据。</p>
      <form onSubmit={e => void search(e)}><label>运行编号<input value={runId} onChange={e => setRunId(e.target.value)} /></label><label>智能体编号<input value={agentId} onChange={e => setAgentId(e.target.value)} /></label><label>状态<select value={status} onChange={e => setStatus(e.target.value)}><option value="">全部</option>{["RECEIVED", "ROUTING", "WAITING_INPUT", "WAITING_CONFIRMATION", "EXECUTING", "SUCCEEDED", "FAILED", "UNKNOWN", "CANCELLED"].map(value => <option key={value} value={value}>{value}</option>)}</select></label><label>起始时间<input type="datetime-local" value={createdAfter} onChange={e => setCreatedAfter(e.target.value)} /></label><button className="primary" disabled={loading}>查询</button></form>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="table-scroll"><table><thead><tr><th>运行</th><th>智能体</th><th>状态</th><th>开始</th><th>耗时</th><th>详情</th></tr></thead><tbody>{runs.map(row => <tr key={row.run_id}><td><code>{row.run_id}</code></td><td><code>{row.agent_id}</code></td><td>{row.status}</td><td>{new Date(row.created_at).toLocaleString()}</td><td>{elapsed(row.elapsed_ms)}</td><td><button className="text-button" onClick={() => void open(row.run_id)}>查看三层轨迹</button></td></tr>)}</tbody></table>{!runs.length && !loading && <p className="muted">当前筛选条件下没有运行记录。</p>}</div>
    </section>
    {selected && <section className="panel run-trace-detail"><div className="mapping-heading"><h2>运行详情</h2><button className="secondary" onClick={() => setSelected(null)}>收起详情</button></div><p><code>{selected.run.run_id}</code> · {selected.run.status} · {selected.plan_source === "skill" ? "规则 Skill" : selected.plan_source === "model" ? "模型规划" : "原有路由"}</p>
      <p className="muted">总耗时 {elapsed(selected.timing.total_ms)} · 处理 {elapsed(selected.timing.processing_ms)} · 等待用户 {elapsed(selected.timing.user_wait_ms)}</p>
      <div className="run-trace-layers">{layers.map(([key, label]) => <section key={key}><h3>{label}</h3>{!selected.layers[key]?.length && <p className="muted">暂无记录</p>}{selected.layers[key]?.map((entry, index) => <div className="run-trace-entry" key={`${entry.created_at}-${index}`}><strong>{entry.kind}</strong><small>{new Date(entry.created_at).toLocaleString()}</small><dl>{Object.entries(entry.detail).map(([field, value]) => <div key={field}><dt>{field}</dt><dd>{readable(value)}</dd></div>)}</dl></div>)}</section>)}</div>
      <h3>业务步骤</h3>{!selected.steps.length && <p className="muted">当前运行没有已登记步骤。</p>}{selected.steps.map((step, index) => <details key={`${index}-${step.name}`}><summary>{step.name} · {step.status} · {elapsed(step.elapsed_ms)}</summary><dl><dt>输入</dt><dd>{readable(step.input)}</dd><dt>输出</dt><dd>{readable(step.output)}</dd></dl></details>)}
      <h3>执行尝试</h3>{!selected.attempts.length && <p className="muted">暂无执行尝试。</p>}{selected.attempts.length > 0 && <div className="table-scroll"><table><thead><tr><th>阶段</th><th>状态</th><th>总耗时</th><th>模型</th><th>连接器</th><th>错误阶段</th></tr></thead><tbody>{selected.attempts.map(item => <tr key={item.id}><td>{item.kind}</td><td>{item.status}</td><td>{elapsed(item.total_ms)}</td><td>{elapsed(item.model_ms)}</td><td>{elapsed(item.connector_ms)}</td><td>{item.error_stage ?? "—"}</td></tr>)}</tbody></table></div>}
    </section>}
  </div>;
}
