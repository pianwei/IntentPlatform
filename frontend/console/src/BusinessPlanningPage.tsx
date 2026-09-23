import { useEffect, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Badge, Notice } from "../../shared/ui";
import "./BusinessPlanningPage.css";

type Action = { id: string; key: string; definition: { key: string; name: string; description: string; effect: "READ_ONLY" | "WRITE" }; revision: number; latest_version: number };
type Skill = { id: string; key: string; definition: { key: string; name: string; description: string; triggers: string[]; input_fields: string[]; steps: unknown[] }; revision: number; latest_version: number; disabled: boolean };
const actionBlank: Action["definition"] = { key: "", name: "", description: "", effect: "READ_ONLY" };
const skillBlank: Skill["definition"] = { key: "", name: "", description: "", triggers: [], input_fields: [], steps: [] };
const lines = (text: string) => [...new Set(text.split("\n").map(value => value.trim()).filter(Boolean))];

export function BusinessPlanningPage({ api, workspace, writable, publishable, admin, development, onSeeded }: {
  api: ApiClient; workspace: string; writable: boolean; publishable: boolean; admin: boolean;
  development: boolean; onSeeded: () => Promise<void>;
}) {
  const base = management(workspace);
  const [actions, setActions] = useState<Action[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [actionId, setActionId] = useState("");
  const [skillId, setSkillId] = useState("");
  const [action, setAction] = useState(actionBlank);
  const [skill, setSkill] = useState(skillBlank);
  const [triggers, setTriggers] = useState("");
  const [inputs, setInputs] = useState("");
  const [steps, setSteps] = useState("[]");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const selectedAction = actions.find(row => row.id === actionId);
  const selectedSkill = skills.find(row => row.id === skillId);
  async function reload() {
    const [a, s] = await Promise.all([
      api.get<Action[]>(`${base}/actions`), api.get<Skill[]>(`${base}/business-skills`),
    ]);
    setActions(a); setSkills(s);
    return { a, s };
  }
  useEffect(() => {
    let live = true;
    setActions([]); setSkills([]); setActionId(""); setSkillId(""); setError(""); setNotice("");
    Promise.all([api.get<Action[]>(`${base}/actions`), api.get<Skill[]>(`${base}/business-skills`)]).then(([a, s]) => {
      if (live) { setActions(a); setSkills(s); }
    }).catch(cause => { if (live) setError(errorText(cause)); });
    return () => { live = false; };
  }, [api, base]);
  function chooseAction(row?: Action) { setActionId(row?.id ?? ""); setAction(row?.definition ?? actionBlank); setError(""); setNotice(""); }
  function chooseSkill(row?: Skill) {
    setSkillId(row?.id ?? "");
    setSkill(row ? { key: row.definition.key, name: row.definition.name,
      description: row.definition.description, triggers: row.definition.triggers,
      input_fields: row.definition.input_fields, steps: row.definition.steps } : skillBlank);
    setTriggers((row?.definition.triggers ?? []).join("\n"));
    setInputs((row?.definition.input_fields ?? []).join("\n"));
    setSteps(JSON.stringify(row?.definition.steps ?? [], null, 2));
    setError(""); setNotice("");
  }
  async function act(fn: () => Promise<string>) {
    setBusy(true); setError(""); setNotice("");
    try { const message = await fn(); setNotice(message); } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }
  async function saveAction() {
    await act(async () => {
      const row = selectedAction
        ? await api.patch<Action>(`${base}/actions/${selectedAction.id}/draft`, { revision: selectedAction.revision, definition: action })
        : await api.post<Action>(`${base}/actions`, action);
      const { a } = await reload(); chooseAction(a.find(item => item.id === row.id) ?? row);
      return "动作草稿已保存；发布后可固定到新智能体版本。";
    });
  }
  async function saveSkill() {
    await act(async () => {
      let parsed: unknown;
      try { parsed = JSON.parse(steps); } catch { throw new Error("步骤规则不是合法 JSON"); }
      if (!Array.isArray(parsed)) throw new Error("步骤规则必须是数组");
      const definition = { ...skill, triggers: lines(triggers), input_fields: lines(inputs), steps: parsed };
      const row = selectedSkill
        ? await api.patch<Skill>(`${base}/business-skills/${selectedSkill.id}/draft`, { revision: selectedSkill.revision, definition })
        : await api.post<Skill>(`${base}/business-skills`, definition);
      const { s } = await reload(); chooseSkill(s.find(item => item.id === row.id) ?? row);
      return "业务 Skill 草稿已保存；发布时校验步骤、依赖和功能契约。";
    });
  }
  async function publish(kind: "actions" | "business-skills", id: string, revision: number) {
    await act(async () => {
      await api.post(`${base}/${kind}/${id}/publish`, { revision });
      const { a, s } = await reload();
      if (kind === "actions") chooseAction(a.find(item => item.id === id));
      else chooseSkill(s.find(item => item.id === id));
      return "已发布不可变版本。当前运行与旧会话保持原版本。";
    });
  }
  return <div className="planning-page">
    {development && admin && <section className="panel planning-seed"><div><h2>三层意图 Demo</h2><p>安装关联一致的 20 项模拟功能、动作、5 个业务 Skill 和一个全量智能体。数据仅供工程预览，不计入真实功能接入。</p></div><button className="primary" disabled={busy} onClick={() => void act(async () => { const result = await api.post<{ chat_path: string }>("/api/v2/dev/demo/seed", {}); await reload(); await onSeeded(); return `模拟功能已就绪。聊天入口：${result.chat_path}`; })}>安装或核对演示数据</button></section>}
    {error && <Notice kind="error">{error}</Notice>}{notice && <Notice kind="success">{notice}</Notice>}
    <div className="planning-columns">
      <section className="panel planning-editor"><header><div><h2>动力层 · 动作目录</h2><p>动作提供语义模板，执行仍由已发布功能承担。</p></div><button className="secondary" disabled={!writable || busy} onClick={() => chooseAction()}>新建动作</button></header>
        <div className="planning-pills">{actions.map(row => <button key={row.id} className={actionId === row.id ? "selected" : ""} onClick={() => chooseAction(row)}>{row.definition.name} <Badge>v{row.latest_version}</Badge></button>)}</div>
        <div className="planning-fields"><label>动作代码<input value={action.key} disabled={!!selectedAction || !writable} onChange={e => setAction({ ...action, key: e.target.value })} /></label><label>名称<input value={action.name} disabled={!writable} onChange={e => setAction({ ...action, name: e.target.value })} /></label><label>操作属性<select value={action.effect} disabled={!writable} onChange={e => setAction({ ...action, effect: e.target.value as Action["definition"]["effect"] })}><option value="READ_ONLY">只读</option><option value="WRITE">写入，需确认</option></select></label><label>业务说明<textarea rows={3} value={action.description} disabled={!writable} onChange={e => setAction({ ...action, description: e.target.value })} /></label></div>
        <div className="button-row"><button className="secondary" disabled={!writable || busy || !action.key || !action.name} onClick={() => void saveAction()}>保存草稿</button><button className="primary" disabled={!publishable || busy || !selectedAction} onClick={() => selectedAction && void publish("actions", selectedAction.id, selectedAction.revision)}>发布动作版本</button></div>
      </section>
      <section className="panel planning-editor"><header><div><h2>策划层 · 业务 Skills</h2><p>先匹配适用规则；无匹配时由已配置模型提出有限计划，再由后端校验。</p></div><button className="secondary" disabled={!writable || busy} onClick={() => chooseSkill()}>新建 Skill</button></header>
        <div className="planning-pills">{skills.map(row => <button key={row.id} className={skillId === row.id ? "selected" : ""} onClick={() => chooseSkill(row)}>{row.definition.name} <Badge>v{row.latest_version}</Badge></button>)}</div>
        <div className="planning-fields"><label>Skill 代码<input value={skill.key} disabled={!!selectedSkill || !writable} onChange={e => setSkill({ ...skill, key: e.target.value })} /></label><label>名称<input value={skill.name} disabled={!writable} onChange={e => setSkill({ ...skill, name: e.target.value })} /></label><label>适用目标与规则说明<textarea rows={3} value={skill.description} disabled={!writable} onChange={e => setSkill({ ...skill, description: e.target.value })} /></label><label>匹配短语（一行一个）<textarea rows={3} value={triggers} disabled={!writable} onChange={e => setTriggers(e.target.value)} /></label><label>可提取参数（一行一个）<textarea rows={3} value={inputs} disabled={!writable} onChange={e => setInputs(e.target.value)} /></label><label>有限步骤规则 JSON<textarea className="planning-code" rows={14} value={steps} disabled={!writable} spellCheck={false} onChange={e => setSteps(e.target.value)} /></label><p className="muted small">每步声明已发布功能、依赖、参数引用、可选条件与批量对象；发布时执行服务端验证。写操作仅允许作为最后一步并由用户确认。</p></div>
        <div className="button-row"><button className="secondary" disabled={!writable || busy || !skill.key || !skill.name} onClick={() => void saveSkill()}>保存草稿</button><button className="primary" disabled={!publishable || busy || !selectedSkill || selectedSkill.disabled} onClick={() => selectedSkill && void publish("business-skills", selectedSkill.id, selectedSkill.revision)}>校验并发布</button></div>
      </section>
    </div>
  </div>;
}
