import { useEffect, useRef, useState } from "react";
import { ArrowRight, Boxes, Check, ChevronDown, Workflow, Zap } from "lucide-react";
import { ApiClient, errorText } from "../../shared/api";
import { Help } from "./ModernUI";
import { Notice } from "../../shared/ui";
import { SafeMarkdown } from "../../shared/SafeMarkdown";
import type { AgentCompilation, Capability, FunctionDefinition, PublishedFunction } from "../../shared/types";
import "./AgentCapabilityExperience.css";

type Projection = NonNullable<AgentCompilation["projection"]> & {
  function_semantics?: Record<string, { object_type?: string; participant_roles?: { type: string }[] }>;
};
function businessEntities(projection: AgentCompilation["projection"]) {
  const semantics = (projection as Projection | null)?.function_semantics;
  const primary = new Set(Object.values(semantics ?? {}).flatMap(value => [value.object_type, ...(value.participant_roles ?? []).map(role => role.type)]).filter(Boolean));
  return projection?.entities.filter(entity => !primary.size || primary.has(entity.id)) ?? [];
}

export function AgentCompilationPreview({ api, path, revision, dirty, functions }: {
  api: ApiClient; path: string; revision?: number; dirty: boolean; functions: Capability[];
}) {
  const [result, setResult] = useState<AgentCompilation | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [entityId, setEntityId] = useState<string | null>(null);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    ++generation.current; setResult(null); setError(""); setBusy(false); setEntityId(null); setExpandedSkill(null);
    return () => { ++generation.current; };
  }, [path, revision, dirty]);
  async function compile() {
    const request = ++generation.current;
    setBusy(true); setError(""); setResult(null); setExpandedSkill(null);
    try {
      const value = await api.post<AgentCompilation>(`${path}/compile-preview`, { revision });
      if (request === generation.current) { setResult(value); setEntityId(businessEntities(value.projection)[0]?.id ?? null); }
    } catch (e) { if (request === generation.current) setError(errorText(e)); }
    finally { if (request === generation.current) setBusy(false); }
  }
  const names = (ids: string[]) => ids.map(id => functions.find(item => item.id === id)?.name ?? "不可用功能").join("、");
  const projection = result?.valid ? result.projection : null;
  const objects = businessEntities(projection);
  const resultObjects = projection?.entities.filter(entity => !objects.some(object => object.id === entity.id)) ?? [];
  const entity = projection?.entities.find(item => item.id === entityId);
  const entityActions = projection?.actions.filter(action => action.function_ids.some(id => entity?.function_ids.includes(id)));
  const base = path.replace(/\/agents\/[^/]+$/, "");
  const readableIssue = (message: string) => {
    let value = message;
    for (const f of functions) value = value.split(f.id).join(f.name);
    return value.replace(/(?:agent|run|release|skill|function|capability|fv|sv)_[a-f0-9]{16,}/gi, "相关对象");
  };

  return <section className="agent-structure application-settings">
    <div className="table-toolbar"><div className="agent-structure-heading"><h2>三层结构</h2><Help text="实体帮助识别业务对象；动作决定可以做什么；策划优先使用组合规则，不适用时在所选能力内自主规划。检查只编译已保存草稿，不发布或调用业务。"/></div>
      <button type="button" className="secondary" disabled={busy || dirty || revision === undefined} onClick={() => void compile()}>{busy ? "正在编译…" : "检查并预览三层结构"}</button>
    </div>
    {(dirty || revision === undefined) && <p className="muted small">先保存智能体草稿，再检查当前版本。</p>}
    {error && <Notice kind="error">{error}</Notice>}
    {result && <><Notice kind={result.valid ? "success" : "error"}>{result.valid ? "三层结构校验通过，可按当前草稿发布。" : "三层结构存在问题，修正后再发布。"}</Notice>
      {!!result.issues.length && <ul className="agent-structure-issues">{result.issues.map((issue, i) => <li key={i}>{issue.function_id && <strong>{names([issue.function_id])}：</strong>}{readableIssue(issue.message)}</li>)}</ul>}
    </>}
    <div className="structure-overview" aria-label="三层职责"><div><Boxes size={19}/><span><strong>实体 · 业务对象</strong><small>{projection ? `${objects.length} 类业务对象` : "理解客户、产品等业务对象"}</small></span></div><ArrowRight size={15}/><div><Zap size={19}/><span><strong>动力 · 可用动作</strong><small>{projection ? `${projection.actions.length} 类动作` : "关联可执行的查询与办理能力"}</small></span></div><ArrowRight size={15}/><div><Workflow size={19}/><span><strong>策划 · 组合流程</strong><small>{projection ? `${projection.skills.length} 条规则` : "规则优先，必要时自主组合"}</small></span></div></div>
    {projection && <div className="structure-relations">
      <div className="structure-object-heading"><h3>从业务对象查看能力</h3><span>选择对象，查看它能做什么</span></div>
      <div className="structure-entity-tabs" aria-label="业务对象">{objects.map(item => <button type="button" className={item.id === entityId ? "active" : ""} aria-pressed={item.id === entityId} key={item.id} onClick={() => setEntityId(item.id)}>{item.label}<small>{item.function_ids.length}</small></button>)}</div>
      {!!resultObjects.length && <details className="structure-other-objects"><summary>其他结果对象（{resultObjects.length}）</summary><div className="structure-entity-tabs">{resultObjects.map(item => <button type="button" className={item.id === entityId ? "active" : ""} aria-pressed={item.id === entityId} key={item.id} onClick={() => setEntityId(item.id)}>{item.label}<small>{item.function_ids.length}</small></button>)}</div></details>}
      {entity && <section className="structure-object-detail" aria-label={`${entity.label}的能力关系`}><h4>{entity.label}<ArrowRight size={15}/>可执行动作与功能</h4>
        <div className="structure-action-list">{entityActions?.map(action => <div className="structure-action" key={action.key}><div><strong>{action.name}</strong><small>{action.effect === "WRITE" ? "业务变更 · 执行前确认" : "只读查询"}</small></div><div className="structure-function-tags">{action.function_ids.filter(id => entity.function_ids.includes(id)).map(id => <span key={id}>{names([id])}{projection.dependency_function_ids.includes(id) && <small>组合内使用</small>}</span>)}</div></div>)}</div>
      </section>}
      <div className="structure-workflows"><h3>组合流程</h3>{projection.skills.map(skill => <section className="structure-workflow" key={skill.version_id}>
        <button type="button" className="structure-workflow-trigger" aria-expanded={expandedSkill === skill.version_id} onClick={() => setExpandedSkill(expandedSkill === skill.version_id ? null : skill.version_id)}><Workflow size={16}/><span><strong>{skill.name}</strong><small>{skill.function_id ? `组合功能：${names([skill.function_id])}` : "已登记业务规则"}</small></span><ChevronDown size={15}/></button>
        {expandedSkill === skill.version_id && <WorkflowDetails api={api} base={base} skill={skill} functions={functions} functionIds={[...new Set([...projection.explicit_function_ids, ...projection.dependency_function_ids])]} />}
      </section>)}{!projection.skills.length && <p className="structure-empty">没有固定组合规则，模型将在所选能力内规划；缺少必要信息时会先询问。</p>}</div>
      <details className="structure-scope"><summary>查看自主规划与组合依赖范围</summary><p><strong>自主规划可选功能：</strong>{names(projection.explicit_function_ids) || "无"}</p><p><strong>组合内部依赖：</strong>{names(projection.dependency_function_ids) || "无"}</p><p className="muted small">内部依赖仅供组合规则使用，不能因此成为自主规划的独立候选。</p></details>
    </div>}
  </section>;
}

