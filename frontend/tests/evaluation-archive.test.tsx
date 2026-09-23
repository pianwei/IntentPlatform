import { readFileSync } from "node:fs";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EvaluationArchiveViewer } from "../console/src/EvaluationArchiveViewer";
import { originalArchiveBlob, parseArchive, S1_RATES } from "../shared/evaluationArchive";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const emptyRate = { numerator: 0, denominator: 0, value: null, defined: false };
function fixture() { return { schema_version: "1", kind: "intent-evaluation-trial", trial_id: "synthetic-viewer-test", created_at: "2026-09-22T00:00:00Z", acceptance_evidence: false, dataset: {}, observations: {}, trace: [], runtime_evidence: {}, gaps: ["OFFLINE_OBSERVATIONS_UNVERIFIED"], redactions_applied: true, report: { schema_version: "1", trial_id: "synthetic-viewer-test", scorer_version: "gold-exact-v1", observation_normalization_version: "runtime-observation-v1", acceptance_evidence: false, evidence_kind: "development", dataset: { dataset_id: "test", version: "v1", purpose: "development" }, configuration: { resource_mode: "synthetic" }, annotation: { provenance: "synthetic" }, total_cases: 0, observed_cases: 0, s1: { n: 0, a: 0, c: 0, ...Object.fromEntries(S1_RATES.map(k => [k, emptyRate])) }, correct_cases: emptyRate, per_function: {}, per_function_macro_route_accuracy: null, s2: { authorization: emptyRate }, out_of_scope_false_acceptance: emptyRate, s3_scenario_completion: emptyRate, unobserved_safety_steps: 2, unauthorized_executions: null, leakage_events: null, difference_counts: {}, observed_fault_counts: {}, observed_outcome_counts: {}, confusion: {}, cases: [], warnings: [] } }; }
function encode(value: unknown) { return new TextEncoder().encode(JSON.stringify(value)).buffer as ArrayBuffer; }
it("preserves original UTF-8 bytes including numeric spelling rather than reserializing display data", async () => {
  const raw = JSON.stringify(fixture()).replace('"dataset":{}', '"dataset":{"number":1.0}'); const bytes = new TextEncoder().encode(raw).buffer as ArrayBuffer;
  const imported = parseArchive(bytes); expect(new TextDecoder().decode(imported.bytes)).toContain('"number":1.0'); expect(new Uint8Array(imported.bytes)).toEqual(new Uint8Array(bytes));
  const blob = originalArchiveBlob(imported); const result = await new Promise<ArrayBuffer>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result as ArrayBuffer); reader.onerror = reject; reader.readAsArrayBuffer(blob); });
  expect(new Uint8Array(result)).toEqual(new Uint8Array(bytes));
});
it("rejects affirmative acceptance flags and malformed rate objects", () => {
  const f = fixture(); expect(() => parseArchive(encode({ ...f, acceptance_evidence: true }))).toThrow(/acceptance_evidence=false/);
  expect(() => parseArchive(encode({ ...f, report: { ...f.report, s2: { authorization: "[REDACTED]" } } }))).toThrow(/S2 authorization/);
});
it("imports locally with no fetch and presents undefined metrics and missing safety evidence", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch); render(<EvaluationArchiveViewer />);
  const file = new File([encode(fixture())], "trial.json", { type: "application/json" }); Object.defineProperty(file, "arrayBuffer", { value: async () => encode(fixture()) });
  fireEvent.change(screen.getByLabelText("导入 trial.json"), { target: { files: [file] } });
  await screen.findByText("synthetic-viewer-test"); expect(fetch).not.toHaveBeenCalled(); expect(screen.getAllByText("未定义").length).toBeGreaterThan(0); expect(screen.getByText("未观测安全步骤").parentElement).toHaveTextContent("2"); expect(screen.getByText(/acceptance_evidence = false/)).toBeInTheDocument(); expect(screen.getByText("OFFLINE_OBSERVATIONS_UNVERIFIED")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "清除本地查看" })); await waitFor(() => expect(screen.queryByText("synthetic-viewer-test")).not.toBeInTheDocument());
});
it("distinguishes declared sanitization scope from unverified unknown secrets, including legacy archives", async () => {
  render(<EvaluationArchiveViewer />);
  async function upload(value: unknown) { const file = new File([encode(value)], "trial.json"); Object.defineProperty(file, "arrayBuffer", { value: async () => encode(value) }); fireEvent.change(screen.getByLabelText("导入 trial.json"), { target: { files: [file] } }); await screen.findByText("synthetic-viewer-test"); }
  await upload(fixture()); expect(screen.getByText(/未提供核验范围/)).toBeInTheDocument();
  await upload({ ...fixture(), sanitization: { mode: "known-secrets-and-wire-fields", score_inputs: "preserved", unknown_secrets_checked: false } });
  expect(screen.getByText(/未知秘密：未自动核验/)).toBeInTheDocument(); expect(screen.getByText(/known-secrets-and-wire-fields/)).toBeInTheDocument();
});
it.skipIf(!process.env.INTENT_EVALUATION_SAMPLE)("opens the separately generated PostgreSQL runtime archive without rewriting bytes", () => {
  const raw = readFileSync(process.env.INTENT_EVALUATION_SAMPLE!); const bytes = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer; const imported = parseArchive(bytes);
  expect(new Uint8Array(imported.bytes)).toEqual(new Uint8Array(bytes)); expect(imported.archive.acceptance_evidence).toBe(false); expect(imported.archive.report.unobserved_safety_steps).toBe(2); expect(imported.archive.redactions_applied).toBe(true);
});
