import { useEffect, useRef, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import { saveBlob, type DocumentSummary, type SourceDocument } from "../../shared/sources";
import "./SourceDocuments.css";

export function DocumentLibrary({ api, workspace, writable, onSelect }: { api: ApiClient; workspace: string; writable: boolean; onSelect?: (document: SourceDocument) => void }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [versions, setVersions] = useState<SourceDocument[]>([]);
  const [document, setDocument] = useState<SourceDocument | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [revisionMode, setRevisionMode] = useState(false);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const generation = useRef(0); const uploadInput = useRef<HTMLInputElement>(null);
  const base = `${management(workspace)}/documents`;
  async function reload() { setDocuments(await api.get<DocumentSummary[]>(base)); }
  useEffect(() => { void reload().catch(e => setError(errorText(e))); return () => { ++generation.current; }; }, [api, workspace]);
  async function select(id: string) {
    const current = ++generation.current; setSelected(id); setDocument(null); setVersions([]); setError("");
    if (!id) return;
    try { const list = await api.get<SourceDocument[]>(`${base}/${encodeURIComponent(id)}/versions`); if (current === generation.current) { setVersions(list); setDocument(list[0] ?? null); } } catch (e) { if (current === generation.current) setError(errorText(e)); }
  }
  async function upload() {
    if (!file || (revisionMode && !selected)) return;
    const latest = documents.find(d => d.id === selected)?.latest_revision;
    if (revisionMode && !latest) return;
    setBusy(true); setError("");
    try {
      const body = new FormData(); body.append("file", file);
      if (revisionMode) body.append("expected_revision", String(latest));
      const saved = await api.request<SourceDocument>(revisionMode ? `${base}/${encodeURIComponent(selected)}/versions` : base, { method: "POST", body });
      await reload(); await select(saved.id); setDocument(saved); setFile(null); if (uploadInput.current) uploadInput.current.value = "";
    } catch (e) { setError(`${errorText(e)}。文件选择已保留；版本冲突时请刷新目录并核对最新修订。`); }
    finally { setBusy(false); }
  }
  return <section className="source-library">
    <div className="mapping-heading"><h3>来源文档与不可变修订</h3><button type="button" className="text-button" disabled={busy} onClick={() => void reload().catch(e => setError(errorText(e)))}>刷新文档目录</button></div>
    {error && <Notice kind="error">{error}</Notice>}
    <div className="form-grid"><label>来源文档<select value={selected} disabled={busy} onChange={e => void select(e.target.value)}><option value="">选择当前空间文档</option>{documents.map(d => <option key={d.id} value={d.id}>{d.filename} · 最新 r{d.latest_revision}</option>)}</select></label><label>原文修订<select disabled={!versions.length || busy} value={document?.revision ?? ""} onChange={e => setDocument(versions.find(v => v.revision === Number(e.target.value)) ?? null)}><option value="">选择修订</option>{versions.map(v => <option key={v.revision} value={v.revision}>r{v.revision} · {v.filename}</option>)}</select></label></div>
    {!documents.length && <p className="muted">尚无可用文档。上传资料后可查看原文、章节和来源修订。</p>}
    <fieldset disabled={!writable || busy} className="source-upload"><label>上传资料文件<input ref={uploadInput} type="file" accept=".txt,.md,.docx,.pdf,.json,.yaml,.yml" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label><label className="checkbox"><input type="checkbox" checked={revisionMode} onChange={e => setRevisionMode(e.target.checked)} />为所选文档追加修订（保留旧版本）</label><button type="button" className="secondary" disabled={!file || (revisionMode && !selected)} onClick={() => void upload()}>{busy ? "正在上传…" : revisionMode ? "上传新修订" : "上传为新文档"}</button></fieldset>
    <p className="muted small">支持可抽取文本的 TXT、MD、DOCX、PDF 及 UTF-8 JSON/YAML。无法解析的材料显示服务端诊断；引用固定具体修订，不会随新上传自动替换。</p>
    {document && <DocumentContent api={api} workspace={workspace} document={document} onSelect={onSelect} />}
  </section>;
}
export function DocumentContent({ api, workspace, document, onSelect }: { api: ApiClient; workspace: string; document: SourceDocument; onSelect?: (document: SourceDocument) => void }) {
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const epoch = useRef(0);
  useEffect(() => { ++epoch.current; setError(""); return () => { ++epoch.current; }; }, [document.id, document.revision]);
  return <section className="document-content"><div className="mapping-heading"><strong>{document.filename} · r{document.revision}</strong><div className="button-row">{onSelect && <button type="button" className="secondary" onClick={() => onSelect(document)}>使用此文档修订</button>}<button type="button" className="text-button" disabled={!document.original_available || busy} onClick={() => { const current = epoch.current; setBusy(true); setError(""); void api.blob(`${management(workspace)}/documents/${encodeURIComponent(document.id)}/versions/${document.revision}/original`).then(blob => { if (current === epoch.current) saveBlob(blob, document.filename); }).catch(e => { if (current === epoch.current) setError(errorText(e)); }).finally(() => setBusy(false)); }}>下载原始文件</button></div></div>{!document.original_available && <Notice>该历史来源只保留抽取文本，没有可下载的原始文件。</Notice>}{error && <Notice kind="error">{error}</Notice>}<code>{document.id} / revision {document.revision}</code><details className="source-section"><summary>查看完整抽取原文</summary><pre>{document.text}</pre></details><div className="document-sections">{document.sections.map(section => <details className="source-section" key={section.id}><summary>{section.heading || "正文段落"} · 第 {section.start_line}–{section.end_line} 行</summary><code>{section.id}</code><pre>{section.text}</pre></details>)}</div></section>;
}
