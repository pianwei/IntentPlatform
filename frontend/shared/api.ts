import type { Identity } from "./types";
import { DEVELOPMENT_UI } from "./environment";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export class ApiClient {
  private token: string | null = null;
  private renewal: Promise<boolean> | null = null;
  constructor(
    private scope: "management" | "runtime",
    private onExpired?: () => void,
  ) {}
  setToken(token: string | null) {
    this.token = token;
  }
  clear() {
    this.token = null;
  }
  private async response(path: string, options: RequestInit = {}, retried = false): Promise<Response> {
    if (
      (!path.startsWith("/api/v2/") && path !== "/health") ||
      path.includes("\\")
    )
      throw new Error("仅允许同源项目 API");
    const pathname = new URL(path, "https://same-origin.invalid").pathname;
    if (
      this.scope === "runtime" &&
      (pathname === "/api/v2/management" ||
        pathname.startsWith("/api/v2/management/"))
    )
      throw new ApiError(403, "运行会话不能访问管理接口");
    const headers = new Headers(options.headers);
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    if (options.body && !(options.body instanceof FormData))
      headers.set("Content-Type", "application/json");
    const response = await fetch(path, {
      ...options,
      headers,
      credentials: this.scope === "management" && pathname.startsWith("/api/v2/auth/") ? "same-origin" : "omit",
      cache: "no-store",
    });
    if (!response.ok) {
      if (response.status === 401 && this.scope === "management" && !retried && !pathname.startsWith("/api/v2/auth/")) {
        if (!this.renewal) this.renewal = this.renew().finally(() => { this.renewal = null; });
        if (await this.renewal) return this.response(path, options, true);
      }
      const data = await response.json().catch(() => null);
      if (response.status === 401) {
        this.clear();
        this.onExpired?.();
      }
      const detail = data?.detail;
      throw new ApiError(
        response.status,
        typeof detail === "string"
          ? detail
          : detail
            ? JSON.stringify(detail)
            : `请求失败（${response.status}）`,
      );
    }
    return response;
  }
  private async renew(): Promise<boolean> {
    try {
      const response = await fetch("/api/v2/auth/refresh", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!response.ok) return false;
      const value = await response.json();
      if (typeof value.access_token !== "string") return false;
      this.token = value.access_token; return true;
    } catch { return false; }
  }
  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    return (await this.response(path, options)).json() as Promise<T>;
  }
  async blob(path: string): Promise<Blob> {
    return (await this.response(path)).blob();
  }
  get<T>(path: string, signal?: AbortSignal) {
    return this.request<T>(path, { signal });
  }
  post<T>(path: string, body: unknown = {}) {
    return this.request<T>(path, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  patch<T>(path: string, body: unknown) {
    return this.request<T>(path, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }
  put<T>(path: string, body: unknown) {
    return this.request<T>(path, { method: "PUT", body: JSON.stringify(body) });
  }
}
export async function developmentLogin(
  api: ApiClient,
  userId: string,
): Promise<Identity> {
  if (!DEVELOPMENT_UI) throw new Error("生产构建不提供开发身份登录");
  const identity = await api.post<Identity>("/api/v2/dev/login", {
    user_id: userId,
  });
  api.setToken(identity.access_token);
  return identity;
}
export function errorText(error: unknown) {
  return error instanceof Error ? error.message : "请求未完成，请重试";
}
export const management = (workspace: string) =>
  `/api/v2/management/workspaces/${encodeURIComponent(workspace)}`;