type Skill = NonNullable<AgentCompilation["projection"]>["skills"][number];
type SkillStep = { step_id: string; capability_id?: string; capability_key?: string; arguments?: Record<string, unknown>; depends_on?: string[]; when?: { step: string; operator: string }; foreach?: { step: string } };
type RuleVersion = { id: string; skill_id: string; version: number; definition: { name: string; description: string; mode?: "template" | "document"; document?: string; steps?: SkillStep[] } };
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);

function WorkflowDetails({ api, base, skill, functions, functionIds }: { api: ApiClient; base: string; skill: Skill; functions: Capability[]; functionIds: string[] }) {
  const [rule, setRule] = useState<RuleVersion | null>(null);
  const [versions, setVersions] = useState<PublishedFunction[]>([]);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    let active = true;
    setBusy(true); setError(""); setWarning(""); setRule(null); setVersions([]);
    void Promise.allSettled([
      api.get<RuleVersion[]>(`${base}/business-skills/${encodeURIComponent(skill.skill_id)}/versions`),
      ...functionIds.map(id => api.get<PublishedFunction[]>(`${base}/functions/${encodeURIComponent(id)}/versions`)),
    ]).then(results => {
      if (!active) return;
      const first = results[0];
      if (first.status === "rejected") setError(`无法读取固定规则版本：${errorText(first.reason)}`);
      else {
        const fixed = (first.value as RuleVersion[]).find(row => row.id === skill.version_id);
        if (!fixed) setError("固定规则版本不可用，未使用其他版本替代。");
        else setRule(fixed);
      }
      const reads = results.slice(1);
      setVersions(reads.flatMap(item => item.status === "fulfilled" ? item.value as PublishedFunction[] : []));
      if (reads.some(item => item.status === "rejected")) setWarning("部分功能说明暂不可读取，规则版本与步骤仍按固定版本展示。");
      setBusy(false);
    });
    return () => { active = false; };
  }, [api, base, skill.version_id]);

  const composite = skill.function_id ? versions.find(v => v.function_id === skill.function_id &&
    v.version === functions.find(f => f.id === skill.function_id)?.latest_version && v.definition.composite?.skill_version_id === skill.version_id) : undefined;
  const fixedFunctions = composite ? composite.definition.composite!.dependencies.flatMap(dependency => {
    const fixed = versions.find(v => v.function_id === dependency.function_id && v.version === dependency.version);
    return fixed ? [fixed] : [];
  }) : skill.function_id ? [] : versions.filter(v => v.version === functions.find(f => f.id === v.function_id)?.latest_version);

  function functionFor(step: SkillStep): { name: string; definition?: FunctionDefinition } {
    if (step.capability_id) {
      const known = functions.find(f => f.id === step.capability_id);
      const published = fixedFunctions.find(v => v.function_id === step.capability_id);
      return { name: published?.definition.name ?? known?.name ?? "功能资料不可用", definition: published?.definition };
    }
    const matches = fixedFunctions.filter(v => (v.definition.connector as unknown as Record<string, unknown>).workflow_id === step.capability_key);
    const ids = new Set(matches.map(v => v.function_id));
    return ids.size === 1 ? { name: matches[0].definition.name, definition: matches[0].definition } : { name: "已登记功能（说明暂不可用）" };
  }
  const steps = rule?.definition.steps ?? [];
  const documentMode = rule?.definition.mode === "document";
  const stepName = (id: unknown) => { const step = steps.find(item => item.step_id === id); return step ? functionFor(step).name : "前置步骤"; };
  function source(value: unknown) {
    if (record(value) && "$input" in value) return "由用户提供，执行前校验与绑定";
    if (record(value) && "$step" in value) return `使用「${stepName(value.$step)}」的结果`;
    if (record(value) && "$item" in value) return "使用本次批量处理的对象";
    if (value === null || value === undefined) return "未设置";
    return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value) : "固定配置值";
  }
  return <div className="structure-workflow-details">
    {busy && <p role="status">正在读取固定规则…</p>}{error && <Notice kind="error">{error}</Notice>}{warning && <p className="muted small">{warning}</p>}
    {rule && <>{!busy && skill.function_id && !composite && <p className="muted small">组合功能版本说明不可用或已变更，请重新检查结构；未使用其他版本替代。</p>}<p className="structure-rule-goal">{rule.definition.description}</p><p className="structure-rule-version"><Check size={13}/>固定规则 v{rule.version}{documentMode ? " · 文档规则" : ` · ${steps.length} 步`}</p>{documentMode ? <><p>每轮根据说明生成计划，实际执行步骤可在运行日志查看。</p><h4>可调用的子功能</h4><ul>{fixedFunctions.map(f => <li key={f.id}><strong>{f.definition.name}</strong>{f.definition.description && <p className="muted small">{f.definition.description}</p>}</li>)}</ul>{composite && fixedFunctions.length !== composite.definition.composite!.dependencies.length && <Notice kind="error">部分固定子功能版本不可用，不能用最新版本替代。</Notice>}<details><summary>查看 Skill 说明</summary><SafeMarkdown text={rule.definition.document || "未记录规则说明。"}/></details></> : <ol className="structure-step-list">{steps.map((step, index) => {
      const f = functionFor(step);
      const fields = f.definition?.input_schema.properties as Record<string, { title?: string }> | undefined;
      return <li key={step.step_id}><span className="structure-step-number">{index + 1}</span><div><h4>{f.name}</h4><p>{step.depends_on?.length ? `在${step.depends_on.map(id => `「${stepName(id)}」`).join("、")}完成后执行` : "可直接开始；与其他无依赖步骤独立执行"}</p>
        {step.when && <p>条件：{step.when.operator === "not_empty" ? "前置结果非空" : step.when.operator === "empty" ? "前置结果为空" : step.when.operator === "exists" ? "前置结果存在" : "满足固定条件"}时执行</p>}
        {step.foreach && <p>对「{stepName(step.foreach.step)}」的结果逐项处理</p>}
        {f.definition?.effect === "WRITE" && <span className="structure-confirmation">业务变更，执行前确认</span>}
        {!!Object.keys(step.arguments ?? {}).length && <details className="structure-step-parameters"><summary>参数来源</summary><dl>{Object.entries(step.arguments ?? {}).map(([name, value]) => <div key={name}><dt>{fields?.[name]?.title || name}</dt><dd>{source(value)}</dd></div>)}</dl></details>}
      </div></li>;
    })}</ol>}</>}
  </div>;
}
