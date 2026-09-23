import { useEffect, useState, type FormEvent } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import "./ModelSettingsPage.css";

type ModelConfig = {
  protocol: "openai-compatible";
  endpoint: string;
  model: string;
  model_version: string;
  timeout_seconds: number;
};
type ModelVersion = {
  id: string;
  profile_id: string;
  version: number;
  config: ModelConfig;
  has_api_key: boolean;
  created_at: string;
};
export type ModelProfile = {
  id: string;
  workspace_id: string;
  name: string;
  revision: number;
  latest_version: number;
  disabled: boolean;
  latest: ModelVersion | null;
};
type TestResult = { ok: boolean; profile_id: string; version: number; elapsed_ms: number };

const blank: ModelConfig = {
  protocol: "openai-compatible",
  endpoint: "",
  model: "",
  model_version: "",
  timeout_seconds: 30,
};

export function ModelSettingsPage({ api, workspace, admin }: { api: ApiClient; workspace: string; admin: boolean }) {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [name, setName] = useState("");
  const [config, setConfig] = useState<ModelConfig>(blank);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const active = profiles.find(item => item.id === selected);
  const base = `${management(workspace)}/model-profiles`;

  async function reload() {
    const items = await api.get<ModelProfile[]>(base);
    setProfiles(items);
    return items;
  }
  useEffect(() => {
    let live = true;
    setProfiles([]); setSelected(""); setName(""); setConfig(blank); setApiKey(""); setVersions([]); setError(""); setNotice("");
    api.get<ModelProfile[]>(base).then(items => { if (live) setProfiles(items); }).catch(e => { if (live) setError(errorText(e)); });
    return () => { live = false; };
  }, [api, base]);
  useEffect(() => {
    let live = true;
    if (!selected) { setVersions([]); return; }
    api.get<ModelVersion[]>(`${base}/${encodeURIComponent(selected)}/versions`)
      .then(items => { if (live) setVersions(items); }).catch(e => { if (live) setError(errorText(e)); });
    return () => { live = false; };
  }, [api, base, selected]);

  function choose(profile: ModelProfile | null) {
    setSelected(profile?.id ?? "");
    setName(profile?.name ?? "");
    setConfig(profile?.latest?.config ?? blank);
    setApiKey(""); setError(""); setNotice("");
  }
  function update(patch: Partial<ModelConfig>) { setConfig(current => ({ ...current, ...patch })); setNotice(""); }
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const result = active
        ? await api.post<ModelProfile>(`${base}/${encodeURIComponent(active.id)}/versions`,
            { revision: active.revision, config, ...(apiKey ? { api_key: apiKey } : {}) })
        : await api.post<ModelProfile>(base, { name: name.trim(), config, api_key: apiKey });
      setApiKey("");
      const items = await reload();
      choose(items.find(item => item.id === result.id) ?? result);
      setNotice(`模型配置 v${result.latest_version} 已保存。智能体发布后，新会话才会使用所选版本。`);
      setVersions(await api.get<ModelVersion[]>(`${base}/${encodeURIComponent(result.id)}/versions`));
    } catch (cause) {
      setError(errorText(cause) + "。当前输入已保留；如出现修订冲突，请刷新并核对后再保存。");
    } finally { setBusy(false); }
  }
  async function test(version: number) {
    if (!active) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await api.post<TestResult>(`${base}/${encodeURIComponent(active.id)}/versions/${version}/test`);
      setNotice(result.ok ? `v${result.version} 连接与结构化输出测试通过，耗时 ${result.elapsed_ms} ms。` : "模型测试未通过。");
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }
  async function disable() {
    if (!active) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api.post(`${base}/${encodeURIComponent(active.id)}/disable`, { revision: active.revision });
      const items = await reload();
      choose(items.find(item => item.id === active.id) ?? null);
      setNotice("模型配置已停用，引用它的会话将明确提示依赖不可用。");
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  if (!admin) return <Notice>模型配置和连接测试仅供当前空间管理员使用。</Notice>;
  return <div className="model-settings">
    <div className="table-toolbar"><p className="muted">模型配置由服务端保管。请填写获准的内网 OpenAI 兼容服务地址与明确版本。</p><button className="primary" onClick={() => choose(null)}>新建模型配置</button><button className="secondary" disabled={busy} onClick={() => void reload().catch(e => setError(errorText(e)))}>刷新</button></div>
    {error && <Notice kind="error">{error}</Notice>}{notice && <Notice kind="success">{notice}</Notice>}
    <div className="model-settings-layout"><section className="panel model-settings-list"><h2>已配置模型</h2>{!profiles.length && <p className="muted">当前空间尚无模型配置。</p>}{profiles.map(profile => <button key={profile.id} className={selected === profile.id ? "model-profile-choice selected" : "model-profile-choice"} onClick={() => choose(profile)}><strong>{profile.name}</strong><span>{profile.latest?.config.model ?? "未配置"} · {profile.latest?.config.model_version ?? "无版本"} · v{profile.latest_version}</span><small>{profile.disabled ? "已停用" : "可选"}</small></button>)}</section>
      <section className="panel model-settings-form"><h2>{active ? `更新 ${active.name}` : "新建模型配置"}</h2>{active?.disabled && <Notice kind="error">此配置已停用。请创建新的模型配置。</Notice>}
        <form onSubmit={e => void save(e)}><fieldset disabled={busy || !!active?.disabled}><div className="form-grid">
          <label>配置名称<input required maxLength={160} value={name} disabled={!!active} onChange={e => setName(e.target.value)} /></label>
          <label>协议<select value={config.protocol} onChange={e => update({ protocol: e.target.value as ModelConfig["protocol"] })}><option value="openai-compatible">OpenAI 兼容</option></select></label>
          <label className="span-2">完整请求地址<input required type="url" maxLength={2000} placeholder="https://模型内网地址/v1/chat/completions" value={config.endpoint} onChange={e => update({ endpoint: e.target.value })} /></label>
          <label>模型名<input required maxLength={160} value={config.model} onChange={e => update({ model: e.target.value })} /></label>
          <label>模型版本<input required maxLength={160} value={config.model_version} onChange={e => update({ model_version: e.target.value })} /></label>
          <label>超时（秒）<input required type="number" min="1" max="120" step="1" value={config.timeout_seconds} onChange={e => update({ timeout_seconds: Number(e.target.value) })} /></label>
          <label>访问凭据<input type="password" autoComplete="new-password" required={!active} value={apiKey} onChange={e => setApiKey(e.target.value)} /><small>{active?.latest?.has_api_key ? "已配置；留空则沿用当前版本的服务端凭据" : "只在保存时提交，页面不读取已保存的凭据"}</small></label>
        </div><button className="primary" disabled={!name.trim() || !config.endpoint || !config.model || !config.model_version || (!active && !apiKey)}>{active ? "保存为新版本" : "创建模型配置"}</button></fieldset></form>
        {active && <><div className="model-versions"><h3>不可变版本</h3>{versions.map(version => <div key={version.id} className="model-version"><span>v{version.version} · {version.config.model} / {version.config.model_version}<small>{version.config.endpoint}</small></span><button className="secondary" disabled={busy || active.disabled} onClick={() => void test(version.version)}>测试连接及结构化输出</button></div>)}</div><button className="danger-link" disabled={busy || active.disabled} onClick={() => void disable()}>停用此配置</button></>}
      </section></div>
  </div>;
}
