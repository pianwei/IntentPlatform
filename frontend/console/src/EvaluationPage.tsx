import { useEffect, useRef, useState, type FormEvent } from "react";
import { FlaskConical, Play, Upload } from "lucide-react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Badge, Empty, Notice } from "../../shared/ui";
import type { Agent, Capability } from "../../shared/types";
import "./EvaluationPage.css";

export type DiagnosticCase = { text: string; expected_capability_id: string };
type EvaluationResponse = {
  id: string;
  label: string;
  total: number;
  correct: number;
  accuracy: number;
  results: { correct: boolean; actual?: string[]; error?: string }[];
  fixture_mode: boolean;
  release_id: string;
  acceptance_evidence: boolean;
};
type CompletedEvaluation = {
  response: EvaluationResponse;
  cases: DiagnosticCase[];
  agentName: string;
};

export function parseDiagnosticCases(raw: string): DiagnosticCase[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("用例必须是有效的 JSON 数组。");
  }
  if (!Array.isArray(parsed) || parsed.length < 1 || parsed.length > 1000)
    throw new Error("请提供 1 至 1000 条诊断用例。");
  return parsed.map((item, index) => {
    if (
      !item ||
      typeof item !== "object" ||
      typeof item.text !== "string" ||
      !item.text.trim() ||
      typeof item.expected_capability_id !== "string" ||
      !item.expected_capability_id.trim()
    )
      throw new Error(
        `第 ${index + 1} 条用例需要非空 text 和 expected_capability_id。`,
      );
    return {
      text: item.text,
      expected_capability_id: item.expected_capability_id,
    };
  });
}

