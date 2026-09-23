export type ArchiveRecord = Record<string, unknown>;
export type EvaluationArchive = { schema_version: "1"; kind: "intent-evaluation-trial"; trial_id: string; created_at: string; acceptance_evidence: false; dataset: ArchiveRecord; observations: ArchiveRecord; report: ArchiveRecord; trace: unknown[]; runtime_evidence: ArchiveRecord; gaps: unknown[]; redactions_applied: boolean; sanitization?: { mode?: string; score_inputs?: string; unknown_secrets_checked?: boolean } };
export type ImportedArchive = { archive: EvaluationArchive; bytes: ArrayBuffer; filename: string };
export function record(value: unknown): ArchiveRecord { return value !== null && typeof value === "object" && !Array.isArray(value) ? value as ArchiveRecord : {}; }
function isRecord(value: unknown): value is ArchiveRecord { return value !== null && typeof value === "object" && !Array.isArray(value); }
function rateValid(value: unknown) { const r = record(value); return Number.isInteger(r.numerator) && Number.isInteger(r.denominator) && Number(r.numerator) >= 0 && Number(r.denominator) >= 0 && typeof r.defined === "boolean" && (r.defined ? typeof r.value === "number" && Number.isFinite(r.value) && Number(r.denominator) > 0 : r.value === null && r.denominator === 0); }
export function parseArchive(bytes: ArrayBuffer, filename = "trial.json"): ImportedArchive {
  if (bytes.byteLength > 20 * 1024 * 1024) throw new Error("文件超过 20 MB，请使用离线工具查看更大的归档。");
  const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  if (!isRecord(value) || value.schema_version !== "1" || value.kind !== "intent-evaluation-trial") throw new Error("仅支持 schema_version=1 的 intent-evaluation-trial 结构归档。");
  if (value.acceptance_evidence !== false || record(value.report).acceptance_evidence !== false) throw new Error("该归档不符合固定的 acceptance_evidence=false 边界，不能作为此版本报告打开。");
  if (typeof value.trial_id !== "string" || typeof value.created_at !== "string" || typeof value.redactions_applied !== "boolean" || !Array.isArray(value.trace) || !Array.isArray(value.gaps)) throw new Error("归档元数据缺失或类型错误。");
  for (const key of ["dataset", "observations", "report", "runtime_evidence"]) if (!isRecord(value[key])) throw new Error(`${key} 必须是对象。`);
  const report = record(value.report);
  if (report.trial_id !== value.trial_id || report.schema_version !== "1" || report.scorer_version !== "gold-exact-v1" || report.observation_normalization_version !== "runtime-observation-v1") throw new Error("归档试验标识或评分/观测版本不受支持。");
  for (const key of ["configuration", "dataset", "s1", "per_function", "s2", "difference_counts", "observed_fault_counts", "observed_outcome_counts", "confusion"]) if (!isRecord(report[key])) throw new Error(`report.${key} 结构缺失。`);
  for (const key of ["correct_cases", "out_of_scope_false_acceptance", "s3_scenario_completion"]) if (!rateValid(report[key])) throw new Error(`report.${key} 分子分母结构无效。`);
  for (const [key, rate] of Object.entries(record(report.s2))) if (!rateValid(rate)) throw new Error(`S2 ${key} 指标缺失或被错误脱敏。`);
  for (const metrics of [record(report.s1), ...Object.values(record(report.per_function)).map(record)]) {
    for (const key of ["n", "a", "c"]) if (!Number.isInteger(metrics[key]) || Number(metrics[key]) < 0) throw new Error(`S1 ${key} 分母无效。`);
    for (const key of S1_RATES) if (!rateValid(metrics[key])) throw new Error(`S1 ${key} 分子分母结构无效。`);
  }
  if (!Array.isArray(report.warnings) || !Array.isArray(report.cases)) throw new Error("报告的 warnings 与 cases 必须是数组。");
  return { archive: value as unknown as EvaluationArchive, bytes: bytes.slice(0), filename };
}
export const S1_RATES = ["correct_route_rate", "decided_accuracy", "decision_coverage", "argument_accuracy", "semantic_accuracy", "automatic_execution_coverage", "confirmation_zero_execution", "confirmation_binding", "post_confirmation_success", "end_to_end_success"];
export const metricNames: Record<string, string> = { correct_route_rate: "正确路由率", decided_accuracy: "已决定准确率", decision_coverage: "决定覆盖率", argument_accuracy: "参数准确率", semantic_accuracy: "语义准确率", automatic_execution_coverage: "自动执行覆盖率", confirmation_zero_execution: "确认前零执行", confirmation_binding: "确认绑定正确率", post_confirmation_success: "确认后成功率", end_to_end_success: "端到端成功率", ambiguity: "歧义", authorization: "授权边界", out_of_scope: "域外", correct_cases: "完整正确用例", out_of_scope_false_acceptance: "域外误接纳", s3_scenario_completion: "S3 场景完整率" };
export function originalArchiveBlob(imported: ImportedArchive) { return new Blob([imported.bytes], { type: "application/json" }); }
