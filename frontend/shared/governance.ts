import type { Channels } from "./types";

export type GovernanceRole = "admin" | "maintainer" | "publisher" | "user";
export const roleLabels: Record<GovernanceRole, string> = { admin: "管理员", maintainer: "维护者", publisher: "发布者", user: "使用者" };
export const connectorLabels: Record<string, string> = { http: "HTTP 接口", dify: "Dify 工作流", hiagent: "HiAgent 工作流", fixture: "开发夹具" };
export type GovernanceCatalog = {
  integrations: { ref: string; label: string; version: string; channels: Channels | string[]; fixture_mode: boolean }[];
  resolvers: { ref: string; version: string; object_types: string[] }[];
  credentials: { ref: string; connector_kind: string }[];
};
export function parseCatalog(value: unknown): GovernanceCatalog {
  const catalog = value as GovernanceCatalog | null;
  if (!catalog || !Array.isArray(catalog.integrations) || !Array.isArray(catalog.resolvers) || !Array.isArray(catalog.credentials)) throw new Error("当前服务端未提供完整的获准引用目录，请核对治理接口版本。");
  return catalog;
}
export type WorkspaceMember = { user_id: string; roles: GovernanceRole[]; active: boolean; revision: number; ceiling_roles: GovernanceRole[]; server_granted: boolean };
export type LimitValues = { max_queued_runs: number; max_routing: number; max_execution: number };
export type WorkspaceLimits = { revision: number; limits: LimitValues; ceiling: LimitValues; usage: { queued_runs: number | null; routing: number | null; execution: number | null } };
export type ProcessingAttempt = {
  id: string; run_id: string; revision: number; kind: "routing" | "execution" | "reconciliation"; call_id: string | null; status: string;
  queued_at: string | null; started_at: string; finished_at: string | null; lease_until: string;
  queue_ms: number | null; context_ms: number | null; model_ms: number | null; validation_ms: number | null; connector_ms: number | null; total_ms: number | null; error_stage: string | null;
};
export const timingLabels = { queue_ms: "排队", context_ms: "上下文", model_ms: "模型", validation_ms: "校验", connector_ms: "连接器", total_ms: "总耗时" } as const;
export function observed(value: number | null | undefined, unit = "") { return typeof value === "number" && Number.isFinite(value) ? `${value}${unit}` : "未观测"; }
