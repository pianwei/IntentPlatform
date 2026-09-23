import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../shared/api";
afterEach(() => vi.unstubAllGlobals());
describe("in-memory scoped API client", () => {
  it("blocks a runtime credential from reaching management routes before transport", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const api = new ApiClient("runtime");
    api.setToken("runtime-secret");
    await expect(
      api.get("/api/v2/management/workspaces"),
    ).rejects.toMatchObject({ status: 403 });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("sends bearer credentials only to same-origin API paths and clears them on expiry", async () => {
    const expired = vi.fn();
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "expired" }), { status: 401 }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify([])));
    vi.stubGlobal("fetch", fetch);
    const store = vi.spyOn(Storage.prototype, "setItem");
    const api = new ApiClient("runtime", expired);
    api.setToken("memory-only");
    await expect(api.get("/api/v2/runtime/agents")).rejects.toMatchObject({
      status: 401,
    });
    await api.get("/api/v2/runtime/agents");
    expect(
      (fetch.mock.calls[0][1].headers as Headers).get("Authorization"),
    ).toBe("Bearer memory-only");
    expect(
      (fetch.mock.calls[1][1].headers as Headers).get("Authorization"),
    ).toBeNull();
    expect(expired).toHaveBeenCalledOnce();
    expect(store).not.toHaveBeenCalled();
    await expect(api.get("https://external.example/api")).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it("does not automatically retry a failed mutation that may have executed", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValue(new TypeError("connection interrupted"));
    vi.stubGlobal("fetch", fetch);
    const api = new ApiClient("runtime");
    await expect(
      api.post("/api/v2/runtime/runs/run-a/confirmations", {
        revision: 1,
        confirmed: true,
      }),
    ).rejects.toThrow("connection interrupted");
    expect(fetch).toHaveBeenCalledOnce();
  });
});
