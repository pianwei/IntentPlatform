import type { ApiClient } from "./api";
import { management } from "./api";

export type IngestStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED";
export type IngestDiagnostic = { code: string; message: string; pointer: string };
export type IngestJob = {
  job_id: string;
  kind: string;
  status: IngestStatus;
  created_at: string;
  updated_at: string;
  result: { document_id?: string; revision?: number; import_id?: string } | null;
  error: { code: string; message: string; diagnostics?: IngestDiagnostic[] } | null;
};
export type IngestAccepted = Pick<IngestJob, "job_id" | "status">;

export class IngestJobFailedError extends Error {
  constructor(public code: string, message: string, public diagnostics: IngestDiagnostic[] = []) {
    super(message);
    this.name = "IngestJobFailedError";
  }
}

export const ingestBase = (workspace: string) => `${management(workspace)}/ingest-jobs`;

export function idempotencyKey(): string {
  if (!globalThis.crypto?.randomUUID) throw new Error("当前浏览器无法生成安全请求标识");
  return globalThis.crypto.randomUUID();
}

function pause(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException("轮询已取消", "AbortError"));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { signal?.removeEventListener("abort", aborted); resolve(); }, 800);
    const aborted = () => { clearTimeout(timer); reject(new DOMException("轮询已取消", "AbortError")); };
    signal?.addEventListener("abort", aborted, { once: true });
  });
}

export async function pollIngestJob(
  api: ApiClient,
  workspace: string,
  jobId: string,
  onStatus: (status: IngestStatus) => void,
  signal?: AbortSignal,
): Promise<IngestJob> {
  for (;;) {
    const job = await api.get<IngestJob>(`${ingestBase(workspace)}/${encodeURIComponent(jobId)}`, signal);
    if (job.job_id !== jobId || !["PENDING", "RUNNING", "SUCCEEDED", "FAILED"].includes(job.status)) {
      throw new Error("接入任务状态响应无效");
    }
    onStatus(job.status);
    if (job.status === "SUCCEEDED") return job;
    if (job.status === "FAILED") throw new IngestJobFailedError(job.error?.code || "INGEST_FAILED", job.error?.message || "接入任务失败，请核对来源并重试", job.error?.diagnostics || []);
    await pause(signal);
  }
}
