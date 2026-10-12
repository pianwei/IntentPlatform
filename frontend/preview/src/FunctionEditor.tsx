import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Check, CheckCircle2, ChevronRight, CircleHelp, FileCode2, GitBranch, Link2, Play, Plus, Save, Trash2, WandSparkles } from "lucide-react";
import { PageTitle, Status } from "./components";

type Parameter = { key: number; name: string; type: string; required: boolean; example: string };
export type PreviewDraft = { name: string; goal: string; kind: string; connector: string; endpoint: string; effect: string; object: string; action: string; params: Parameter[]; processor: string; method: string; credential: string; source: string; display: string };
export function PreviewFunctionEditor({ existing, initial, onSave, onBack, onDirty, notice }: { existing: boolean; initial?: PreviewDraft; onSave: (draft: PreviewDraft) => void; onBack: () => void; onDirty: (dirty: boolean) => void; notice: (text: string) => void }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState(initial?.name ?? (existing ? "查询账户余额" : ""));
  const [goal, setGoal] = useState(initial?.goal ?? (existing ? "根据账户编号查询余额、可用余额及币种。适用于单个账户的资金查询。" : ""));
  const [kind, setKind] = useState(initial?.kind ?? "direct");
  const [connector, setConnector] = useState(initial?.connector ?? "模拟处理器");
  const [endpoint, setEndpoint] = useState(initial?.endpoint ?? "");
  const [effect, setEffect] = useState(initial?.effect ?? "只读查询");
  const [object, setObject] = useState(initial?.object ?? "账户");
  const [action, setAction] = useState(initial?.action ?? "查询");
  const [params, setParams] = useState<Parameter[]>(initial?.params ?? [{ key: 1, name: "account_id", type: "文本", required: true, example: "ACC-2608" }]);
  const [processor, setProcessor] = useState(initial?.processor ?? "账户余额查询");
  const [method, setMethod] = useState(initial?.method ?? "GET");
  const [credential, setCredential] = useState(initial?.credential ?? "不需要凭据");
  const [source, setSource] = useState(initial?.source ?? "用户输入或前序功能结果");
  const [display, setDisplay] = useState(initial?.display ?? "详情卡片");
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [tested, setTested] = useState(false);
  const [published, setPublished] = useState(false);
  const [testInput, setTestInput] = useState("ACC-2608");
  useEffect(() => onDirty(dirty), [dirty, onDirty]);
  function touch() { setDirty(true); setTested(false); setPublished(false); }
  function fillExample() { setName("查询账户余额"); setGoal("根据账户编号查询余额、可用余额及币种。适用于单个账户的资金查询。"); setEndpoint("https://example.com/api/accounts/{account_id}/balance"); setErrors({}); touch(); }
  function validate(target: number) {
    const next: Record<string, string> = {};
    if (!name.trim()) next.name = "请填写功能名称，例如：查询账户余额";
    if (!goal.trim()) next.goal = "请说明这个功能能解决什么业务问题";
    if (kind === "direct" && connector !== "模拟处理器" && !/^https?:\/\//.test(endpoint)) next.endpoint = "请填写以 http:// 或 https:// 开头的接口地址";
    if (target >= 2 && params.some(p => !p.name.trim())) next.params = "请补全参数名称";
    if (target >= 2 && new Set(params.map(p => p.name.trim())).size !== params.length) next.params = "参数名称不能重复";
    setErrors(next);
    if (Object.keys(next).length) { setStep(next.name || next.goal || next.endpoint ? 0 : 1); return false; }
    return true;
  }
  function move(target: number) { if (target < step || validate(target)) setStep(target); }
  function persist() { onSave({ name, goal, kind, connector, endpoint, effect, object, action, params, processor, method, credential, source, display }); setDirty(false); }
  function save() { if (!name.trim()) { setErrors({ name: "保存草稿前请填写功能名称" }); setStep(0); return; } persist(); notice("草稿已保留，可在本次预览中继续编辑；刷新页面后清除"); }
  const steps = ["基本信息与执行", "输入输出与映射", "验证与发布"];
  return <section className="editor-page">
    <button className="back-link" onClick={onBack}><ArrowLeft size={15} />功能库</button>
    <PageTitle title={existing ? "编辑功能" : "新建功能"} help="先描述业务，再配置输入输出。草稿可逐步完善，发布前需要完成全部必填项和验证。">
      <button className="button" onClick={fillExample}><WandSparkles size={15} />填入示例</button>
    </PageTitle>
    <div className="editor-stepper" aria-label="配置步骤">{steps.map((label, index) => <button key={label} className={index === step ? "current" : index < step ? "complete" : ""} onClick={() => move(index)} aria-current={index === step ? "step" : undefined}><span>{index < step ? <Check size={15} /> : index + 1}</span><strong>{label}</strong>{index < 2 && <ChevronRight size={16} />}</button>)}</div>
    <div className="editor-body">
      <div className="editor-main">
        {step === 0 && <>
          <section className="form-section"><div className="section-caption"><h2>基本信息</h2><span><em>*</em> 必填</span></div>
            <label className="field">功能名称 <em>*</em><input value={name} onChange={e => { setName(e.target.value); touch(); }} placeholder="例如：查询账户余额" aria-invalid={!!errors.name} />{errors.name && <span role="alert" className="field-error">{errors.name}</span>}</label>
            <label className="field">业务目标 <em>*</em><textarea value={goal} onChange={e => { setGoal(e.target.value); touch(); }} rows={3} placeholder="说明解决什么问题、何时使用。例如：根据账户编号查询当前余额。" aria-invalid={!!errors.goal} />{errors.goal && <span role="alert" className="field-error">{errors.goal}</span>}</label>
            <div className="field-label">执行方式 <em>*</em></div><div className="type-choices" role="group" aria-label="执行方式">
              <button className={kind === "direct" ? "chosen" : ""} aria-pressed={kind === "direct"} onClick={() => { setKind("direct"); touch(); }}><Link2 size={19} /><span><strong>直接调用</strong><small>调用一个接口或已有工作流</small></span><span className="radio-dot" /></button>
              <button className={kind === "composite" ? "chosen" : ""} aria-pressed={kind === "composite"} onClick={() => { setKind("composite"); touch(); }}><GitBranch size={19} /><span><strong>组合功能</strong><small>组合已发布功能完成业务目标</small></span><span className="radio-dot" /></button>
            </div>
          </section>
          <section className="form-section"><h2>{kind === "direct" ? "连接配置" : "组合步骤"}</h2>
            {kind === "direct" ? <><div className="field-row"><label className="field">接入类型 <em>*</em><select value={connector} onChange={e => { setConnector(e.target.value); touch(); }}>{["模拟处理器", "HTTP 接口", "Dify 工作流", "HiAgent 工作流"].map(v => <option key={v}>{v}</option>)}</select></label><label className="field">操作性质 <em>*</em><select value={effect} onChange={e => { setEffect(e.target.value); touch(); }}><option>只读查询</option><option>写入操作</option></select></label></div>
              {connector === "模拟处理器" ? <label className="field">处理器 <em>*</em><select aria-label="模拟处理器" value={processor} onChange={e => { setProcessor(e.target.value); touch(); }}><option>账户余额查询</option><option>客户定位</option><option>账户状态查询</option></select><small>当前预览使用内置业务样例。</small></label> : <><label className="field">接口地址 <em>*</em><input value={endpoint} onChange={e => { setEndpoint(e.target.value); touch(); }} placeholder="https://example.com/api/accounts/{account_id}/balance" aria-invalid={!!errors.endpoint} />{errors.endpoint && <span role="alert" className="field-error">{errors.endpoint}</span>}</label><div className="field-row"><label className="field">请求方法<select value={method} onChange={e => { setMethod(e.target.value); touch(); }}><option>GET</option><option>POST</option></select></label><label className="field">服务凭据 <span className="optional">选填</span><select value={credential} onChange={e => { setCredential(e.target.value); touch(); }}><option>不需要凭据</option><option>示例服务凭据</option></select></label></div></>}
              {effect === "写入操作" && <div className="inline-note">写入前将向用户展示对象与参数，并要求确认。</div>}
            </> : <><p className="muted">选取已发布功能，定义结果如何传递到下一步。</p><div className="composition-steps">{["定位客户", "查询客户账户列表", "查询账户余额"].map((s, i) => <div key={s}><b>{i + 1}</b><span><strong>{s}</strong><small>{i === 0 ? "参数来自用户输入" : "参数来自上一步结果"}</small></span><Status value="已发布" /></div>)}</div><button className="button" onClick={() => notice("当前预览展示三步组合示例，正式编辑器将在审核后接入")}><Plus size={14} />添加步骤</button></>}
          </section>
        </>}
        {step === 1 && <>
          <section className="form-section"><div className="section-caption"><h2>输入参数</h2><button className="text-button" onClick={() => { setParams([...params, { key: Math.max(0, ...params.map(p => p.key)) + 1, name: "", type: "文本", required: false, example: "" }]); touch(); }}><Plus size={14} />添加参数</button></div>
            <div className="parameter-table"><table><thead><tr><th>参数名称 *</th><th>类型</th><th>必填</th><th>示例值</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{params.map((p, index) => <tr key={p.key}><td><input aria-label={`参数名称 ${index + 1}`} value={p.name} placeholder="account_id" onChange={e => { setParams(params.map(x => x.key === p.key ? { ...x, name: e.target.value } : x)); touch(); }} /></td><td><select aria-label={`参数类型 ${index + 1}`} value={p.type} onChange={e => { setParams(params.map(x => x.key === p.key ? { ...x, type: e.target.value } : x)); touch(); }}><option>文本</option><option>数字</option><option>布尔值</option></select></td><td><input type="checkbox" aria-label={`参数必填 ${index + 1}`} checked={p.required} onChange={e => { setParams(params.map(x => x.key === p.key ? { ...x, required: e.target.checked } : x)); touch(); }} /></td><td><input aria-label={`参数示例 ${index + 1}`} value={p.example} onChange={e => { setParams(params.map(x => x.key === p.key ? { ...x, example: e.target.value } : x)); touch(); }} /></td><td><button className="icon-button" aria-label={`删除参数 ${index + 1}`} onClick={() => { setParams(params.filter(x => x.key !== p.key)); touch(); }}><Trash2 size={15} /></button></td></tr>)}</tbody></table></div>
            {errors.params && <p className="field-error" role="alert">{errors.params}</p>}
          </section>
          <section className="form-section"><h2>业务映射</h2><div className="field-row"><label className="field">关联实体 <em>*</em><select value={object} onChange={e => { setObject(e.target.value); touch(); }}><option>账户</option><option>客户</option><option>贷款</option><option>工单</option></select></label><label className="field">业务动作 <em>*</em><select value={action} onChange={e => { setAction(e.target.value); touch(); }}><option>查询</option><option>统计</option><option>创建</option><option>冻结</option></select></label></div><label className="field">参数来源<select value={source} onChange={e => { setSource(e.target.value); touch(); }}><option>用户输入或前序功能结果</option><option>仅用户输入</option></select></label></section>
          <section className="form-section"><h2>输出与展示</h2><div className="mapping-preview"><span>balance</span><ArrowRight size={14} /><strong>账户余额</strong><span>available_balance</span><ArrowRight size={14} /><strong>可用余额</strong><span>currency</span><ArrowRight size={14} /><strong>币种</strong></div><label className="field">展示形式<select value={display} onChange={e => { setDisplay(e.target.value); touch(); }}><option>详情卡片</option><option>表格</option><option>文本</option></select></label><details className="advanced"><summary><FileCode2 size={15} />查看结构化定义</summary><pre>{JSON.stringify({ name, object_type: object, action, parameters: params.map(({ key, ...p }) => p) }, null, 2)}</pre></details></section>
        </>}
        {step === 2 && <>
          <section className="form-section"><div className="section-caption"><h2>验证配置</h2><Status value="通过" /></div><div className="validation-checks">{["业务信息已填写", "输入参数与实体映射完整", effect === "只读查询" ? "只读功能，无须写入确认" : "写入功能，必须经用户确认"].map(t => <div key={t}><CheckCircle2 size={17} />{t}</div>)}</div></section>
          <section className="form-section"><h2>试运行</h2><label className="field">账户编号 <em>*</em><input value={testInput} onChange={e => { setTestInput(e.target.value); setTested(false); }} placeholder="例如：ACC-2608" /></label><button className="button primary" onClick={() => { if (!testInput.trim()) { notice("请先填写测试用账户编号"); return; } setTested(true); }}><Play size={14} />运行示例</button>
            {tested && <div className="test-result" role="status"><div><CheckCircle2 size={17} /><strong>示例验证完成</strong><span>本地预览</span></div><dl><div><dt>账户</dt><dd>{testInput}</dd></div><div><dt>账户余额</dt><dd>28,800.00 元</dd></div><div><dt>可用余额</dt><dd>28,800.00 元</dd></div><div><dt>币种</dt><dd>人民币</dd></div></dl><small>以上为界面样例，未调用业务接口。</small></div>}
          </section>
          {published && <section className="publish-result" role="status"><CheckCircle2 size={23} /><div><strong>发布流程预览完成</strong><p>正式发布将在界面审核、后端集成与验证完成后启用。</p></div></section>}
        </>}
      </div>
      <aside className="editor-guide"><div className="guide-title"><CircleHelp size={17} /><h3>{["从业务问题开始", "把参数说清楚", "先验证，再发布"][step]}</h3></div>
        <p>{["用业务人员能理解的名称描述功能，让智能体知道什么时候使用它。", "每个必填参数都需要明确的类型与来源，智能体才能正确补参和传参。", "检查输入与展示结果，确保功能满足预期，再供智能体使用。"][step]}</p>
        <div className="example-block"><span>一个好的例子</span><strong>{["查询账户余额", "account_id · 账户编号", "输入 ACC-2608"][step]}</strong><p>{["根据账户编号，返回余额、可用余额及币种。", "文本类型，必填。可来自用户提供的编号，或账户列表查询的结果。", "应返回账户余额、可用余额和币种，不展示未声明的其他字段。"][step]}</p></div>
        <div className="guide-progress"><span>配置进度</span><strong>{step + 1} / 3</strong><div><i style={{ width: `${(step + 1) / 3 * 100}%` }} /></div></div>
        <p className="guide-foot">草稿保留在本次预览中，刷新页面后清除。</p>
      </aside>
    </div>
    <footer className="editor-footer"><span>{dirty ? "有未保存的修改" : initial ? "草稿内容已保存" : existing ? "已加载功能配置" : "尚未保存草稿"}</span><div><button className="button" onClick={save}><Save size={15} />保存草稿</button>{step > 0 && <button className="button" onClick={() => move(step - 1)}>上一步</button>}{step < 2 ? <button className="button primary" onClick={() => move(step + 1)}>下一步<ArrowRight size={15} /></button> : <button className="button primary" disabled={!tested || published} onClick={() => { setPublished(true); persist(); notice("发布交互预览完成，未产生正式版本"); }}>预览发布</button>}</div></footer>
  </section>;
}
