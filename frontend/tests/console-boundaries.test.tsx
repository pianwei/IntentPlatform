import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentEditor, ConsoleWorkspace } from "../console/src/App";
import { ApiClient } from "../shared/api";
import type { Capability } from "../shared/types";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const respond = (data: unknown) => new Response(JSON.stringify(data));
const capability = (id: string): Capability => ({
  id,
  workspace_id: id,
  name: `功能 ${id}`,
  description: "private resource",
  revision: 1,
  status: "published",
  latest_version: 1,
  is_fixture: false,
  definition: {
    name: id,
    description: "",
    object_type: "Object",
    action: "READ",
    result_type: "Detail",
    aliases: [],
    input_schema: {},
    connector: {
      kind: "http",
      endpoint: null,
      method: "GET",
      credential_ref: null,
      timeout_seconds: 10,
    },
    effect: "READ_ONLY",
    requires_confirmation: false,
    allowed_users: [],
    preconditions: [],
    presentation: { component: "detail", title: "结果", field_map: {} },
    positive_examples: [],
    negative_examples: [],
  },
});
describe("management consistency boundaries", () => {
  it("does not replace a newly opened editor in the same workspace with a closed editor's delayed save", async () => {
    let resolveSave!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { resolveSave = resolve; });
    const f = { ...capability("f"), workspace_id: "a" }; const g = { ...capability("g"), workspace_id: "a" };
    const fetch = vi.fn((path: string, init?: RequestInit) => {
      if (path === "/api/v2/management/workspaces") return Promise.resolve(respond([{ id: "a", name: "空间 A", roles: ["admin"] }]));
      if (path.endsWith("/a/functions")) return Promise.resolve(respond([f, g]));
      if (path.endsWith("/functions/f/draft") && init?.method === "PATCH") return pending;
      if (path.endsWith("/source-review")) return Promise.resolve(respond({ revision: 1, required: false, ready: true, issues: [], citations: [] }));
      return Promise.resolve(respond([]));
    });
    vi.stubGlobal("fetch", fetch);
    render(<ConsoleWorkspace api={new ApiClient("management")} identity={{ access_token: "test", user_id: "user", fixture_mode: false }} onLogout={async () => {}} />);
    await screen.findByText("功能 f"); fireEvent.click(within(screen.getByText("功能 f").closest("tr")!).getByRole("button", { name: "管理映射" }));
    fireEvent.change(screen.getByLabelText("业务描述"), { target: { value: "保存 F 的修改" } }); fireEvent.click(screen.getByRole("button", { name: "保存新草稿" }));
    await waitFor(() => expect(fetch.mock.calls.some(([path, init]) => path.endsWith("/functions/f/draft") && init?.method === "PATCH")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "关闭功能编辑" })); fireEvent.click(within(screen.getByText("功能 g").closest("tr")!).getByRole("button", { name: "管理映射" }));
    fireEvent.change(screen.getByLabelText("功能名称"), { target: { value: "G 未保存的修改" } });
    await act(async () => { resolveSave(respond({ ...f, revision: 2 })); await pending; });
    expect(screen.getByLabelText("功能名称")).toHaveValue("G 未保存的修改");
  });
  it("does not reopen a created function from the prior workspace after a delayed save", async () => {
    let resolveCreate!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { resolveCreate = resolve; });
    const fetch = vi.fn((path: string) => {
      if (path === "/api/v2/management/workspaces") return Promise.resolve(respond([{ id: "a", name: "空间 A", roles: ["admin"] }, { id: "b", name: "空间 B", roles: ["admin"] }]));
      if (path.endsWith("/a/functions/import")) return pending;
      if (path.endsWith("/b/functions")) return Promise.resolve(respond([capability("b")]));
      return Promise.resolve(respond([]));
    });
    vi.stubGlobal("fetch", fetch);
    render(<ConsoleWorkspace api={new ApiClient("management")} identity={{ access_token: "test", user_id: "user", fixture_mode: false }} onLogout={async () => {}} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "接入新功能" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "接入新功能" }));
    fireEvent.change(screen.getByLabelText("功能名称"), { target: { value: "新功能" } });
    fireEvent.change(screen.getByLabelText("业务描述"), { target: { value: "合成测试" } });
    fireEvent.change(screen.getByLabelText("业务对象"), { target: { value: "Object" } });
    fireEvent.change(screen.getByLabelText("结果类型"), { target: { value: "Detail" } });
    fireEvent.click(screen.getByRole("button", { name: "创建功能草稿" }));
    await waitFor(() => expect(fetch.mock.calls.some(([path]) => path.endsWith("/a/functions/import"))).toBe(true));
    fireEvent.change(screen.getByLabelText("切换工作空间"), { target: { value: "b" } });
    await screen.findByText("功能 b");
    await act(async () => { resolveCreate(respond(capability("a"))); await pending; });
    expect(screen.queryByRole("dialog", { name: "管理功能" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("切换工作空间")).toHaveValue("b");
    expect(screen.getByText("功能 b")).toBeVisible();
  });
  it("ignores a late response from the prior workspace after the user switches scope", async () => {
    let resolveA!: (value: Response) => void;
    const oldResponse = new Promise<Response>((resolve) => {
      resolveA = resolve;
    });
    const fetch = vi.fn((path: string) => {
      if (path === "/api/v2/management/workspaces")
        return Promise.resolve(
          respond([
            { id: "a", name: "空间 A", roles: ["admin"] },
            { id: "b", name: "空间 B", roles: ["user"] },
          ]),
        );
      if (path.endsWith("/a/functions")) return oldResponse;
      if (path.endsWith("/b/functions"))
        return Promise.resolve(respond([capability("b")]));
      return Promise.resolve(respond([]));
    });
    vi.stubGlobal("fetch", fetch);
    render(
      <ConsoleWorkspace
        api={new ApiClient("management")}
        identity={{
          access_token: "test",
          user_id: "user",
          fixture_mode: false,
        }}
        onLogout={async () => {}}
      />,
    );
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(([path]) => path.endsWith("/a/functions")),
      ).toBe(true),
    );
    fireEvent.change(screen.getByLabelText("切换工作空间"), {
      target: { value: "b" },
    });
    await screen.findByText("功能 b");
    await act(async () => {
      resolveA(respond([capability("a")]));
      await oldResponse;
    });
    expect(screen.queryByText("功能 a")).not.toBeInTheDocument();
    expect(screen.getByText("功能 b")).toBeVisible();
    expect(screen.getByRole("button", { name: "接入新功能" })).toBeDisabled();
  });
  it("saves the observed agent revision so the server can reject stale drafts", async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(respond({ id: "agent-a" })));
    vi.stubGlobal("fetch", fetch);
    const onSaved = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentEditor
        api={new ApiClient("management")}
        workspace="space-a"
        item={{
          id: "agent-a",
          workspace_id: "space-a",
          name: "原名",
          revision: 7,
          capability_ids: ["cap-a"],
          allowed_origins: [],
          suggested_questions: [],
          fixture_mode: false,
        }}
        functions={[capability("cap-a")]}
        onClose={() => {}}
        onSaved={onSaved}
      />,
    );
    fireEvent.change(screen.getByLabelText("智能体名称"), {
      target: { value: "新名" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存智能体草稿" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    const patch = fetch.mock.calls.find(([, options]) => options.method === "PATCH")!;
    expect(patch[0]).toBe(
      "/api/v2/management/workspaces/space-a/agents/agent-a/draft",
    );
    expect(JSON.parse(patch[1].body)).toMatchObject({
      revision: 7,
      name: "新名",
      capability_ids: ["cap-a"],
    });
    expect(patch[1].method).toBe("PATCH");
  });
});
