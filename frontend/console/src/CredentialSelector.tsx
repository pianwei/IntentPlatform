import { ApiClient } from "../../shared/api";
import { Notice } from "../../shared/ui";
import { useGovernanceCatalog } from "./useGovernanceCatalog";

export function CredentialSelector({ api, workspace, kind, value, onChange }: { api: ApiClient; workspace: string; kind: string; value: string | null; onChange: (value: string | null) => void }) {
  const { catalog, error, loading, reload } = useGovernanceCatalog(api, workspace);
  const options = (catalog?.credentials ?? []).filter(item => item.connector_kind === kind);
  const unavailable = !!value && !options.some(item => item.ref === value);
  return <div><label>凭据引用<select value={value ?? ""} disabled={loading || !catalog} onChange={e => onChange(e.target.value || null)}><option value="">不使用凭据</option>{unavailable && <option value={value!} disabled>{value} · 当前未获准或目录未提供</option>}{options.map(item => <option key={item.ref} value={item.ref}>{item.ref}</option>)}</select></label>
    {error && <Notice kind="error">{error}</Notice>}{loading && <small className="muted">读取获准凭据引用…</small>}{!loading && catalog && !options.length && <small className="muted">此接入类型暂无获准凭据；需要服务端配置后才能选择。</small>}{unavailable && catalog && <Notice>当前引用不在获准目录中，已保留草稿值；保存不代表该引用可用。</Notice>}{error && <button type="button" className="text-button" onClick={reload}>重新读取凭据目录</button>}
  </div>;
}
