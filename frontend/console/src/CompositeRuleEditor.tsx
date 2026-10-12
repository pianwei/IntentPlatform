import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import { SafeMarkdown } from "../../shared/SafeMarkdown";
import type { Capability, FunctionDefinition, PublishedFunction } from "../../shared/types";
import { makeSkillDocumentTemplate, skillDocumentTemplates, type SkillTemplateId } from "./skillDocumentTemplates";
import "./FunctionExperience.css";

type SkillDefinition = { key: string; name: string; description: string; mode?: "template" | "document"; document?: string; triggers?: string[]; input_fields?: string[]; applicable_entities?: string[]; applicable_actions?: string[]; steps?: Record<string, unknown>[] };
type Skill = { id: string; revision: number; latest_version: number; disabled: boolean; definition: SkillDefinition };
type SkillVersion = { id: string; skill_id: string; version: number; definition: SkillDefinition };
export type CompositeCommit = () => Promise<Partial<FunctionDefinition>>;
function sameDocument(left: SkillDefinition | undefined, right: SkillDefinition) {
  return left?.mode === "document" && left.key === right.key && left.name === right.name && left.description === right.description &&
    left.document === right.document && ["input_fields", "applicable_entities", "applicable_actions", "triggers", "steps"].every(key =>
      JSON.stringify(left[key as keyof SkillDefinition] ?? []) === JSON.stringify(right[key as keyof SkillDefinition] ?? []));
}
const blankDocument = "## 适用目标\n说明这个组合解决什么问题，哪些问题不适用。\n\n## 办理规则\n说明需要哪些子功能、执行先后关系和参数来源。\n\n## 回答要求\n说明最终应返回哪些结果，以及缺少资料、失败或需要确认时如何处理。";
function referenceDocument(rule: SkillDefinition, definition: FunctionDefinition, catalog: PublishedFunction[]) {
  const steps = rule.steps ?? [];
  const names = new Map(steps.map((step, index) => [String(step.step_id), `${index + 1}. ${catalog.find(item => item.function_id === step.capability_id && item.version === definition.composite?.dependencies.find(dep => dep.function_id === item.function_id)?.version)?.definition.name ?? "已配置子功能"}`]));
  const source = (value: unknown): string => {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const row = value as Record<string, unknown>;
      if ("$input" in row) return `用户输入「${String(row.$input)}」`;
      if ("$step" in row) return `步骤「${names.get(String(row.$step)) ?? "前置步骤"}」结果${row.path ? `的「${String(row.path)}」字段` : ""}`;
      if ("$item" in row) return `当前批量对象${row.path ? `的「${String(row.path)}」字段` : ""}`;
    }
    return JSON.stringify(value) ?? "未设置";
  };
  const lines = steps.map(step => {
    const when = step.when as Record<string, unknown> | undefined, batch = step.foreach as Record<string, unknown> | undefined;
    return [`### ${names.get(String(step.step_id))}`, Array.isArray(step.depends_on) && step.depends_on.length ? `在${step.depends_on.map(id => `「${names.get(String(id)) ?? "前置步骤"}」`).join("、")}完成后执行。` : "无前置步骤依赖。", ...Object.entries((step.arguments ?? {}) as object).map(([key, value]) => `- 参数「${key}」：${source(value)}`), ...(when ? [`条件：步骤「${names.get(String(when.step)) ?? "前置步骤"}」结果${when.path?`的「${String(when.path)}」字段`:""}满足「${({not_empty:"非空",empty:"为空",exists:"存在",eq:"等于",ne:"不等于",equals:"等于",not_equals:"不等于"} as Record<string,string>)[String(when.operator)] ?? String(when.operator)}」${when.value !== undefined ? ` ${source(when.value)}` : ""}才执行。`] : []), ...(batch ? [`批量：逐项处理步骤「${names.get(String(batch.step)) ?? "前置步骤"}」的结果${batch.path ? `「${String(batch.path)}」` : ""}。`] : [])].join("\n");
  });
  return `## 适用目标\n${rule.description}\n\n## 办理规则\n${lines.join("\n\n")}\n\n## 回答要求\n${Object.entries(definition.composite?.final_output_mapping ?? {}).map(([key,value]) => `- 最终「${key}」：${source(value)}`).join("\n")}\n仅根据已完成子功能的获准结果回答；缺少参数先询问，写操作先确认。`;
}
export function CompositeRuleEditor({ api, workspace, definition, functions, writable, update, onPending, onCommit }: {
  api: ApiClient; workspace: string; definition: FunctionDefinition; functions: Capability[]; writable: boolean; publishable: boolean;
  update: (patch: Partial<FunctionDefinition>) => void; onPending: (pending: boolean) => void; onCommit?: (commit: CompositeCommit | null) => void;
}) {
  const [versions, setVersions] = useState<PublishedFunction[]>([]), [selected, setSelected] = useState<string[]>(definition.composite?.dependencies.map(item => item.function_id) ?? []);
  const [document, setDocument] = useState(""), [query, setQuery] = useState(""), [skill, setSkill] = useState<Skill | null>(null), [savedVersion, setSavedVersion] = useState<SkillVersion | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [dirty, setDirty] = useState(false), [error, setError] = useState(""), [preview, setPreview] = useState(false), [legacyReference,setLegacyReference] = useState(false);
  const [pins,setPins] = useState<Record<string,number>>(()=>Object.fromEntries((definition.composite?.dependencies ?? []).map(dep=>[dep.function_id,dep.version])));
  const [pickerOpen, setPickerOpen] = useState(false), [pendingSelection, setPendingSelection] = useState<string[]>([]), [pickerPage, setPickerPage] = useState(0);
  const [templatesOpen, setTemplatesOpen] = useState(false), [templateId, setTemplateId] = useState<SkillTemplateId>("overview");
  const pickerSearch = useRef<HTMLInputElement>(null), addButton = useRef<HTMLButtonElement>(null), templateButton = useRef<HTMLButtonElement>(null);
  const firstTemplateButton = useRef<HTMLButtonElement>(null), editTab = useRef<HTMLButtonElement>(null), previewTab = useRef<HTMLButtonElement>(null);
  const mounted = useRef(true), generation = useRef(0), state = useRef({ definition, selected, document, skill, savedVersion, versions, loading, dirty, pins });
  state.current = { definition, selected, document, skill, savedVersion, versions, loading, dirty, pins };
  const base = `${management(workspace)}/business-skills`, key = functions.map(item => `${item.id}:${item.latest_version}:${item.status}`).join("|");
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; onPending(false); onCommit?.(null); }; }, []);
  useEffect(() => {
    const epoch = ++generation.current; setLoading(true); setError("");
    const load = async () => {
      const groups = await Promise.all(functions.filter(item => item.latest_version && item.status !== "disabled").map(item => api.get<PublishedFunction[]>(`${management(workspace)}/functions/${item.id}/versions`)));
      const catalog = groups.flat(); if (!mounted.current || epoch !== generation.current) return; setVersions(catalog);
      const id = state.current.definition.composite?.skill_version_id;
      if (id) {
        const skills = await api.get<Skill[]>(base);
        const all = (await Promise.all(skills.filter(item => item.latest_version).map(item => api.get<SkillVersion[]>(`${base}/${item.id}/versions`)))).flat();
        const current = all.find(item => item.id === id);
        if (!current) throw new Error("已有组合说明读取失败，请核对后再保存。");
        if (!mounted.current || epoch !== generation.current || state.current.dirty) return;
        const draft = skills.find(item => item.id === current.skill_id) ?? null; setSkill(current.definition.mode === "document" ? draft : null); setSavedVersion(current);
        if (current.definition.mode === "document") setDocument(current.definition.document ?? "");
        else {
          setDocument(referenceDocument(current.definition,state.current.definition,catalog)); setLegacyReference(true);
        }
      }
    };
    load().catch(cause => { if (mounted.current && epoch === generation.current) setError(errorText(cause)); }).finally(() => { if (mounted.current && epoch === generation.current) setLoading(false); });
    return () => { generation.current++; };
  }, [api, workspace, key]);
  useEffect(() => onPending(busy || dirty), [busy, dirty, onPending]);
  const available = functions.flatMap(item => {
    if (item.status === "disabled" || !item.latest_version) return [];
    const current = versions.find(version => version.function_id === item.id && version.version === (pins[item.id] ?? item.latest_version));
    return current && current.definition.execution_kind !== "composite" ? [{ ...item, name: current.definition.name, description: current.definition.description, definition: current.definition }] : [];
  });
  function applySelection(next: string[], nextPins: Record<string, number>) {
    if (JSON.stringify(next) === JSON.stringify(selected) && JSON.stringify(nextPins) === JSON.stringify(pins)) return;
    setSelected(next);setPins(nextPins);setDirty(true);
    const chosen=next.map(id=>versions.find(version=>version.function_id===id&&version.version===nextPins[id]));if(chosen.every(Boolean)){const writes=chosen.some(item=>item!.definition.effect==="WRITE");update({effect:writes?"WRITE":"READ_ONLY",requires_confirmation:writes});}
  }
  function closePicker() { setPickerOpen(false); addButton.current?.focus(); }
  function confirmSelection() {
    const nextPins = Object.fromEntries(pendingSelection.map(id => [id, pins[id] ?? functions.find(item => item.id === id)?.latest_version ?? 0]));
    applySelection(pendingSelection, nextPins); closePicker();
  }
  function removeChild(id: string) { const nextPins = { ...pins }; delete nextPins[id]; applySelection(selected.filter(item => item !== id), nextPins); }
  function updateChild(id: string) { applySelection(selected, { ...pins, [id]: functions.find(item => item.id === id)!.latest_version! }); }
  function openPicker() { setPendingSelection([...selected]); setQuery(""); setPickerPage(0); setTemplatesOpen(false); setPickerOpen(true); }
  function closeTemplates() { setTemplatesOpen(false); templateButton.current?.focus(); }
  useEffect(() => { if (pickerOpen) pickerSearch.current?.focus(); }, [pickerOpen]);
  useEffect(() => { if (templatesOpen) firstTemplateButton.current?.focus(); }, [templatesOpen]);
  function switchDocumentTab(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? false : event.key === "End" ? true : !preview;
    setPreview(next); (next ? previewTab : editTab).current?.focus();
  }
  const chosen = selected.map(id => available.find(item => item.id === id)).filter((item): item is typeof available[number] => Boolean(item));
  const matched = available.filter(item => !query.trim() || `${item.name} ${item.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const pageSize = 6, pageCount = Math.max(1, Math.ceil(matched.length / pageSize));
  const activePage = Math.min(pickerPage, pageCount - 1);
  const templateDocument = makeSkillDocumentTemplate(templateId, definition, chosen.map(item => item.name));
  function applyTemplate(append = false) {
    const next = append && document.trim() ? `${document.trimEnd()}\n\n${templateDocument}` : templateDocument;
    if (next !== document) { setDocument(next); setDirty(true); setLegacyReference(false); }
    setPreview(false); closeTemplates();
  }
  async function commit(): Promise<Partial<FunctionDefinition>> {
    const current = state.current;
    if (current.loading) throw new Error("子功能正在读取，请稍后保存。");
    if (!current.document.trim() || !current.selected.length) throw new Error("请选择至少一个已发布子功能，并填写 Skill 说明。");
    const dependencies = current.selected.map(id => {
      const item = functions.find(item => item.id === id && item.status !== "disabled" && item.latest_version);
      const version = current.versions.find(version => version.function_id === id && version.version === (current.pins[id] ?? item?.latest_version));
      if (!item || !version || version.definition.execution_kind === "composite") throw new Error("所选子功能不可用；请移除后重新选择。");
      return { function_id: id, version: version.version };
    });
    if (!current.definition.name.trim() || !current.definition.description.trim()) throw new Error("请先填写功能名称和组合介绍。");
    setBusy(true); setError("");
    try {
      const body: SkillDefinition = { key: current.skill?.definition.key ?? `function.${crypto.randomUUID()}`, name: current.definition.name, description: current.definition.description, mode: "document", document: current.document.trim(), steps: [],
        input_fields: Object.keys((current.definition.input_schema.properties ?? {}) as object), applicable_entities: current.definition.object_type ? [current.definition.object_type] : [], applicable_actions: current.definition.action ? [current.definition.action] : [], triggers: [] };
      let saved = current.skill;
      let version = saved && current.savedVersion?.skill_id === saved.id && sameDocument(current.savedVersion.definition, body) ? current.savedVersion : null;
      if (!version) {
        if (!saved) saved = await api.post<Skill>(base, body);
        else if (!sameDocument(saved.definition, body)) saved = await api.patch<Skill>(`${base}/${saved.id}/draft`, { revision: saved.revision, definition: body });
        if (mounted.current) { setSkill(saved); state.current.skill = saved; }
        // A previous attempt may have published successfully before its response
        // or subsequent version read failed. Re-read the server's revision and
        // exact latest version; a matching draft is never publication evidence.
        if (current.skill) {
          const fresh = (await api.get<Skill[]>(base)).find(item => item.id === saved!.id);
          if (!fresh || fresh.disabled || fresh.revision !== saved.revision || !sameDocument(fresh.definition, body)) throw new Error("组合说明已被修改或不可用，请保留输入并重新读取后再保存。");
          saved = fresh;
          if (mounted.current) { setSkill(saved); state.current.skill = saved; }
        }
        if (saved.latest_version) {
          const published = await api.get<SkillVersion[]>(`${base}/${saved.id}/versions`);
          const latest = published.find(item => item.skill_id === saved!.id && item.version === saved!.latest_version);
          if (!latest) throw new Error("组合说明的固定发布记录不可用，请保留输入并重新读取后再保存。");
          version = sameDocument(latest.definition, body) ? latest : null;
        }
      }
      if (!version) {
        if (!saved) throw new Error("组合说明尚未保存，请重试保存。");
        const publishedSkill = await api.post<Skill>(`${base}/${saved.id}/publish`, { revision: saved.revision });
        if (publishedSkill?.id !== saved.id || !Number.isSafeInteger(publishedSkill.latest_version) || publishedSkill.latest_version < 1) throw new Error("组合说明已保存，发布响应尚未确认，请重试保存。");
        saved = publishedSkill;
        if (mounted.current) { setSkill(saved); state.current.skill = saved; }
        const published = await api.get<SkillVersion[]>(`${base}/${saved.id}/versions`);
        version = published.find(item => item.skill_id === saved!.id && item.version === saved!.latest_version) ?? null;
        if (!version || !sameDocument(version.definition, body)) throw new Error("组合说明已保存，本次发布的文档尚未核对，请保留输入并重试保存。");
      }
      if (mounted.current) { setSavedVersion(version); state.current.savedVersion=version; setDirty(false);setLegacyReference(false); }
      const writes=dependencies.some(dep=>current.versions.find(v=>v.function_id===dep.function_id&&v.version===dep.version)?.definition.effect==="WRITE");
      return { authoring_version: "function-v2", effect:writes?"WRITE":"READ_ONLY",requires_confirmation:writes, composite: { mode: "document", skill_version_id: version!.id, dependencies, final_output_mapping: {} } };
    } catch (cause) { if (mounted.current) setError(errorText(cause)); throw cause; } finally { if (mounted.current) setBusy(false); }
  }
  const commitRef = useRef(commit); commitRef.current = commit;
  useEffect(() => { onCommit?.(() => commitRef.current()); return () => onCommit?.(null); }, [onCommit]);
  return <section className="form-section composite-editor"><div className="section-caption"><h2>组合设置</h2><span className="muted small">{selected.length} 项子功能</span></div>
    {error && <Notice kind="error">{error}</Notice>}
    {loading && <p className="muted small">正在读取已发布子功能…</p>}
    <fieldset disabled={!writable || loading || busy}>
      <div className="composite-block">
        <div className="composite-block-heading"><div><h3>子功能</h3><p>只添加完成这个组合所需的功能。</p></div><button ref={addButton} type="button" className="button secondary" onClick={openPicker} aria-expanded={pickerOpen} aria-controls="composite-child-picker">添加子功能</button></div>
        {pickerOpen ? <div id="composite-child-picker" className="composite-picker" onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closePicker(); } }}>
          <div className="composite-picker-heading"><strong>选择已发布功能</strong><span className="muted small">已选 {pendingSelection.length} 项</span></div>
          <input ref={pickerSearch} aria-label="搜索组合子功能" value={query} placeholder="搜索功能名称或用途" onChange={event => { setQuery(event.target.value); setPickerPage(0); }} />
          <div className="function-child-list composite-picker-results">{matched.slice(activePage * pageSize, (activePage + 1) * pageSize).map(item => <label key={item.id}><input type="checkbox" aria-label={`组合子功能 ${item.name}`} checked={pendingSelection.includes(item.id)} onChange={event => setPendingSelection(previous => event.target.checked ? [...previous, item.id] : previous.filter(id => id !== item.id))} /><span><strong>{item.name}</strong><small>{item.description}</small></span></label>)}
            {!matched.length && <p className="composite-empty">没有匹配的已发布功能。</p>}
          </div>
          <div className="composite-picker-footer"><div className="composite-pagination"><span className="muted small">{matched.length} 项 · {activePage + 1}/{pageCount}</span><button type="button" className="text-button" disabled={activePage === 0} onClick={() => setPickerPage(activePage - 1)}>上一页</button><button type="button" className="text-button" disabled={activePage + 1 >= pageCount} onClick={() => setPickerPage(activePage + 1)}>下一页</button></div><div className="button-row"><button type="button" className="button secondary" onClick={closePicker}>取消选择</button><button type="button" className="button" onClick={confirmSelection}>确认选择</button></div></div>
        </div> : <div className="composite-selected-list" aria-label="已选子功能">
          {chosen.map(item => <div className="composite-selected-row" key={item.id}><div className="composite-selected-name"><strong>{item.name}</strong><span className="composite-version">v{pins[item.id] ?? item.latest_version}</span></div><div className="composite-child-actions">
            <details className="composite-child-description"><summary>说明</summary><p>{item.description || "暂无说明。"}</p></details>
            {pins[item.id] && pins[item.id] !== item.latest_version && versions.some(version => version.function_id === item.id && version.version === item.latest_version && version.definition.execution_kind !== "composite") && <button type="button" className="text-button" onClick={() => updateChild(item.id)}>更新子功能</button>}
            <button type="button" className="text-button" aria-label={`移除子功能 ${item.name}`} onClick={() => removeChild(item.id)}>移除</button>
          </div></div>)}
          {!loading && selected.filter(id => !available.some(item => item.id === id)).map(id => <div className="composite-unavailable" key={id}><span><strong>{functions.find(item => item.id === id)?.name ?? "已选子功能"}</strong><small>暂不可用，请移除或恢复此依赖后再保存。</small></span><button type="button" className="text-button" aria-label={`移除不可用子功能 ${functions.find(item => item.id === id)?.name ?? "已选子功能"}`} onClick={() => removeChild(id)}>移除</button></div>)}
          {!loading && !selected.length && <p className="composite-empty">还没有子功能，先添加，再编写办理规则。</p>}
        </div>}
      </div>
      <div className="composite-block composite-skill-block">
        <div className="composite-block-heading"><div><h3>Skill 说明 <em>*</em></h3><p>说明何时适用、如何协作，以及最终返回什么。</p></div><button ref={templateButton} type="button" className="button secondary" onClick={() => { setTemplatesOpen(true); setPickerOpen(false); }} aria-expanded={templatesOpen}>使用模板</button></div>
        {templatesOpen ? <div className="composite-template-browser" onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeTemplates(); } }}>
          <div className="composite-template-options" aria-label="Skill 模板">{skillDocumentTemplates.map((item, index) => <button ref={index === 0 ? firstTemplateButton : undefined} key={item.id} type="button" className={`composite-template-option ${templateId === item.id ? "is-active" : ""}`} aria-pressed={templateId === item.id} onClick={() => setTemplateId(item.id)}><strong>{item.name}</strong><span>{item.description}</span></button>)}</div>
          <div className="composite-template-preview" aria-label="模板预览"><SafeMarkdown text={templateDocument} /></div>
          {document.trim() && <p className="composite-template-warning">当前已有说明。选择替换或追加后才会修改。</p>}
          <div className="composite-template-footer"><button type="button" className="button secondary" onClick={closeTemplates}>取消模板</button><div className="button-row">{document.trim() && <button type="button" className="button secondary" onClick={() => applyTemplate(true)}>追加到当前说明</button>}<button type="button" className="button" onClick={() => applyTemplate()}>{document.trim() ? "替换当前说明" : "使用此模板"}</button></div></div>
        </div> : <>
          <div className="composite-document-tabs" role="tablist" aria-label="Skill 说明视图"><button ref={editTab} type="button" role="tab" id="skill-edit-tab" tabIndex={!preview ? 0 : -1} aria-selected={!preview} aria-controls="skill-document-panel" onKeyDown={switchDocumentTab} onClick={() => setPreview(false)}>编辑</button><button ref={previewTab} type="button" role="tab" id="skill-preview-tab" tabIndex={preview ? 0 : -1} aria-selected={preview} aria-controls="skill-document-panel" onKeyDown={switchDocumentTab} onClick={() => setPreview(true)}>预览</button></div>
          <div id="skill-document-panel" role="tabpanel" aria-labelledby={preview ? "skill-preview-tab" : "skill-edit-tab"}>{preview ? <div className="composite-document-preview"><SafeMarkdown text={document || "尚未填写说明。"} /></div> : <textarea className="document-skill-editor" aria-label="Skill 说明" rows={10} value={document} placeholder={blankDocument} onChange={event => { setDocument(event.target.value); setDirty(true); }} />}</div>
        </>}
        {legacyReference && <p className="muted small">当前展示原规则的参考说明；保存转换后需重新测试，查看不改变发布版本。</p>}
      </div>
      {chosen.some(item => item.definition.effect === "WRITE") && <Notice>包含业务写入，执行和测试前需确认对象与参数。</Notice>}
      {!onCommit && <button type="button" className="button" onClick={() => void commit().then(update).catch(() => {})}>应用组合配置</button>}
    </fieldset>
  </section>;
}
