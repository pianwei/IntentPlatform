import { useState, useEffect, type FormEvent } from "react";
import {
  CheckCircle2,
  FileText,
  LoaderCircle,
  ShieldCheck,
} from "lucide-react";
import type { Choice, Field, Run, RuntimeEvent, RuntimeFile, RuntimeResultComponent } from "../../shared/types";
import { BusinessResultContent, businessLabel, valueText } from "../../shared/BusinessResult";
import { Badge, Notice } from "../../shared/ui";
import { parseFieldValues, usesJsonInput } from "../../shared/fields";
import { ValueInput } from "../../shared/SchemaValueInput";
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
const finalComponentKinds = new Set(["table", "detail", "citation", "text", "markdown"]);
function finalComponent(value: unknown): value is RuntimeResultComponent {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.title === "string" && typeof item.component === "string" && finalComponentKinds.has(item.component)
    && (item.fields === undefined || Array.isArray(item.fields) && item.fields.every(field => !!field && typeof field === "object" && typeof field.label === "string"));
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
  const [typedValues,setTypedValues]=useState<Record<string,unknown>>({});
  const [formError, setFormError] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);
  useEffect(()=>{setValues({});setTypedValues({});setFormError("");},[event.run_id,event.revision,event.payload.question_id]);
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
        for(const field of fields)if(field.schema&&field.required&&typedValues[field.name]===undefined)throw new Error(`请填写 ${field.label||field.name}`);
        await onAction(event, "answers", {
          question_id: data.question_id,
          values: {...parseFieldValues(fields.filter(field=>!field.schema), values),...typedValues},
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
            {fields.map((field) => field.schema ? <fieldset key={field.name} disabled={!active}><ValueInput field={{...field.schema,title:field.label||field.name}} value={typedValues[field.name]} name={field.name} labelPrefix="业务参数" required={field.required} change={value=>setTypedValues(previous=>{const next={...previous};if(value===undefined)delete next[field.name];else next[field.name]=value;return next;})}/></fieldset> : (
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
  const component = String(data.component ?? "detail");
  const components = Array.isArray(data.components) ? data.components.filter(finalComponent) : [];
  const details = <div className="final-result-components">{components.map((item, index) => <section className="final-result-component" key={`${index}-${item.title}`}>
    <h4>{item.title}</h4><BusinessResultContent data={{ component: item.component, data: item.data, fields: item.fields }} />
  </section>)}</div>;
  return (
    <section className={`result-card business-result result-${component}`}>
      {typeof data.answer_source === "string" && ["business", "model", "practice", "mixed"].includes(data.answer_source) && <div className="answer-source">{{ business: "业务结果", model: "模型回答", practice: "模拟陪练", mixed: "业务结果与解释" }[data.answer_source]}</div>}
      <header>
        <CheckCircle2 size={18} />
        <h3>{String(data.title ?? "业务结果")}</h3>
        <Badge tone={data.status === "UNKNOWN" ? "warning" : "success"}>
          {statuses[String(data.status)] || String(data.status ?? "已返回")}
        </Badge>
      </header>
      <BusinessResultContent data={data} />
      {data.result_unavailable !== true && components.length > 0 && details}
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
          <Badge tone="warning">模拟结果</Badge>
        )}
      </footer>
    </section>
  );
}
