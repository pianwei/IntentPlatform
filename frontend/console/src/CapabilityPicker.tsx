import { useEffect, useRef, useState } from "react";
import { Blocks, ChevronDown, Plus, Search, X } from "lucide-react";
import { Modal } from "./ModernUI";
import { ApiClient, errorText, management } from "../../shared/api";
import type { Capability, PublishedFunction } from "../../shared/types";
import "./AgentCapabilityExperience.css";

const executable = (f: Capability) => !!f.latest_version && f.status !== "disabled";
const versionKey = (f: Capability) => `${f.id}:${f.latest_version}`;

export function CapabilityPicker({ functions, selected, onChange, onCreate, api, disabled = false }: {
  functions: Capability[]; selected: string[]; onChange: (v: string[]) => void;
  onCreate?: () => void; api?: ApiClient; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [versions, setVersions] = useState<Record<string, PublishedFunction>>({});
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const catalogKey = functions.map(f => `${versionKey(f)}:${f.status}`).join("|");

  useEffect(() => {
    if (!open || !api) return;
    const request = ++generation.current;
    const targets = functions.filter(f => executable(f) && !versions[versionKey(f)]);
    if (!targets.length) { setLoading(false); setProblems({}); return; }
    setLoading(true);
    void Promise.allSettled(targets.map(async f => {
      const rows = await api.get<PublishedFunction[]>(`${management(f.workspace_id)}/functions/${encodeURIComponent(f.id)}/versions`);
      const value = rows.find(v => v.version === f.latest_version);
      if (!value) throw new Error("已发布版本未能读取，请刷新功能列表后重试。");
      return value;
    })).then(results => {
      if (request !== generation.current) return;
      const next: Record<string, PublishedFunction> = {}, errors: Record<string, string> = {};
      results.forEach((result, index) => {
        const key = versionKey(targets[index]);
        if (result.status === "fulfilled") next[key] = result.value;
        else errors[key] = errorText(result.reason);
      });
      setVersions(old => ({ ...old, ...next }));
      setProblems(errors);
      setLoading(false);
    });
    return () => { ++generation.current; };
  }, [open, api, catalogKey]);

  function display(f: Capability): Capability {
    const published = versions[versionKey(f)];
    return published ? { ...f, name: published.definition.name, description: published.definition.description, definition: published.definition } : f;
  }
  const allowed = functions.filter(executable).map(display);
  const available = allowed.filter(f => `${f.name} ${f.description}`.toLowerCase().includes(query.trim().toLowerCase()) &&
    (kind === "all" || (f.definition.execution_kind ?? "direct") === kind));
  const readable = (f: Capability) => !api || !!versions[versionKey(f)];
  const canAdd = (f: Capability) => executable(f) && readable(f);
  const newlyUnavailable = pending.some(id => !selected.includes(id) && !functions.some(f => f.id === id && canAdd(f)));
  const missing = (id: string) => functions.find(f => f.id === id);
  const show = (id: string) => { const f = missing(id); return f ? display(f) : null; };

  function begin() {
    setPending([...selected]); setQuery(""); setKind("all"); setExpanded(null); setProblems({}); setOpen(true);
  }
  function close() { ++generation.current; setLoading(false); setOpen(false); }
  function toggle(id: string) { setPending(ids => ids.includes(id) ? ids.filter(v => v !== id) : [...ids, id]); }
  function apply() {
    if (newlyUnavailable || loading || disabled) return;
    const next = [...new Set(pending)];
    if (JSON.stringify(next) !== JSON.stringify(selected)) onChange(next);
    close();
  }

  return <section className="capability-composer">
    <div className="section-caption"><h2>业务能力 <span className="badge">{selected.length} 项</span></h2><div className="button-row">
      {onCreate && <button type="button" className="text-button" disabled={disabled} onClick={onCreate}><Plus size={14}/>新建功能</button>}
      <button type="button" className="button" disabled={disabled} onClick={begin}><Plus size={14}/>添加功能</button>
    </div></div>
    <div className="capability-selected-overview">{(showAll ? selected : selected.slice(0, 6)).map(id => {
      const f = show(id);
      return <article className="capability-summary" key={id}><Blocks size={16}/><div><strong>{f?.name ?? "不可用功能"}</strong>
        <p>{f?.description || "功能资料暂不可用"}</p><small>{f?.definition.execution_kind === "composite" ? "组合流程" : "直接调用"}{f?.status === "disabled" ? " · 已停用，请移除后发布" : f?.latest_version ? ` · v${f.latest_version}` : " · 未发布"}</small>
      </div><button type="button" className="icon-button" disabled={disabled} aria-label={`移除 ${f?.name ?? "不可用功能"}`} onClick={() => onChange(selected.filter(v => v !== id))}><X size={14}/></button></article>;
    })}{!selected.length && <p className="capability-empty">还未添加业务能力。可以选择已有功能，或先新建一个功能。</p>}</div>
    {selected.length > 6 && <button type="button" className="text-button capability-overview-toggle" onClick={() => setShowAll(v => !v)}>{showAll ? "收起已选能力" : `查看全部 ${selected.length} 项能力`}<ChevronDown size={14}/></button>}

    {open && <Modal title="添加业务能力" wide onClose={close} busy={disabled}>
      <div className="capability-selection-dialog">
        <div className="capability-selection-toolbar"><label className="capability-search"><Search size={16}/><input aria-label="查找可用功能" placeholder="搜索名称或业务目标" value={query} onChange={e => setQuery(e.target.value)}/></label>
          <div className="filter-tabs" aria-label="功能类别">{[["all", "全部"], ["direct", "直接调用"], ["composite", "组合功能"]].map(([v, label]) => <button type="button" aria-pressed={kind === v} className={kind === v ? "active" : ""} key={v} onClick={() => setKind(v)}>{label}</button>)}</div>
        </div>
        <div className="capability-selection-columns">
          <section className="capability-catalog" aria-label="可用功能"><div className="capability-list-heading"><h3>可用功能 <span>{available.length}</span></h3><button type="button" className="text-button" disabled={loading || disabled || !available.some(f => canAdd(f) && !pending.includes(f.id))} onClick={() => setPending(ids => [...new Set([...ids, ...available.filter(canAdd).map(f => f.id)])])}>选择当前结果</button></div>
            {loading && <p className="capability-load" role="status">正在读取已发布功能…</p>}
            <div className="capability-catalog-scroll">{available.map(f => <article className={`capability-option ${pending.includes(f.id) ? "chosen" : ""}`} key={f.id}>
              <label className="capability-choice"><input type="checkbox" aria-label={`选择功能 ${f.name}`} checked={pending.includes(f.id)} disabled={disabled || (!pending.includes(f.id) && !canAdd(f))} onChange={() => toggle(f.id)}/><span><strong>{f.name}</strong><small>{f.definition.execution_kind === "composite" ? "组合功能" : "直接调用"} · v{f.latest_version}</small>{readable(f) && <p>{f.description}</p>}</span></label>
              {problems[versionKey(f)] && <p className="capability-load-error" role="alert">{problems[versionKey(f)]}</p>}
              {readable(f) && <button type="button" className="text-button capability-detail-toggle" aria-expanded={expanded === f.id} onClick={() => setExpanded(expanded === f.id ? null : f.id)}>查看说明与依赖<ChevronDown size={13}/></button>}
              {expanded === f.id && <CapabilityDescription item={f} functions={functions.map(display)}/>}
            </article>)}{!available.length && <p className="capability-empty">没有匹配的已发布功能，试试其他关键词或类别。</p>}</div>
          </section>
          <section className="capability-pending" aria-label="待确认的功能"><div className="capability-list-heading"><h3>已选 <span>{pending.length}</span></h3><button type="button" className="text-button" disabled={disabled || !pending.length} onClick={() => setPending([])}>清空</button></div>
            <div className="capability-pending-scroll">{pending.map(id => { const f = show(id); return <div className="capability-pending-row" key={id}><div><strong>{f?.name ?? "不可用功能"}</strong><small>{!f || !executable(f) ? "不可用，请移除后发布" : f.definition.execution_kind === "composite" ? "组合功能" : "直接调用"}</small></div><button type="button" className="icon-button" disabled={disabled} aria-label={`从待选中移除 ${f?.name ?? "不可用功能"}`} onClick={() => setPending(ids => ids.filter(v => v !== id))}><X size={14}/></button></div>; })}{!pending.length && <p className="capability-empty">从左侧选择需要的能力。</p>}</div>
          </section>
        </div>
      </div>
      <footer className="modal-footer capability-selection-footer"><span>确认后更新当前草稿</span><button type="button" className="button" disabled={disabled} onClick={close}>取消</button><button type="button" className="button primary" disabled={disabled || loading || newlyUnavailable} onClick={apply}>确认添加{pending.length > 0 ? `（${pending.length}）` : ""}</button></footer>
    </Modal>}
  </section>;
}

function CapabilityDescription({ item, functions }: { item: Capability; functions: Capability[] }) {
  const schema = item.definition.input_schema.properties as Record<string, { title?: string }> | undefined;
  return <div className="capability-description"><dl><dt>业务对象</dt><dd>{item.definition.object_type}</dd><dt>输入参数</dt><dd>{Object.entries(schema ?? {}).map(([key, value]) => value.title || key).join("、") || "无需参数"}</dd><dt>办理方式</dt><dd>{item.definition.effect === "WRITE" ? "会产生业务变更" : "只读查询"}{item.definition.requires_confirmation ? "，执行前确认" : ""}</dd></dl>
    {item.definition.composite && <><h4>固定依赖</h4><ul>{item.definition.composite.dependencies.map((dependency, i) => <li key={i}>{functions.find(f => f.id === dependency.function_id)?.name ?? "依赖功能资料不可用"} · v{dependency.version}</li>)}</ul></>}
  </div>;
}
