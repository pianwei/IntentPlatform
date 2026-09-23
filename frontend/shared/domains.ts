export type DomainDefinition = {
  name: string; description: string;
  source: { reference: string; owner: string; document_id?: string | null; revision?: number | null; section?: string | null };
  concepts: { id: string; label: string; kind: "object" | "result"; parents: string[] }[];
  relations: { id: string; source_type: string; target_type: string }[];
  units: { id: string; dimension: string }[];
  conversions: { from_unit: string; to_unit: string; factor: number }[];
};
export type DomainValidation = { valid: boolean; revision: number; errors: string[]; checks: Record<string, unknown> };
export type DomainDraft = { id: string; workspace_id: string; key: string; revision: number; latest_version: number; definition: DomainDefinition; validation: DomainValidation | null };
export type DomainVersion = { id: string; package_id: string; workspace_id: string; version: number; definition: DomainDefinition; compiled: Record<string, unknown>; published_by: string };
export type ResolverReference = { reference: string; version: string; entity_type: string };
export const blankDomain: DomainDefinition = { name: "", description: "", source: { reference: "", owner: "" }, concepts: [], relations: [], units: [], conversions: [] };
