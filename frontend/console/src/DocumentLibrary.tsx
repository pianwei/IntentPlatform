import { useEffect, useRef, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import { saveBlob, type DocumentSummary, type SourceDocument } from "../../shared/sources";
import { idempotencyKey, ingestBase, pollIngestJob, IngestJobFailedError, type IngestStatus, type IngestAccepted } from "../../shared/ingest";
import "./SourceDocuments.css";

export function DocumentLibrary({ api, workspace, writable, onSelect, onBusyChange, onDirtyChange }: { api: ApiClient; workspace: string; writable: boolean; onSelect?: (document: SourceDocument) => void; onBusyChange?: (busy: boolean) => void; onDirtyChange?: (dirty: boolean) => void }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [versions, setVersions] = useState<SourceDocument[]>([]);
  const [document, setDocument] = useState<SourceDocument | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [revisionMode, setRevisionMode] = useState(false);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [jobStatus, setJobStatus] = useState<IngestStatus | null>(null);
  const generation = useRef(0); const uploadInput = useRef<HTMLInputElement>(null);
  const uploadGeneration = useRef(0); const uploadAbort = useRef<AbortController | null>(null);
  const submission = useRef<{ file: File; revisionMode: boolean; selected: string; latest?: number; key: string } | null>(null);
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);
  useEffect(() => { onDirtyChange?.(!!file); }, [file, onDirtyChange]);
  useEffect(() => () => { onBusyChange?.(false); onDirtyChange?.(false); }, [onBusyChange, onDirtyChange]);
  const base = `${management(workspace)}/documents`;
  async function reload() { setDocuments(await api.get<DocumentSummary[]>(base)); }
  useEffect(() => { void reload().catch(e => setError(errorText(e))); return () => { ++generation.current; ++uploadGeneration.current; uploadAbort.current?.abort(); }; }, [api, workspace]);
  async function select(id: string) {
    const current = ++generation.current; setSelected(id); setDocument(null); setVersions([]); setError("");
    if (!id) return;
    try { const list = await api.get<SourceDocument[]>(`${base}/${encodeURIComponent(id)}/versions`); if (current === generation.current) { setVersions(list); setDocument(list[0] ?? null); } } catch (e) { if (current === generation.current) setError(errorText(e)); }
  }
  async function upload() {
    if (!file || (revisionMode && !selected)) return;
    const latest = documents.find(d => d.id === selected)?.latest_revision;
    if (revisionMode && !latest) return;
    const current = ++uploadGeneration.current;
    const controller = new AbortController(); uploadAbort.current = controller;
    const previous = submission.current;
    const key = previous?.file === file && previous.revisionMode === revisionMode && previous.selected === selected && previous.latest === latest
      ? previous.key : idempotencyKey();
    submission.current = { file, revisionMode, selected, latest, key };
    setBusy(true); setError(""); setJobStatus("PENDING");
    try {
      const body = new FormData(); body.append("file", file);
      if (revisionMode) body.append("expected_revision", String(latest));
      const accepted = await api.request<IngestAccepted>(revisionMode ? `${ingestBase(workspace)}/documents/${encodeURIComponent(selected)}/versions` : `${ingestBase(workspace)}/documents`,
        { method: "POST", body, headers: { "Idempotency-Key": key }, signal: controller.signal });
      const finished = await pollIngestJob(api, workspace, accepted.job_id, status => { if (current === uploadGeneration.current) setJobStatus(status); }, controller.signal);
      const documentId = finished.result?.document_id; const revision = finished.result?.revision;
      if (!documentId || !Number.isInteger(revision)) throw new Error("任务完成但缺少文档修订引用");
      const saved = await api.get<SourceDocument>(`${base}/${encodeURIComponent(documentId)}/versions/${revision}`, controller.signal);
      if (current !== uploadGeneration.current) return;
      await reload(); await select(documentId); setDocument(saved); setFile(null); submission.current = null;
      if (uploadInput.current) uploadInput.current.value = "";
    } catch (e) {
      if (e instanceof IngestJobFailedError) submission.current = null;
      if (current === uploadGeneration.current && !controller.signal.aborted) setError(`${errorText(e)}。文件选择已保留；版本冲突时请刷新目录并核对最新修订。`);
    }
    finally { if (current === uploadGeneration.current) { setBusy(false); setJobStatus(null); uploadAbort.current = null; } }
  }
  return <section className="source-library">
    <div className="mapping-heading"><h3>来源文档与不可变修订</h3><button type="button" className="text-button" disabled={busy} onClick={() => void reload().catch(e => setError(errorText(e)))}>刷新文档目录</button></div>
    {error && <Notice kind="error">{error}</Notice>}
    {busy && jobStatus && <Notice>接入任务：{jobStatus === "PENDING" ? "待处理" : jobStatus === "RUNNING" ? "处理中" : jobStatus === "SUCCEEDED" ? "读取结果" : "失败"}。可继续使用其他页面；文档可用后会刷新目录。</Notice>}
    <div className="form-grid"><label>来源文档<select value={selected} disabled={busy} onChange={e => void select(e.target.value)}><option value="">选择当前空间文档</option>{documents.map(d => <option key={d.id} value={d.id}>{d.filename} · 最新 r{d.latest_revision}</option>)}</select></label><label>原文修订<select disabled={!versions.length || busy} value={document?.revision ?? ""} onChange={e => setDocument(versions.find(v => v.revision === Number(e.target.value)) ?? null)}><option value="">选择修订</option>{versions.map(v => <option key={v.revision} value={v.revision}>r{v.revision} · {v.filename}</option>)}</select></label></div>
    {!documents.length && <p className="muted">尚无可用文档。上传资料后可查看原文、章节和来源修订。</p>}
    <fieldset disabled={!writable || busy} className="source-upload"><label>上传资料文件<input ref={uploadInput} type="file" accept=".txt,.md,.docx,.pdf,.json,.yaml,.yml" onChange={e => { setFile(e.target.files?.[0] ?? null); submission.current = null; }} /></label><label className="checkbox"><input type="checkbox" checked={revisionMode} onChange={e => { setRevisionMode(e.target.checked); submission.current = null; }} />为所选文档追加修订（保留旧版本）</label><button type="button" className="secondary" disabled={!file || (revisionMode && !selected)} onClick={() => void upload()}>{busy ? "正在处理任务…" : revisionMode ? "上传新修订" : "上传为新文档"}</button></fieldset>
    <p className="muted small">支持可抽取文本的 TXT、MD、DOCX、PDF 及 UTF-8 JSON/YAML。无法解析的材料显示服务端诊断；引用固定具体修订，不会随新上传自动替换。</p>
    {document && <DocumentContent api={api} workspace={workspace} document={document} onSelect={onSelect} />}
  </section>;
}
export function DocumentContent({ api, workspace, document, onSelect }: { api: ApiClient; workspace: string; document: SourceDocument; onSelect?: (document: SourceDocument) => void }) {
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const epoch = useRef(0);
  useEffect(() => { ++epoch.current; setError(""); return () => { ++epoch.current; }; }, [document.id, document.revision]);
  return <section className="document-content"><div className="mapping-heading"><strong>{document.filename} · r{document.revision}</strong><div className="button-row">{onSelect && <button type="button" className="secondary" onClick={() => onSelect(document)}>使用此文档修订</button>}<button type="button" className="text-button" disabled={!document.original_available || busy} onClick={() => { const current = epoch.current; setBusy(true); setError(""); void api.blob(`${management(workspace)}/documents/${encodeURIComponent(document.id)}/versions/${document.revision}/original`).then(blob => { if (current === epoch.current) saveBlob(blob, document.filename); }).catch(e => { if (current === epoch.current) setError(errorText(e)); }).finally(() => setBusy(false)); }}>下载原始文件</button></div></div>{!document.original_available && <Notice>该历史来源只保留抽取文本，没有可下载的原始文件。</Notice>}{error && <Notice kind="error">{error}</Notice>}<code>{document.id} / revision {document.revision}</code><details className="source-section"><summary>查看完整抽取原文</summary><pre>{document.text}</pre></details><div className="document-sections">{document.sections.map(section => <details className="source-section" key={section.id}><summary>{section.heading || "正文段落"} · 第 {section.start_line}–{section.end_line} 行</summary><code>{section.id}</code><pre>{section.text}</pre></details>)}</div></section>;
}
