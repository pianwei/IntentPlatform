import { useEffect, useRef, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import { REVIEW_FIELDS, type Citation, type SourceDocument, type SourceReview } from "../../shared/sources";
import type { Capability } from "../../shared/types";
import { DocumentContent, DocumentLibrary } from "./DocumentLibrary";
import { jsonEqual } from "../../shared/jsonEquality";

function withoutAttribution(citation: Citation) { const { claimed_by: _claimedBy, ...value } = citation; return value; }
export function SourceReviewPanel({ api, workspace, item, writable, dirty, busy: parentBusy, onReview, onChanged, onDirtyChange, onBusyChange }: { api: ApiClient; workspace: string; item: Capability; writable: boolean; dirty: boolean; busy: boolean; onReview: (value: SourceReview | null) => void; onDirtyChange?: (value: boolean) => void; onBusyChange?: (value: boolean) => void; onChanged: (item: Capability) => Promise<void> }) {
  const [review, setReview] = useState<SourceReview | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [document, setDocument] = useState<SourceDocument | null>(null);
  const [sectionId, setSectionId] = useState(""); const [field, setField] = useState("description");
  const [quote, setQuote] = useState(""); const [value, setValue] = useState('""'); const [editing, setEditing] = useState<number | null>(null);
  const [citationOpen, setCitationOpen] = useState(false);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const generation = useRef(0); const callback = useRef(onReview); callback.current = onReview;
  const base = `${management(workspace)}/functions/${encodeURIComponent(item.id)}`;
  const sourceDirty = !!review && !jsonEqual(citations.map(withoutAttribution), (review.citations ?? []).map(withoutAttribution));
  const blocked = !writable || dirty || parentBusy || busy || !review || review.revision !== item.revision;
  async function load() { const current = ++generation.current; setError(""); setReview(null); callback.current(null); try { const next = await api.get<SourceReview>(`${base}/source-review`); if (current !== generation.current) return; if (!Number.isInteger(next.revision) || !Array.isArray(next.issues) || !Array.isArray(next.citations)) throw new Error("来源审查响应结构不可用，请核对后端契约。"); setReview(next); setCitations(next.citations ?? []); callback.current(next); } catch (e) { if (current === generation.current) setError(errorText(e)); } }
  useEffect(() => { void load(); return () => { ++generation.current; }; }, [api, workspace, item.id, item.revision]);
  // Uncommitted citation changes must block function validation too.
  useEffect(() => { callback.current(sourceDirty ? null : review); }, [sourceDirty, review]);
  useEffect(() => { onDirtyChange?.(sourceDirty); }, [sourceDirty, onDirtyChange]);
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);
  async function perform(fn: () => Promise<void>) { setBusy(true); setError(""); try { await fn(); } catch (e) { setError(`${errorText(e)}。当前输入与理由已保留，请核对服务器修订后重试。`); } finally { setBusy(false); } }
  function receive(next: SourceReview) { setReview(next); setCitations(next.citations ?? []); callback.current(next); }
  async function changed(next: SourceReview) { receive(next); await onChanged({ ...item, revision: next.revision, status: "draft", validation: null }); }
  async function readSource(id: string, revision: number) { const source = await api.get<SourceDocument>(`${management(workspace)}/documents/${encodeURIComponent(id)}/versions/${revision}`); setDocument(source); setCitationOpen(true); return source; }
  function addCitation() {
    try {
      if (!document) throw new Error("请先固定一个来源文档修订。");
      const section = document.sections.find(s => s.id === sectionId);
      if (!section || !quote.trim() || !section.text.includes(quote)) throw new Error("引用摘录必须逐字出现在所选原文章节中。");
      const citation: Citation = { field_path: field, document_id: document.id, revision: document.revision, section_id: sectionId, quote, proposed_value: JSON.parse(value) };
      setCitations(editing === null ? [...citations, citation] : citations.map((old, index) => index === editing ? citation : old)); setEditing(null); setError("");
    } catch (e) { setError(errorText(e)); }
  }
  return <section className="source-review"><div className="mapping-heading"><h3>来源候选、冲突与审查</h3><button type="button" className="text-button" disabled={busy || sourceDirty} onClick={() => void load()}>重新读取来源审查</button></div>
    {error && <Notice kind="error">{error}</Notice>}{dirty && <Notice>功能映射有未保存修改，先保存草稿再维护来源或确认当前值。</Notice>}{sourceDirty && <Notice>引用有未保存修改。保存引用将增加功能修订，原验证和审查确认失效。</Notice>}
    {review && <><Notice kind={review.ready && !sourceDirty ? "success" : "info"}>{review.required ? `修订 ${review.revision}：${review.ready ? "来源审查已就绪" : "仍有待处理冲突或业务解释"}` : "该功能当前无需来源审查；新增引用后会进入审查流程。"}</Notice>
      {review.api_source && <div className="source-choice"><strong>API 原始来源</strong><p>{review.api_source.operation_key} · 文档 r{review.api_source.revision} · {review.api_source.parser_version}</p><code>{review.api_source.pointer}</code><button type="button" className="text-button" onClick={() => void perform(async () => { await readSource(review.api_source!.document_id, review.api_source!.revision); })}>查看 API 来源原文</button><details className="source-section"><summary>全部 API 提取事实（与人工引用分开）</summary><pre>{JSON.stringify(review.api_facts ?? {}, null, 2)}</pre></details></div>}
      <h4>已记录与待保存的文档引用</h4>{!citations.length && <p className="muted">尚无人工引用。</p>}{citations.map((citation, index) => <div className="source-candidate" key={index}><strong>{citation.field_path}</strong><p>{citation.document_id} · r{citation.revision} · {citation.section_id}</p><blockquote>{citation.quote}</blockquote><pre>{JSON.stringify(citation.proposed_value, null, 2)}</pre>{citation.claimed_by && <small>声明人：{citation.claimed_by}</small>}<div className="button-row"><button type="button" className="text-button" disabled={blocked} onClick={() => void perform(async () => { await readSource(citation.document_id, citation.revision); setField(citation.field_path); setSectionId(citation.section_id); setQuote(citation.quote); setValue(JSON.stringify(citation.proposed_value, null, 2)); setEditing(index); })}>编辑引用 {index + 1}</button><button type="button" className="danger-link" disabled={blocked} onClick={() => { setCitations(citations.filter((_, i) => i !== index)); setEditing(null); }}>移除引用 {index + 1}</button></div></div>)}
      <div className="button-row">{sourceDirty && <button type="button" className="text-button" disabled={blocked} onClick={() => { setCitations(review.citations ?? []); setEditing(null); }}>还原引用修改</button>}<button type="button" className="secondary" disabled={blocked} onClick={() => { setCitationOpen(!citationOpen); setEditing(null); }}>添加文档引用</button><button type="button" className="primary" disabled={blocked || !sourceDirty} onClick={() => void perform(async () => { const next = await api.put<SourceReview>(`${base}/sources`, { revision: item.revision, citations: citations.map(withoutAttribution) }); await changed(next); })}>保存完整引用列表</button></div>
      {citationOpen && <><fieldset disabled={blocked}><DocumentLibrary api={api} workspace={workspace} writable={writable && !blocked} onSelect={d => { setDocument(d); setSectionId(""); setQuote(""); setEditing(null); }} /></fieldset>{document && <><p className="source-choice">引用固定：{document.filename} · r{document.revision}</p><DocumentContent api={api} workspace={workspace} document={document} /><fieldset disabled={blocked}><label>引用字段<select value={field} onChange={e => setField(e.target.value)}>{REVIEW_FIELDS.map(f => <option key={f}>{f}</option>)}</select></label><label>原文章节<select value={sectionId} onChange={e => { setSectionId(e.target.value); setQuote(""); }}><option value="">选择章节或段落</option>{document.sections.map(s => <option value={s.id} key={s.id}>{s.heading || "正文"} · 第 {s.start_line}–{s.end_line} 行</option>)}</select></label>{sectionId && <pre className="source-candidate">{document.sections.find(s => s.id === sectionId)?.text}</pre>}<label>逐字原文摘录<textarea rows={3} maxLength={6000} value={quote} onChange={e => setQuote(e.target.value)} /></label><label>候选解释值（JSON，字符串需双引号）<textarea rows={3} value={value} onChange={e => setValue(e.target.value)} /></label><button type="button" className="secondary" onClick={addCitation}>{editing === null ? "加入待保存引用" : "更新待保存引用"}</button></fieldset></>}</>}
      <h4>当前值与候选来源</h4>{review.issues.map(issue => <section className={`source-review-issue ${issue.kind}`} key={issue.field_path}><h4>{issue.field_path} · {issue.kind === "conflict" ? "存在冲突" : "业务解释需审查"} · {issue.resolved ? "已确认" : "待确认"}</h4><p>当前已保存值</p><pre>{JSON.stringify(issue.current_value, null, 2)}</pre>{issue.candidates.map((candidate, index) => <div className="source-candidate" key={index}><strong>{candidate.kind === "api" ? "API 提取事实" : "人工文档引用"}</strong><pre>{JSON.stringify(candidate.value, null, 2)}</pre>{candidate.kind === "api" ? <p>原文指针：{candidate.source_pointer} · 引用目标：{JSON.stringify(candidate.ref_targets ?? [])}</p> : <><p>{candidate.document_id} · r{candidate.revision} · {candidate.section_id} · 声明人 {candidate.claimed_by}</p><blockquote>{candidate.quote}</blockquote></>}</div>)}{!issue.candidates.length && <p className="muted">没有可自动断言的来源事实，请依据业务规则核对当前值。</p>}{issue.resolved ? <p>已确认理由：{issue.reason}</p> : <><label>确认理由：{issue.field_path}<textarea minLength={3} maxLength={2000} disabled={blocked || sourceDirty} value={reasons[issue.field_path] ?? ""} onChange={e => setReasons({ ...reasons, [issue.field_path]: e.target.value })} /></label><button type="button" className="secondary" disabled={blocked || sourceDirty || (reasons[issue.field_path] ?? "").trim().length < 3} onClick={() => void perform(async () => { const next = await api.post<SourceReview>(`${base}/source-review/confirm`, { revision: item.revision, resolutions: [{ field_path: issue.field_path, reason: reasons[issue.field_path].trim() }] }); await changed(next); })}>确认当前值：{issue.field_path}</button></>}</section>)}
      <p className="muted small">确认只记录您对当前值及来源的解释，不自动改写功能定义。需要采用另一候选时，先修改并保存对应表单或 JSON，再重新审查。</p>
    </>}
  </section>;
}
