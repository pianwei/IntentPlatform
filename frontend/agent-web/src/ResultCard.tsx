import { useState, type FormEvent } from "react";
import {
  CheckCircle2,
  FileText,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import type { Choice, Field, Run, RuntimeEvent, RuntimeFile } from "../../shared/types";
import { SafeMarkdown } from "../../shared/SafeMarkdown";
import { Badge, Notice } from "../../shared/ui";
import { parseFieldValues, usesJsonInput } from "../../shared/fields";
export const statuses: Record<string, string> = {
  RECEIVED: "已受理",
  ROUTING: "正在识别意图",
  WAITING_INPUT: "等待补充信息",
  WAITING_CONFIRMATION: "等待您的确认",
  EXECUTING: "业务执行中",
  SUCCEEDED: "已完成",
  FAILED: "执行失败",
  UNKNOWN: "结果未知，需核实",
  CANCELLED: "已取消",
};
export const questionKinds: Record<string, string> = { arguments: "补充业务参数", function_choice: "选择业务功能", entity_mention: "描述业务对象", entity_choice: "选择授权业务对象", history_choice: "选择历史结果对象", planner_arguments: "补充任务参数", planner_choice: "选择业务对象", planner_clarification: "澄清任务目标" };
export function BindingSummary({ bindings }: { bindings: Run["bindings"] }) {
  if (!bindings?.length) return null;
  return <details className="binding-summary"><summary>业务对象已确认</summary>{bindings.map((binding, index) => <p key={index}>{binding.source === "history" ? "同会话历史结果" : binding.source === "resolver" ? "已授权业务对象" : "业务对象已确认"}</p>)}</details>;
}
function valueText(value: unknown) {
  return value === null || value === undefined
    ? "—"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
const businessLabels: Record<string, string> = {
  customers: "客户", customer_id: "客户编号", name: "名称", city: "城市", masked_id: "证件标识",
  accounts: "账户", account_id: "账户编号", label: "账户名称", currency: "币种",
  balance: "余额", available: "可用余额", status: "状态", reason: "原因",
  transactions: "交易", transaction_id: "交易编号", posted_on: "交易日期",
  direction: "收支方向", amount: "金额", description: "说明",
  income: "收入", expense: "支出", net: "净额", count: "笔数",
  loans: "贷款合同", loan_id: "合同编号", product_name: "产品名称", outstanding: "剩余余额",
  payments: "还款安排", due_on: "到期日", overdue: "逾期记录", days: "逾期天数",
  products: "产品", product_id: "产品编号", category: "类别", holdings: "持仓",
  holding_id: "持仓编号", units: "持有份额", tickets: "工单", ticket_id: "工单编号",
  history: "处理记录", risk_level: "风险等级", as_of: "登记日期", segment: "客户分类",
};
function businessLabel(name: string) { return businessLabels[name] ?? name; }
function duration(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(0)} ms` : "未记录";
}
export function RuntimeProcessingSummary({ run }: { run: Run | null }) {
  if (!run || !run.steps?.length) return null;
  const timing = run.timing;
  return <details className="binding-summary"><summary>处理记录 · {run.steps.length} 步 · 总耗时 {duration(timing?.total_ms)}</summary>
    <p className="muted small">处理 {duration(timing?.processing_ms)} · 等待补充或确认 {duration(timing?.user_wait_ms)}</p>
    <ol>{run.steps.map((step, index) => <li key={`${index}-${step.name}`}><strong>{step.name}</strong> · {statuses[step.status] ?? step.status} · {duration(step.elapsed_ms)}
      {Object.keys(step.input ?? {}).length > 0 && <details><summary>输入</summary><dl className="detail-grid">{Object.entries(step.input).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{valueText(value)}</dd></div>)}</dl></details>}
      {step.output !== null && step.output !== undefined && <details><summary>输出</summary><pre>{valueText(step.output)}</pre></details>}
    </li>)}</ol>
  </details>;
}
export function canInteract(event: RuntimeEvent, run: Run | null) {
  return (
    !!run &&
    run.run_id === event.run_id &&
    run.revision === event.revision &&
    ((event.event_type === "QUESTION" && run.status === "WAITING_INPUT") ||
      (event.event_type === "CONFIRMATION" &&
        run.status === "WAITING_CONFIRMATION"))
  );
}
export function ResultCard({
  event,
  run,
  onAction,
  busy = false,
  onDownload,
}: {
  event: RuntimeEvent;
  run: Run | null;
  onAction: (
    event: RuntimeEvent,
    kind: "answers" | "confirmations",
    body: Record<string, unknown>,
  ) => Promise<void>;
  busy?: boolean;
  onDownload?: (file: RuntimeFile) => Promise<void>;
}) {
  const data = event.payload;
  const [values, setValues] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);
  const active = canInteract(event, run) && !busy;
  if (event.event_type === "STATUS")
    return (
      <div className="run-progress" role="status">
        {["RECEIVED", "ROUTING", "EXECUTING"].includes(String(data.status)) ? (
          <LoaderCircle size={15} className="spin" />
        ) : (
          <CheckCircle2 size={15} />
        )}
        <span>
          {statuses[String(data.status)] || String(data.status)}
          {data.message ? ` · ${String(data.message)}` : ""}
        </span>
      </div>
    );
  if (event.event_type === "ERROR")
    return (
      <Notice kind="error">
        {String(data.message ?? "请求未完成")}
      </Notice>
    );
  if (event.event_type === "QUESTION") {
    const fields = (Array.isArray(data.fields) ? data.fields : []) as Field[];
    const options = (
      Array.isArray(data.options) ? data.options : []
    ) as Choice[];
    async function submit(e: FormEvent) {
      e.preventDefault();
      setFormError("");
      try {
        await onAction(event, "answers", {
          question_id: data.question_id,
          values: parseFieldValues(fields, values),
        });
      } catch (error) {
        setFormError(error instanceof Error ? error.message : "参数格式有误");
      }
    }
    return (
      <section className="result-card">
        <header>
          <FileText size={18} />
          <h3>{String(data.message ?? "请补充以下信息")}</h3>
        </header>
        {typeof data.kind === "string" && <p className="muted small">{questionKinds[data.kind] ?? "补充信息"}{data.kind === "entity_mention" ? "：请填写名称或描述，由服务端解析并校验权限。" : ""}</p>}
        {!active && (
          <p className="muted small">
            该问题当前不可提交，以最新问题状态为准。
          </p>
        )}
        {options.length > 0 ? (
          <div className="choice-list">
            {options.map((option) => (
              <button
                key={option.id}
                className="secondary"
                disabled={!active}
                onClick={() =>
                  void onAction(event, "answers", {
                    question_id: data.question_id,
                    option_id: option.id,
                  })
                }
              >
                {option.label}
              </button>
            ))}
          </div>
        ) : (
          <form onSubmit={(e) => void submit(e)}>
            {formError && <Notice kind="error">{formError}</Notice>}
            {Array.isArray(data.errors) && (
              <p className="muted small">
                {data.errors.map(String).join("；")}
              </p>
            )}
            {fields.map((field) => (
              <label key={field.name}>
                {field.label || field.name}
                {field.unit && !(field.label ?? "").includes(field.unit) ? `（${field.unit}）` : ""}
                {field.required ? " *" : ""}
                {usesJsonInput(field) ? (
                  <textarea
                    disabled={!active}
                    required={field.required}
                    value={values[field.name] ?? ""}
                    onChange={(e) =>
                      setValues({ ...values, [field.name]: e.target.value })
                    }
                    placeholder="请输入 JSON 值"
                    rows={2}
                  />
                ) : field.type === "boolean" ? (
                  <select
                    disabled={!active}
                    required={field.required}
                    value={values[field.name] ?? ""}
                    onChange={(e) =>
                      setValues({ ...values, [field.name]: e.target.value })
                    }
                  >
                    <option value="">请选择</option>
                    <option value="true">是</option>
                    <option value="false">否</option>
                  </select>
                ) : (
                  <input
                    disabled={!active}
                    required={field.required}
                    type={
                      field.type === "number" || field.type === "integer"
                        ? "number"
                        : "text"
                    }
                    step={field.type === "integer" ? 1 : "any"}
                    value={values[field.name] ?? ""}
                    onChange={(e) =>
                      setValues({ ...values, [field.name]: e.target.value })
                    }
                  />
                )}
              </label>
            ))}
            <button className="primary" disabled={!active}>
              提交补充信息
            </button>
          </form>
        )}
      </section>
    );
  }
  if (event.event_type === "CONFIRMATION")
    return (
      <section className="result-card confirmation">
        <header>
          <ShieldCheck size={20} />
          <h3>{String(data.message ?? "执行前请确认")}</h3>
        </header>
        <strong>{String(data.name ?? "业务操作")}</strong>
        <dl className="detail-grid">
          {Object.entries(
            (data.arguments as Record<string, unknown>) ?? {},
          ).map(([key, value]) => (
            <div key={key}>
              <dt>{businessLabel(key)}</dt>
              <dd>{valueText(value)}</dd>
            </div>
          ))}
        </dl>
        <p className="muted small">
          确认后将执行以上业务操作。
          {data.expires_at
            ? `有效期至 ${new Date(String(data.expires_at)).toLocaleString()}`
            : ""}
        </p>
        <div className="button-row">
          <button
            className="primary"
            disabled={!active}
            onClick={() =>
              void onAction(event, "confirmations", { confirmed: true })
            }
          >
            确认执行
          </button>
          <button
            className="secondary"
            disabled={!active}
            onClick={() =>
              void onAction(event, "confirmations", { confirmed: false })
            }
          >
            取消
          </button>
        </div>
        {!active && <p className="muted small">当前确认已不可操作。</p>}
      </section>
    );
  if (event.event_type !== "RESULT") return null;
  const result = data.data;
  const component = String(data.component ?? "detail");
  const resultObject = result && typeof result === "object" && !Array.isArray(result)
    ? result as Record<string, unknown> : null;
  const namedRows = resultObject && Object.entries(resultObject).find(([, value]) =>
    Array.isArray(value) && value.every(item => item !== null && typeof item === "object" && !Array.isArray(item)));
  const rows = Array.isArray(result)
    ? result
    : resultObject && Array.isArray(resultObject.rows)
      ? resultObject.rows
      : namedRows
      ? namedRows[1] as unknown[]
      : null;
  const fields = Array.isArray(data.fields)
    ? (data.fields as { label: string; value: unknown }[])
    : result && typeof result === "object" && !Array.isArray(result)
      ? Object.entries(result).map(([label, value]) => ({ label, value }))
      : [{ label: "结果", value: result }];
  const tableRows = rows?.filter(
    (row): row is Record<string, unknown> =>
      row !== null && typeof row === "object" && !Array.isArray(row),
  );
  const columns = tableRows
    ? Array.from(new Set(tableRows.flatMap((row) => Object.keys(row))))
    : [];
  return (
    <section className="result-card">
      <header>
        <CheckCircle2 size={18} />
        <h3>{String(data.title ?? "业务结果")}</h3>
        <Badge tone={data.status === "UNKNOWN" ? "warning" : "success"}>
          {statuses[String(data.status)] || String(data.status ?? "已返回")}
        </Badge>
      </header>
      {data.result_unavailable === true ? (
        <p className="result-text" role="status">业务操作已确认成功，但返回内容未通过已发布的展示契约。请按调用记录核对结果；此处不会重新提交业务。</p>
      ) : component === "table" && tableRows ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{businessLabel(column)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row, index) => (
                <tr key={index}>
                  {columns.map((column) => (
                    <td key={column}>{valueText(row[column])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {!tableRows.length && (
            <p className="muted">接口已返回，暂无结果行。</p>
          )}
        </div>
      ) : component === "file" ? null : component === "markdown" ? (
        <SafeMarkdown text={valueText(result)} />
      ) : component === "citation" ? (
        <div className="citation-result"><SafeMarkdown text={typeof result === "string" ? result : valueText(result)} /><p className="muted small">仅展示服务端准许的引用内容；文件通过授权下载读取。</p></div>
      ) : component === "text" ? (
        <p className="result-text">{valueText(result)}</p>
      ) : (
        <dl className="detail-grid">
          {fields.map((field, index) => (
            <div key={`${field.label}-${index}`}>
              <dt>{businessLabel(field.label)}</dt>
              <dd>{valueText(field.value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {data.result_unavailable !== true && (component === "file" || (Array.isArray(data.files) && data.files.length > 0)) && (
        <div className="runtime-files">
          {formError && <Notice kind="error">{formError}</Notice>}
          {(Array.isArray(data.files) ? data.files as RuntimeFile[] : []).map(file => <div className="runtime-file" key={file.id}><FileText size={20} /><span><strong>{file.filename}</strong><small>{file.media_type} · {Math.ceil(file.size / 1024)} KB</small></span><button className="secondary" disabled={!onDownload || downloading !== null} onClick={() => {
            setDownloading(file.id); setFormError("");
            void onDownload?.(file).catch(e => setFormError(e instanceof Error ? e.message : "文件下载失败")).finally(() => setDownloading(null));
          }}>{downloading === file.id ? "正在读取…" : "下载"}</button></div>)}
          {!Array.isArray(data.files) || !data.files.length ? <p className="muted">服务端未返回可授权下载的文件引用。</p> : null}
        </div>
      )}
      <footer className="provenance">
        已返回业务结果
        {Boolean(data.fixture_mode) && (
          <Badge tone="warning">开发夹具结果</Badge>
        )}
      </footer>
    </section>
  );
}
