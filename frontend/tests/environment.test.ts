import { afterEach, describe, expect, it, vi } from "vitest";
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});
describe("explicit development build boundary", () => {
  it("rejects local development login in production before any network operation", async () => {
    vi.resetModules();
    vi.stubEnv("MODE", "production");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const { ApiClient, developmentLogin } = await import("../shared/api");
    await expect(
      developmentLogin(new ApiClient("management"), "dev-admin"),
    ).rejects.toThrow("生产构建");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("allows the explicit development mode to request a server validated fixture identity", async () => {
    vi.resetModules();
    vi.stubEnv("MODE", "development");
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            access_token: "development-token",
            user_id: "dev-reader",
            fixture_mode: true,
          }),
        ),
      );
    vi.stubGlobal("fetch", fetch);
    const { ApiClient, developmentLogin } = await import("../shared/api");
    const identity = await developmentLogin(
      new ApiClient("management"),
      "dev-reader",
    );
    expect(identity.user_id).toBe("dev-reader");
    expect(fetch.mock.calls[0][0]).toBe("/api/v2/dev/login");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      user_id: "dev-reader",
    });
  });
});
