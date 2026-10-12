import { useRef, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { Notice } from "../../shared/ui";

type Change = { id: string; user_id: string; action: string; resource_id: string; created_at: string };
const verbs: Record<string, string> = { create: "创建", import: "接入", edit: "修改草稿", validate: "验证", publish: "发布", disable: "停用", rollback: "切换发布版本", channels: "更新渠道", access: "更新访问权限", revoke_origin: "撤销宿主", revoke_integration: "撤销身份集成" };
export function ObjectChangeHistory({ api, workspace, resourceId }: { api: ApiClient; workspace: string; resourceId: string }) {
  const [rows, setRows] = useState<Change[]>([]), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const requestId = useRef(0);
  async function load() {
    const current = ++requestId.current; setBusy(true); setError("");
    try { const value = await api.get<Change[]>(`${management(workspace)}/audit?resource_id=${encodeURIComponent(resourceId)}`); if (current === requestId.current) setRows(value.filter(row => row.resource_id === resourceId)); }
    catch (e) { if (current === requestId.current) setError(errorText(e)); }
    finally { if (current === requestId.current) setBusy(false); }
  }
  return <details className="application-settings" onToggle={e => { if (e.currentTarget.open) void load(); else ++requestId.current; }}><summary>变更记录</summary>{error && <Notice kind="error">{error}</Notice>}{busy ? <p>正在读取…</p> : <><p className="muted small">此功能或智能体最近的配置与发布操作。</p><table><thead><tr><th>时间</th><th>操作人</th><th>操作</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td>{new Date(row.created_at).toLocaleString()}</td><td>{row.user_id}</td><td>{verbs[row.action.split(".").at(-1)!] ?? row.action}</td></tr>)}</tbody></table>{!rows.length && <p>暂无变更记录。</p>}</>}</details>;
}
