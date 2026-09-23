import { useEffect, useState } from "react";
import { ApiClient, errorText, management } from "../../shared/api";
import { parseCatalog, type GovernanceCatalog } from "../../shared/governance";

export function useGovernanceCatalog(api: ApiClient, workspace: string) {
  const [catalog, setCatalog] = useState<GovernanceCatalog | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let active = true;
    setCatalog(null); setError(""); setLoading(true);
    api.get<unknown>(`${management(workspace)}/catalogs`).then(value => { if (active) setCatalog(parseCatalog(value)); }).catch(e => { if (active) setError(errorText(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api, workspace, tick]);
  return { catalog, error, loading, reload: () => setTick(value => value + 1) };
}
