import { useEffect, useRef, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import { idempotencyKey, ingestBase, pollIngestJob, IngestJobFailedError, type IngestAccepted, type IngestStatus } from "../../shared/ingest";
import type { Capability } from "../../shared/types";
import type { ImportDiagnostic, OpenAPIImport, SourceDocument } from "../../shared/sources";
import { DocumentLibrary } from "./DocumentLibrary";
export function Diagnostics({ values }: { values: ImportDiagnostic[] }) { return <>{values.map((d, index) => <div key={index} className={`import-diagnostics ${d.severity}`}><strong>{d.severity === "error" ? "不支持 / 错误" : "需审查"} · {d.code}</strong><p>{d.message}</p><code>{d.pointer}</code></div>)}</>; }
export function OpenAPIWizard({ api, workspace, writable, onCreated }: { api: ApiClient; workspace: string; writable: boolean; onCreated: (item: Capability) => Promise<void> }) {
  const [document, setDocument] = useState<SourceDocument | null>(null);
  const [report, setReport] = useState<OpenAPIImport | null>(null);
  const [operationKey, setOperationKey] = useState("");
  const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [jobDiagnostics, setJobDiagnostics] = useState<ImportDiagnostic[]>([]);
  const [jobStatus, setJobStatus] = useState<IngestStatus | null>(null);
  const parseSubmission = useRef<{ documentId: string; revision: number; key: string } | null>(null);
  const parseAbort = useRef<AbortController | null>(null);
  useEffect(() => () => parseAbort.current?.abort(), [api, workspace]);
  const operation = report?.operations.find(o => o.key === operationKey);
  async function perform(fn: () => Promise<void>) { setBusy(true); setError(""); setJobDiagnostics([]); try { await fn(); } catch (e) { if (e instanceof IngestJobFailedError) { parseSubmission.current = null; setJobDiagnostics(e.diagnostics.map(d => ({ ...d, severity: "error" as const }))); } setError(errorText(e)); } finally { setBusy(false); setJobStatus(null); } }
  return <section className="openapi-wizard"><h3>从 OpenAPI 创建功能草稿</h3><Notice>上传 JSON/YAML 文档，固定来源修订后由服务端解析。支持 OpenAPI 3.0.x 的有限结构；不抓取外部引用，不调用导入的业务接口。</Notice>{error && <Notice kind="error">{error}</Notice>}<Diagnostics values={jobDiagnostics} />
    <fieldset disabled={busy}><DocumentLibrary api={api} workspace={workspace} writable={writable} onSelect={value => { setDocument(value); setReport(null); setOperationKey(""); setError(""); setJobDiagnostics([]); parseSubmission.current = null; }} /></fieldset>
    {document && <div className="source-choice">待解析：{document.filename} · r{document.revision}<button type="button" className="secondary" disabled={!writable || busy} onClick={() => void perform(async () => {
      setReport(null); setOperationKey(""); setJobStatus("PENDING");
      const previous = parseSubmission.current;
      const key = previous?.documentId === document.id && previous.revision === document.revision ? previous.key : idempotencyKey();
      parseSubmission.current = { documentId: document.id, revision: document.revision, key };
      const controller = new AbortController(); parseAbort.current = controller;
      const accepted = await api.request<IngestAccepted>(`${ingestBase(workspace)}/openapi-imports`, { method: "POST", body: JSON.stringify({ document_id: document.id, revision: document.revision }), headers: { "Idempotency-Key": key }, signal: controller.signal });
      const finished = await pollIngestJob(api, workspace, accepted.job_id, setJobStatus, controller.signal);
      if (!finished.result?.import_id) throw new Error("任务完成但缺少接口解析记录");
      setReport(await api.get<OpenAPIImport>(`${management(workspace)}/openapi-imports/${encodeURIComponent(finished.result.import_id)}`, controller.signal));
      parseSubmission.current = null; parseAbort.current = null;
    })}>服务端解析此修订</button>{busy && jobStatus && <p className="muted small">解析任务：{jobStatus === "PENDING" ? "待处理" : jobStatus === "RUNNING" ? "处理中" : jobStatus === "SUCCEEDED" ? "读取结果" : "失败"}</p>}</div>}
    {report && <><p className="muted small">解析器 {report.parser_version} · 导入记录 {report.id} · 固定文档 r{report.document_revision}</p><Diagnostics values={report.diagnostics} />{!report.valid && <Notice kind="error">该来源解析未通过，不能创建功能草稿。请处理诊断后上传新修订。</Notice>}<label>选择接口操作<select disabled={!report.valid || busy || !writable} value={operationKey} onChange={e => { setOperationKey(e.target.value); const selected = report.operations.find(o => o.key === e.target.value); setName(selected?.definition?.name ?? ""); setDescription(selected?.definition?.description ?? ""); }}><option value="">选择服务端支持的操作</option>{report.operations.map(o => <option key={o.key} value={o.key} disabled={!o.supported}>{o.key} · {o.summary || o.operation_id || "未命名"}{!o.supported ? "（不支持）" : ""}</option>)}</select></label>
    {report.operations.filter(o => !o.supported).map(o => <details className="source-section" key={o.key}><summary>{o.key} · 无法选择的原因</summary><Diagnostics values={o.diagnostics} /></details>)}
    {operation && <><Diagnostics values={operation.diagnostics} /><p className="muted">原文指针：{operation.source_pointer}</p><div className="form-grid"><label>导入功能名称<input disabled={busy || !writable} value={name} onChange={e => setName(e.target.value)} /></label><label>导入功能描述<textarea disabled={busy || !writable} value={description} onChange={e => setDescription(e.target.value)} /></label></div><details className="source-section"><summary>查看服务端提出的草稿与位置映射</summary><pre>{JSON.stringify(operation.definition, null, 2)}</pre></details><button type="button" className="primary" disabled={!writable || busy || !report.valid || !operation.supported} onClick={() => void perform(async () => { const created = await api.post<Capability>(`${management(workspace)}/openapi-imports/${encodeURIComponent(report.id)}/functions`, { operation_key: operation.key, ...(name.trim() ? { name: name.trim() } : {}), ...(description.trim() ? { description: description.trim() } : {}) }); await onCreated(created); })}>生成待审查功能草稿</button><p className="muted small">生成草稿后仍须完善业务语义、审查来源、执行成功/失败样例验证，再发布。接口安全声明不会转成凭据或访问授权。</p></>}
    </>}
  </section>;
}
