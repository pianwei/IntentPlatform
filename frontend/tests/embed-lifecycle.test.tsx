import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentApp } from "../agent-web/src/App";
const originalParent = Object.getOwnPropertyDescriptor(window, "parent");
const originalReferrer = Object.getOwnPropertyDescriptor(document, "referrer");
afterEach(() => {
  cleanup();
  if (originalParent) Object.defineProperty(window, "parent", originalParent);
  if (originalReferrer)
    Object.defineProperty(document, "referrer", originalReferrer);
  else delete (document as unknown as { referrer?: string }).referrer;
  history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});
it("host logout during ticket exchange never resurrects the arriving credential", async () => {
  const parent = { postMessage: vi.fn() } as unknown as Window;
  Object.defineProperty(window, "parent", {
    value: parent,
    configurable: true,
  });
  Object.defineProperty(document, "referrer", {
    value: "https://host.internal/page",
    configurable: true,
  });
  history.replaceState(null, "", "/embed/public-a");
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  let resolveExchange!: (response: Response) => void;
  const exchange = new Promise<Response>((resolve) => {
    resolveExchange = resolve;
  });
  const fetch = vi.fn((path: string) => {
    if (path === "/api/v2/runtime/apps/public-a") return Promise.resolve(new Response(JSON.stringify({ public_id: "public-a", agent_id: "agent-a", release_id: "release-a", channels: { chat: true, embed: true }, fixture_mode: true, runtime_ui: { title: "测试助手", icon: { kind: "emoji", value: "✦" }, description: "", welcome_message: "欢迎", suggested_questions: [], primary_color: "#137C8B", input_placeholder: "输入", show_history: true } })));
    if (path.endsWith("/handshakes"))
      return Promise.resolve(
        new Response(
          JSON.stringify({
            instance_nonce: "nonce-a",
            expires_at: "2099-01-01T00:00:00Z",
          }),
        ),
      );
    if (path.endsWith("/embed/exchange")) return exchange;
    return Promise.resolve(new Response(JSON.stringify({ revoked: true })));
  });
  vi.stubGlobal("fetch", fetch);
  render(<AgentApp />);
  await waitFor(() => expect(parent.postMessage).toHaveBeenCalled());
  function host(type: string) {
    window.dispatchEvent(
      new MessageEvent("message", {
        origin: "https://host.internal",
        source: parent,
        data: {
          type,
          protocol_version: "1",
          agent_id: "agent-a",
          instance_nonce: "nonce-a",
          ticket: "one-time",
        },
      }),
    );
  }
  await act(async () => {
    host("AGENT_INIT");
  });
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([path]) => path.endsWith("/embed/exchange")),
    ).toBe(true),
  );
  await act(async () => {
    host("HOST_LOGOUT");
  });
  await act(async () => {
    resolveExchange(
      new Response(
        JSON.stringify({
          access_token: "arrived-after-logout",
          user_id: "dev-reader",
          fixture_mode: true,
        }),
      ),
    );
    await exchange;
  });
  expect(
    fetch.mock.calls.some(([path]) => path.startsWith("/api/v2/runtime/agents")),
  ).toBe(false);
  expect(parent.postMessage).toHaveBeenCalledWith(
    expect.objectContaining({
      type: "AUTH_REQUIRED",
      instance_nonce: "nonce-a",
    }),
    "https://host.internal",
  );
});
