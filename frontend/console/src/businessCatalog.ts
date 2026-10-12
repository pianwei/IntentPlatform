import { management } from "../../shared/api";

export type CatalogKind = "object" | "action";
export type CatalogItem = {
  id: string; kind: CatalogKind; key: string; name: string; description: string;
  effect: "READ_ONLY" | "WRITE" | null; enabled: boolean; revision: number;
  latest_version: number; origin: "platform" | "workspace" | "legacy";
  default_id: string | null; adopted_version: number | null; created_at?: string;
};
export type CatalogVersion = { id: string; version: number; definition: Record<string, unknown>; created_at: string };
export type CatalogPage = { items: CatalogItem[]; defaults?: CatalogItem[] };
export type CatalogRelations = { objects: {
  id: string; name: string; actions: { key: string; name: string; functions: {
    id: string; name: string; version: number | null; execution_kind: string;
    composites: { id: string; name: string; version: number | null }[];
  }[] }[];
}[] };
export type AdoptionImpact = { functions: { id: string; name: string }[]; changes: Record<string, unknown>; adopted_version: number | null; target_version: number };
export const catalogPath = (workspace?: string) => workspace ? `${management(workspace)}/business-catalog` : "/api/v2/management/platform/business-catalog";
