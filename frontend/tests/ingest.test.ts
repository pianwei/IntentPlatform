import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../shared/api";
import { pollIngestJob } from "../shared/ingest";

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

it("polls a durable ingestion job through running to success", async () => {
  vi.useFakeTimers();
  const api = new ApiClient("management");
  const get = vi.spyOn(api, "get")
    .mockResolvedValueOnce({ job_id: "job", status: "PENDING" })
    .mockResolvedValueOnce({ job_id: "job", status: "RUNNING" })
    .mockResolvedValueOnce({ job_id: "job", status: "SUCCEEDED", result: { document_id: "doc", revision: 2 } });
  const statuses: string[] = [];
  const finished = pollIngestJob(api, "ws", "job", status => statuses.push(status));
  await vi.advanceTimersByTimeAsync(1600);
  expect((await finished).result).toEqual({ document_id: "doc", revision: 2 });
  expect(statuses).toEqual(["PENDING", "RUNNING", "SUCCEEDED"]);
  expect(get).toHaveBeenCalledTimes(3);
  expect(get).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/ingest-jobs/job", undefined);
});

it("shows a safe failed-job diagnosis without polling forever", async () => {
  const api = new ApiClient("management");
  const get = vi.spyOn(api, "get").mockResolvedValue({ job_id: "job", status: "FAILED", result: null, error: { code: "INVALID_DOCUMENT", message: "文档无法解析" } });
  await expect(pollIngestJob(api, "ws", "job", () => {})).rejects.toThrow("文档无法解析");
  expect(get).toHaveBeenCalledOnce();
});