export function EvaluationPage({
  api,
  workspace,
  agents,
  functions,
  writable,
  fixtureMode,
}: {
  api: ApiClient;
  workspace: string;
  agents: Agent[];
  functions: Capability[];
  writable: boolean;
  fixtureMode: boolean;
}) {
  const published = agents.filter((agent) => !!agent.current_release_id);
  const [agentId, setAgentId] = useState(published[0]?.id ?? "");
  const [label, setLabel] = useState("");
  const [raw, setRaw] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [completed, setCompleted] = useState<CompletedEvaluation | null>(null);
  const alive = useRef(true);
  const selected = published.find((agent) => agent.id === agentId);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (!published.some((agent) => agent.id === agentId))
      setAgentId(published[0]?.id ?? "");
  }, [agents, agentId]);
  async function evaluate(event: FormEvent) {
    event.preventDefault();
    if (!writable || !selected || busy) return;
    setError("");
    let cases: DiagnosticCase[];
    try {
      cases = parseDiagnosticCases(raw);
    } catch (error) {
      setError(errorText(error));
      return;
    }
    if (!label.trim()) {
      setError("请填写本次评测的标识。");
      return;
    }
    setBusy(true);
    setCompleted(null);
    const agentName = selected.name;
    try {
      const response = await api.post<EvaluationResponse>(
        `${management(workspace)}/evaluations`,
        { agent_id: agentId, label: label.trim(), cases },
      );
      if (alive.current) setCompleted({ response, cases, agentName });
    } catch (error) {
      if (alive.current) setError(errorText(error));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function upload(file: File) {
    setError("");
    try {
      const text = await file.text();
      parseDiagnosticCases(text);
      if (alive.current) setRaw(text);
    } catch (error) {
      if (alive.current) setError(errorText(error));
    }
  }
  return (
    <div className="evaluation-page">
      <Notice>
        {fixtureMode ? "当前为开发诊断。" : "当前接口提供路由诊断。"}
        结果不作为封存 S1 / S2 / S3
        验收证据；需要由业务人员提供并核对真实用例与期望功能。
      </Notice>
      {error && <Notice kind="error">{error}</Notice>}
      <section className="panel evaluation-form">
        <div className="mapping-heading">
          <h2>
            <FlaskConical size={20} />
            路由诊断
          </h2>
          <Badge>{published.length} 个已发布智能体</Badge>
        </div>
        {!published.length && (
          <Empty title="请先发布一个智能体">
            评测绑定已发布快照，草稿不能用于诊断。
          </Empty>
        )}
        <form onSubmit={(event) => void evaluate(event)}>
          <fieldset disabled={!writable || busy}>
            <div className="form-grid">
              <label>
                评测智能体
                <select
                  value={agentId}
                  onChange={(event) => setAgentId(event.target.value)}
                  required
                >
                  <option value="" disabled>
                    选择已发布智能体
                  </option>
                  {published.map((agent) => (
                    <option key={agent.id} value={agent.id}>
                      {agent.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                评测标识
                <input
                  required
                  value={label}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="填写本次数据集或实验标识"
                />
              </label>
            </div>
            {selected && (
              <p className="muted small">
                当前发布：<code>{selected.current_release_id}</code>
                ；实际评测版本以返回的 release_id 为准。
              </p>
            )}
            <div className="mapping-heading">
              <h3>用户提供的诊断用例</h3>
              <label className="upload-cases">
                <Upload size={15} />
                导入 JSON
                <input
                  type="file"
                  accept=".json,application/json"
                  aria-label="导入诊断用例 JSON"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void upload(file);
                    event.target.value = "";
                  }}
                />
              </label>
            </div>
            <label>
              用例 JSON
              <textarea
                className="code-input"
                rows={10}
                value={raw}
                onChange={(event) => setRaw(event.target.value)}
                placeholder="输入 JSON 数组；每条对象包含 text 和 expected_capability_id"
                spellCheck={false}
              />
            </label>
            <p className="muted small">
              1–1000 条。text 为用户表达，expected_capability_id
              为经业务核对的期望功能
              ID。当前诊断只比较模型返回的功能列表是否恰好匹配单个期望
              ID，不评定参数、确认策略或真实业务调用。
            </p>
            <button
              className="primary"
              disabled={
                busy || !writable || !selected || !raw.trim() || !label.trim()
              }
            >
              <Play size={16} />
              {busy ? "正在运行诊断…" : "运行路由诊断"}
            </button>
          </fieldset>
        </form>
        {!writable && <p className="muted small">当前身份没有评测发布权限。</p>}
        <details className="form-details">
          <summary>查看当前空间的功能 ID</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>功能名称</th>
                  <th>功能 ID</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {functions.map((item) => (
                  <tr key={item.id}>
                    <td>
                      {item.name}
                      {item.is_fixture && (
                        <Badge tone="warning">开发夹具</Badge>
                      )}
                    </td>
                    <td>
                      <code>{item.id}</code>
                    </td>
                    <td>{item.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            此表供填写 ID 时核对。实际可见候选由评测发布快照与当前身份权限决定。
          </p>
        </details>
      </section>
      {completed && (
        <section className="panel evaluation-result">
          <header className="evaluation-result-heading">
            <div>
              <div className="eyebrow">LAST COMPLETED DIAGNOSTIC</div>
              <h2>{completed.response.label}</h2>
              <p className="muted small">
                {completed.agentName} · {completed.response.id}
              </p>
              <code>{completed.response.release_id}</code>
            </div>
            <Badge tone={completed.response.fixture_mode ? "warning" : "info"}>
              {completed.response.fixture_mode ? "开发诊断" : "路由诊断"}
            </Badge>
          </header>
          <div className="evaluation-metrics">
            <div>
              <span>实际正确 / 总数</span>
              <strong>
                {completed.response.correct} / {completed.response.total}
              </strong>
            </div>
            <div>
              <span>服务端正确率</span>
              <strong>
                {Number.isFinite(completed.response.accuracy)
                  ? `${(completed.response.accuracy * 100).toFixed(2)}%`
                  : "未返回"}
              </strong>
            </div>
            <div>
              <span>验收属性</span>
              <strong className="evaluation-classification">
                {completed.response.acceptance_evidence
                  ? "接口标记为验收证据，仍需独立核对"
                  : "非正式验收证据"}
              </strong>
            </div>
          </div>
          <p className="evaluation-note muted small">
            失败及异常仍保留在总分母中。下表对应本次提交的原始用例；修改上方输入不会改写已有结果。
          </p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>用户表达</th>
                  <th>期望功能 ID</th>
                  <th>实际功能 / 异常</th>
                  <th>结论</th>
                </tr>
              </thead>
              <tbody>
                {completed.cases.map((item, index) => {
                  const result = completed.response.results[index];
                  return (
                    <tr key={index}>
                      <td>{index + 1}</td>
                      <td>{item.text}</td>
                      <td>
                        <code>{item.expected_capability_id}</code>
                      </td>
                      <td>
                        {result?.error ??
                          (result?.actual?.length
                            ? result.actual.join("、")
                            : "未返回功能")}
                      </td>
                      <td>
                        <Badge
                          tone={
                            !result
                              ? "warning"
                              : result.correct
                                ? "success"
                                : "danger"
                          }
                        >
                          {!result
                            ? "缺少结果"
                            : result.correct
                              ? "匹配"
                              : "不匹配 / 异常"}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <details className="evaluation-raw">
            <summary>查看原始诊断响应</summary>
            <pre>{JSON.stringify(completed.response, null, 2)}</pre>
          </details>
        </section>
      )}
    </div>
  );
}
