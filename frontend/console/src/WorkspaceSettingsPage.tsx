import { useEffect, useState, type FormEvent } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";
import type { PlatformContext, Workspace } from "../../shared/types";
import type { GovernanceRole, WorkspaceMember } from "../../shared/governance";
import { roleLabels } from "../../shared/governance";
import { MembersPanel } from "./GovernancePage";

export function WorkspaceSettingsPage({ api, workspace, userId, admin, onCreated }: {
  api: ApiClient; workspace: string; userId: string; admin: boolean;
  onCreated: (workspace: Workspace) => Promise<void>;
}) {
  const [context, setContext] = useState<PlatformContext | null>(null);
  const [name, setName] = useState("");
  const [administrator, setAdministrator] = useState("");
  const [memberId, setMemberId] = useState("");
  const [role, setRole] = useState<GovernanceRole>("user");
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let active = true;
    api.get<PlatformContext>("/api/v2/management/platform-context").then(value => { if (active) setContext(value); }).catch(e => { if (active) setError(errorText(e)); });
    if (workspace && admin) api.get<WorkspaceMember[]>(`${management(workspace)}/members`).then(value => { if (active) setMembers(value); }).catch(e => { if (active) setError(errorText(e)); });
    return () => { active = false; };
  }, [api, workspace, admin, generation]);
  async function create(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(""); setNotice("");
    try { const created = await api.post<Workspace>("/api/v2/management/workspaces", { name: name.trim(), administrator_id: administrator }); await onCreated(created); setNotice(created.roles.length ? "空间已创建。" : "空间已创建并分配给所选管理员；当前身份未获得该空间业务权限。"); setName(""); setAdministrator(""); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function add(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(""); setNotice("");
    const existing = members.find(item => item.user_id === memberId);
    try {
      await api.put(`${management(workspace)}/members/${encodeURIComponent(memberId)}`, { revision: existing?.revision ?? 0, roles: [role], active: true });
      setGeneration(value => value + 1); setMemberId(""); setNotice("成员已添加，权限立即生效。");
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  const choices = context?.identities ?? [];
  return <div className="governance-page">
    {error && <Notice kind="error">{error}</Notice>}{notice && <Notice kind="success">{notice}</Notice>}
    {context?.can_manage_workspaces && <section className="panel governance-panel"><h2>创建工作空间</h2><p className="muted">指定一位服务端已确认的用户担任空间管理员。</p><form onSubmit={e => void create(e)}><fieldset disabled={busy}><div className="form-grid"><label>空间名称<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label><label>空间管理员<select required value={administrator} onChange={e => setAdministrator(e.target.value)}><option value="">选择管理员</option>{choices.map(item => <option key={item.user_id} value={item.user_id}>{item.label || item.user_id}</option>)}</select></label></div><button className="primary" disabled={!name.trim() || !administrator}>创建空间并分配管理员</button></fieldset></form></section>}
    {workspace && admin ? <>
      <section className="panel governance-panel"><h2>添加空间成员</h2><form onSubmit={e => void add(e)}><fieldset disabled={busy}><div className="form-grid"><label>已确认用户<select required value={memberId} onChange={e => setMemberId(e.target.value)}><option value="">选择用户</option>{choices.filter(item => item.user_id !== userId && !members.some(member => member.user_id === item.user_id && member.active)).map(item => <option key={item.user_id} value={item.user_id}>{item.label || item.user_id}</option>)}</select></label><label>分配空间角色<select value={role} onChange={e => setRole(e.target.value as GovernanceRole)}>{(Object.keys(roleLabels) as GovernanceRole[]).map(value => <option key={value} value={value}>{roleLabels[value]}</option>)}</select></label></div><button className="primary" disabled={!memberId}>添加成员</button></fieldset></form><p className="muted small">只能添加获准身份，不能授予平台管理员。已有成员可在下方调整或撤销。</p></section>
      <MembersPanel key={generation} api={api} workspace={workspace} userId={userId} />
    </> : <Notice>{workspace ? "当前身份没有该空间的成员管理权限。" : "创建或选择工作空间后管理成员。"}</Notice>}
  </div>;
}
