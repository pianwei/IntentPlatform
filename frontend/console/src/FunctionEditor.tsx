import { SemanticMappingEditor } from "./SemanticMappingEditor";
import { useState, type FormEvent } from "react";
import { Check, ChevronRight, Code2, X } from "lucide-react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import type { Capability, FunctionDefinition, Validation } from "../../shared/types";
import { OpenAPIWizard } from "./OpenAPIWizard";
import { HttpMappingEditor } from "./HttpMappingEditor";
import { SourceReviewPanel } from "./SourceReviewPanel";
import type { SourceReview } from "../../shared/sources";
import { httpEditorError } from "../../shared/httpMapping";
import { jsonEqual } from "../../shared/jsonEquality";
import { CredentialSelector } from "./CredentialSelector";
import { OutputMappingEditor } from "./OutputMappingEditor";
export const blankDefinition: FunctionDefinition = {
  name: "",
  description: "",
  object_type: "",
  action: "READ",
  result_type: "",
  aliases: [],
  input_schema: {
    type: "object",
    properties: {},
    required: [],
    additionalProperties: false,
  },
  output_schema: null,
  connector: {
    kind: "http",
    endpoint: null,
    method: "GET",
    credential_ref: null,
    timeout_seconds: 10,
    idempotency_header: null,
    status_endpoint: null,
  },
  effect: "READ_ONLY",
  requires_confirmation: false,
  allowed_users: [],
  preconditions: [],
  presentation: { component: "detail", title: "查询结果", field_map: {}, files_field: null },
  positive_examples: [],
  negative_examples: [],
};
export function parseFunctionDraft(raw: string): FunctionDefinition {
  const value = JSON.parse(raw);
  const object = (v: unknown) => !!v && typeof v === "object" && !Array.isArray(v);
  if (!object(value) || !object(value.connector) || !object(value.input_schema) || !object(value.presentation)) throw new Error("定义、connector、input_schema 和 presentation 必须是对象");
  if (value.output_schema !== undefined && value.output_schema !== null && !object(value.output_schema)) throw new Error("output_schema 必须是对象或 null");
  if (!object(value.presentation.field_map)) throw new Error("presentation.field_map 必须是字段映射对象");
  for (const key of ["name", "description", "object_type", "action", "result_type", "effect"]) if (typeof value[key] !== "string") throw new Error(`${key} 必须是字符串`);
  if (typeof value.connector.kind !== "string" || typeof value.connector.method !== "string") throw new Error("connector.kind 与 method 必须是字符串");
  const httpError = httpEditorError(value.connector);
  if (httpError) throw new Error(httpError);
  for (const key of ["domain_refs", "participant_roles", "relations", "result_bindings"]) if (value[key] !== undefined && (!Array.isArray(value[key]) || value[key].some((row: unknown) => !object(row)))) throw new Error(`${key} 必须是对象数组`);
  for (const key of ["argument_bindings", "parameter_semantics"]) if (value[key] !== undefined && (!object(value[key]) || Object.values(value[key]).some(row => !object(row)))) throw new Error(`${key} 必须是映射对象`);
  return value as FunctionDefinition;
}
export function FunctionEditor({
  api,
  workspace,
  item,
  writable,
  publishable = writable,
  onClose,
  onSaved,
}: {
  api: ApiClient;
  workspace: string;
  item: Capability | null;
  writable: boolean;
  publishable?: boolean;
  onClose: () => void;
  onSaved: (item: Capability) => Promise<void>;
}) {
  const [json, setJson] = useState(
    JSON.stringify(item?.definition ?? blankDefinition, null, 2),
  );
  const [advanced, setAdvanced] = useState(() => {
    try { parseFunctionDraft(JSON.stringify(item?.definition ?? blankDefinition)); return false; }
    catch { return true; }
  });
  const [importMode, setImportMode] = useState(false);
  const [sourceReview, setSourceReview] = useState<SourceReview | null>(null);
  const [sourceDirty, setSourceDirty] = useState(false);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [failureArgs, setFailureArgs] = useState("{}");
  const [testArgs, setTestArgs] = useState("{}");
  const [probe, setProbe] = useState(false);
  const [allowEffects, setAllowEffects] = useState(false);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  let definition: FunctionDefinition = blankDefinition;
  let draftError = "";
  try { definition = parseFunctionDraft(json); } catch (e) { draftError = errorText(e); }
  const dirty = !!draftError || !jsonEqual(definition, item?.definition ?? blankDefinition);
  function update(patch: Partial<FunctionDefinition>) {
    try { const latest = parseFunctionDraft(json); setJson(JSON.stringify({ ...latest, ...patch }, null, 2)); setValidation(null); }
    catch (e) { setError(errorText(e)); }
  }
  async function perform(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    await perform(async () => {
      const value = parseFunctionDraft(json);
      const saved = item
        ? await api.patch<Capability>(
            `${management(workspace)}/functions/${item.id}/draft`,
            { revision: item.revision, definition: value },
          )
        : await api.post<Capability>(
            `${management(workspace)}/functions/import`,
            { definition: value },
          );
      setJson(JSON.stringify(saved.definition, null, 2));
      setValidation(null);
      await onSaved(saved);
    });
  }
  return (
    <div className="drawer-backdrop">
      <section
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={item ? "管理功能" : "接入新功能"}
      >
        <div className="drawer-heading">
          <div>
            <div className="eyebrow">
              CAPABILITY / {item ? "映射与版本" : "自助接入"}
            </div>
            <h2>{item?.name || "接入新功能"}</h2>
          </div>
          <button aria-label="关闭功能编辑" onClick={onClose}>
            <X size={21} />
          </button>
        </div>
        <div className="drawer-body">
          {error && <Notice kind="error">{error}</Notice>}
          {sourceDirty && <Notice>引用列表尚未保存。请先保存引用或还原引用修改，再编辑功能定义。</Notice>}
          {sourceBusy && <Notice>正在处理来源，请等待完成后编辑功能定义。</Notice>}
          <div className="stepper">
            <span className="current">1 接口与说明</span>
            <ChevronRight size={15} />
            <span>2 映射确认</span>
            <ChevronRight size={15} />
            <span>3 验证发布</span>
          </div>
          {!item && <div className="button-row"><button type="button" className="secondary" onClick={() => setImportMode(!importMode)}>{importMode ? "返回手工接入草稿" : "从 OpenAPI 文档接入"}</button></div>}
          {!item && importMode && <OpenAPIWizard api={api} workspace={workspace} writable={writable} onCreated={onSaved} />}
          {(!importMode || item) && <form onSubmit={(e) => void save(e)}>
            <fieldset disabled={!writable || busy || sourceDirty || sourceBusy}>
              <fieldset disabled={!!draftError}><h3>功能定义</h3>
              <div className="form-grid">
                <label>
                  功能名称
                  <input
                    required
                    value={definition.name}
                    onChange={(e) => update({ name: e.target.value })}
                    placeholder="填写真实业务用途"
                  />
                </label>
                <label>
                  接入方式
                  <select
                    value={definition.connector.kind}
                    onChange={(e) =>
                      update({
                        connector: {
                          ...definition.connector,
                          kind: e.target
                            .value as FunctionDefinition["connector"]["kind"],
                        },
                      })
                    }
                  >
                    <option value="http">HTTP / OpenAPI</option>
                    <option value="dify">内网 Dify 工作流</option>
                    <option value="hiagent">内网 HiAgent 工作流</option>
                    {definition.connector.kind === "fixture" && (
                      <option value="fixture">开发夹具</option>
                    )}
                  </select>
                </label>
                <label className="span-2">
                  业务描述
                  <textarea
                    required
                    rows={3}
                    value={definition.description}
                    onChange={(e) => update({ description: e.target.value })}
                    placeholder="说明功能用途、适用范围与返回内容"
                  />
                </label>
                <label className="span-2">
                  接口地址
                  <input
                    type="url"
                    value={definition.connector.endpoint ?? ""}
                    onChange={(e) =>
                      update({
                        connector: {
                          ...definition.connector,
                          endpoint: e.target.value || null,
                        },
                      })
                    }
                    placeholder="由服务端内网地址白名单校验"
                  />
                </label>
                <label>
                  HTTP 方法
                  <select
                    value={definition.connector.method}
                    onChange={(e) =>
                      update({
                        connector: {
                          ...definition.connector,
                          method: e.target.value,
                        },
                      })
                    }
                  >
                    {["GET", "POST"].map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                </label>
                <CredentialSelector api={api} workspace={workspace} kind={definition.connector.kind} value={definition.connector.credential_ref} onChange={credential_ref => update({ connector: { ...definition.connector, credential_ref } })} />
                <label>
                  业务对象
                  <input
                    required
                    value={definition.object_type}
                    onChange={(e) => update({ object_type: e.target.value })}
                    placeholder="如已确认的领域概念 ID"
                  />
                </label>
                <label>
                  动作
                  <input
                    required
                    value={definition.action}
                    onChange={(e) => update({ action: e.target.value })}
                  />
                </label>
                <label>
                  结果类型
                  <input
                    required
                    value={definition.result_type}
                    onChange={(e) => update({ result_type: e.target.value })}
                  />
                </label>
                <label>
                  执行效果
                  <select
                    value={definition.effect}
                    onChange={(e) =>
                      update({
                        effect: e.target.value as FunctionDefinition["effect"],
                        requires_confirmation: e.target.value === "WRITE",
                      })
                    }
                  >
                    <option value="READ_ONLY">只读</option>
                    <option value="WRITE">写操作 · 必须确认</option>
                  </select>
                </label>
              </div>
              {!draftError && <HttpMappingEditor definition={definition} update={update} />}
              {!draftError && <OutputMappingEditor definition={definition} update={update} />}
              {!draftError && <SemanticMappingEditor api={api} workspace={workspace} definition={definition} update={update} />}
              </fieldset>
              {draftError && <Notice kind="error">高级映射无效：{draftError}。请修复 JSON 后再编辑表单、保存或验证；输入已保留。</Notice>}
              <div className="mapping-heading">
                <h3>参数、前置条件与展示映射</h3>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => setAdvanced(!advanced)}
                >
                  <Code2 size={16} />
                  {advanced ? "收起 JSON 映射" : "编辑完整 JSON 映射"}
                </button>
              </div>
              {advanced ? (
                <label>
                  FunctionDefinition
                  <textarea
                    className="code-input"
                    rows={18}
                    value={json}
                    onChange={(e) => { setJson(e.target.value); setValidation(null); }}
                    spellCheck={false}
                  />
                </label>
              ) : (
                <p className="muted small">
                  服务端会验证输入
                  Schema、业务映射、前置条件及字段映射；草稿不会影响已发布版本。
                </p>
              )}
              <button className="primary" disabled={busy || !writable || !!draftError || sourceDirty || sourceBusy}>
                <Check size={16} />
                {item ? "保存新草稿" : "创建功能草稿"}
              </button>
            </fieldset>
          </form>}
          {item && <SourceReviewPanel api={api} workspace={workspace} item={item} writable={writable} dirty={dirty} busy={busy} onReview={setSourceReview} onDirtyChange={setSourceDirty} onBusyChange={setSourceBusy} onChanged={async saved => { setValidation(null); await onSaved(saved); }} />}
          {item && (
            <section className="validation-panel">
              <h3>验证与发布</h3>
              {dirty && <Notice>有尚未保存的映射，请先保存草稿后验证。</Notice>}
              {(!sourceReview?.ready || sourceReview.revision !== item.revision) && <Notice>来源审查尚未就绪，需读取并处理当前修订的来源后再验证发布。</Notice>}
              <label>
                契约测试参数（JSON）
                <textarea
                  rows={3}
                  className="code-input"
                  value={testArgs}
                  onChange={(e) => setTestArgs(e.target.value)}
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={probe}
                  onChange={(e) => setProbe(e.target.checked)}
                />
                调用真实测试接口（按已授权测试环境执行）
              </label>
              {sourceReview?.api_source && <><Notice>OpenAPI 导入功能需要成功与预期 FAILED 的失败样例探测。勾选真实测试后将调用两组参数；写操作的两次调用均需明确允许效果。静态检查不能通过此发布门禁。</Notice><label>预期失败样例参数（JSON）<textarea rows={3} className="code-input" value={failureArgs} onChange={e => setFailureArgs(e.target.value)} /></label></>}
              {definition.effect === "WRITE" && probe && (
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={allowEffects}
                    onChange={(e) => setAllowEffects(e.target.checked)}
                  />
                  确认本次测试允许产生接口所声明的写入效果
                </label>
              )}
              <div className="button-row">
                <button
                  className="secondary"
                  disabled={
                    !writable ||
                    busy ||
                    dirty ||
                    !sourceReview?.ready || sourceReview.revision !== item.revision ||
                    (probe && definition.effect === "WRITE" && !allowEffects)
                  }
                  onClick={() =>
                    void perform(async () =>
                      setValidation(
                        await api.post<Validation>(
                          `${management(workspace)}/functions/${item.id}/validate`,
                          {
                            revision: item.revision,
                            arguments: JSON.parse(testArgs),
                            ...(sourceReview?.api_source ? { failure_arguments: JSON.parse(failureArgs) } : {}),
                            probe,
                            allow_effects: allowEffects,
                          },
                        ),
                      ),
                    )
                  }
                >
                  验证当前草稿
                </button>
                <button
                  className="primary"
                  disabled={
                    !writable ||
                    busy ||
                    dirty ||
                    !sourceReview?.ready || sourceReview.revision !== item.revision ||
                    !publishable ||
                    !((validation?.valid && validation.revision === item.revision) || item.status === "validated")
                  }
                  onClick={() =>
                    void perform(async () =>
                      onSaved(
                        await api.post<Capability>(
                          `${management(workspace)}/functions/${item.id}/publish`,
                          { revision: item.revision },
                        ),
                      ),
                    )
                  }
                >
                  发布功能版本
                </button>
                <button
                  className="danger-link"
                  disabled={!writable || busy || item.status === "disabled"}
                  onClick={() =>
                    void perform(async () =>
                      onSaved(
                        await api.post<Capability>(
                          `${management(workspace)}/functions/${item.id}/disable`,
                        ),
                      ),
                    )
                  }
                >
                  停用功能
                </button>
              </div>
              {validation && (
                <div className="validation-result">
                  <Notice kind={validation.valid ? "success" : "error"}>
                    {validation.valid
                      ? "当前草稿通过所选验证"
                      : "验证未通过，请处理下列问题"}
                  </Notice>
                  {validation.errors.map((message) => (
                    <p key={message}>{message}</p>
                  ))}
                  <pre>
                    {JSON.stringify(
                      { checks: validation.checks, probe: validation.probe },
                      null,
                      2,
                    )}
                  </pre>
                  {!probe && (
                    <p className="muted">
                      本次未调用真实接口，不代表真实业务联调通过。
                    </p>
                  )}
                </div>
              )}
            </section>
          )}
        </div>
      </section>
    </div>
  );
}
