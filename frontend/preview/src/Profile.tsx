import { useState } from "react";
import { Check, Copy, KeyRound, Plus, ShieldCheck, SlidersHorizontal, UserRound } from "lucide-react";
import { Modal, Status } from "./components";

export function Profile({ onClose, notice, density, setDensity }: { onClose: () => void; notice: (text: string) => void; density: string; setDensity: (s: string) => void }) {
  const [tab, setTab] = useState("资料");
  const [displayName, setDisplayName] = useState("陈宁");
  const [keyCreated, setKeyCreated] = useState(false);
  const [keyActive, setKeyActive] = useState(true);
  const [passwords, setPasswords] = useState(["", "", ""]);
  const [error, setError] = useState("");
  const [changed, setChanged] = useState(false);
  return <Modal title="个人中心" onClose={onClose} wide>
    <div className="profile-layout"><nav aria-label="个人中心分类">{[["资料", UserRound], ["角色权限", ShieldCheck], ["账户安全", KeyRound], ["API Key", KeyRound], ["偏好设置", SlidersHorizontal]].map(([label, Icon]) => { const I = Icon as typeof UserRound; return <button key={String(label)} className={tab === label ? "active" : ""} onClick={() => setTab(String(label))}><I size={16} />{String(label)}</button>; })}</nav>
      <div className="profile-content">
        {tab === "资料" && <><div className="profile-identity"><span className="avatar large">陈</span><div><h3>{displayName || "陈宁"}</h3><p>chen.ning</p></div></div><label className="field">显示名称<input value={displayName} onChange={e => setDisplayName(e.target.value)} /></label><dl className="info-list"><div><dt>登录账号</dt><dd>chen.ning</dd></div><div><dt>账号 ID</dt><dd>U10001</dd></div><div><dt>所属空间</dt><dd>业务协作空间</dd></div></dl><button className="button primary" onClick={() => { if (!displayName.trim()) { notice("显示名称不能为空"); return; } notice("预览中的显示名称已保存"); }}>保存资料</button></>}
        {tab === "角色权限" && <><h3>当前权限</h3><div className="permission-row"><ShieldCheck size={19} /><div><strong>平台管理员</strong><p>创建空间、管理账号、重置密码</p></div></div><div className="permission-row"><UserRound size={19} /><div><strong>业务协作空间 · 空间管理员</strong><p>维护功能与智能体、发布应用、查看日志、管理本空间成员</p></div></div></>}
        {tab === "账户安全" && <><h3>修改密码</h3><p className="muted">修改成功后，需要重新登录。</p><form onSubmit={e => { e.preventDefault(); if (!passwords[0]) { setError("请填写当前密码"); return; } if (passwords[1].length < 12) { setError("新密码至少需要 12 个字符"); return; } if (passwords[1] !== passwords[2]) { setError("两次输入的新密码不一致"); return; } setError(""); setChanged(true); setPasswords(["", "", ""]); }}>{["当前密码", "新密码", "确认新密码"].map((label, i) => <label className="field" key={label}>{label}<input type="password" autoComplete={i === 0 ? "current-password" : "new-password"} value={passwords[i]} onChange={e => setPasswords(passwords.map((p, j) => j === i ? e.target.value : p))} /></label>)}{error && <p className="field-error" role="alert">{error}</p>}{changed && <div className="inline-note" role="status">改密交互预览完成，实际账号未发生变化。</div>}<button className="button primary" type="submit">确认修改</button></form></>}
        {tab === "API Key" && <><div className="section-caption"><h3>我的 API Key</h3><button className="button" onClick={() => { setKeyCreated(true); setKeyActive(true); }}><Plus size={14} />创建 Key</button></div><p className="muted">继承你对智能体的访问权限，用于宿主后端接入。</p><div className="key-row"><KeyRound size={18} /><div><strong>业务门户</strong><small>•••• •••• demo · 到期于 2026-12-31</small></div><Status value={keyActive ? "有效" : "已撤销"} /><button className="text-button danger-text" disabled={!keyActive} onClick={() => { setKeyActive(false); notice("示例 Key 已撤销"); }}>撤销</button></div>{keyCreated && <div className="inline-note"><strong>Key 创建预览</strong><p>正式功能会在此仅展示一次完整密钥。此预览不生成可用凭据。</p><code>preview-only-not-a-real-key</code></div>}</>}
        {tab === "偏好设置" && <><h3>列表密度</h3><p className="muted">设置当前预览的列表与卡片间距。</p><div className="density-options">{["舒适", "紧凑"].map(d => <button key={d} aria-pressed={density === d} className={density === d ? "selected" : ""} onClick={() => setDensity(d)}><div>{[0, 1, 2].map(i => <i key={i} />)}</div><span>{d}{density === d && <Check size={14} />}</span></button>)}</div></>}
      </div>
    </div>
  </Modal>;
}
