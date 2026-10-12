import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PersonalCenter } from "../console/src/PersonalCenter";
import { ApiClient } from "../shared/api";
import type { Profile, UserKey } from "../shared/accounts";

const profile: Profile = { user_id: "account-a", username: "zhang", display_name: "张明", scope: "management", workspaces: [], fixture_mode: false, is_platform_admin: false, must_change_password: false, auth_method: "password", preferences: { density: "compact", text_size: "large" } };
const key: UserKey = { id: "key-a", name: "业务门户", prefix: "demo", created_at: "2026-09-01", expires_at: null, revoked: false };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function renderCenter(api: ApiClient, tab = "资料", close = vi.fn(), changed = vi.fn()) {
  function Harness() {
    const [current, setCurrent] = useState(profile);
    return <PersonalCenter api={api} profile={current} initialTab={tab} onProfile={p => { setCurrent(p); changed(p); }} onClose={close} onPasswordChanged={vi.fn()} />;
  }
  return render(<Harness />);
}

it("previews text size immediately, saves only that field and reverts on failure", async () => {
  const api = new ApiClient("management"), changed = vi.fn();
  let fail!: (reason: Error) => void;
  const patch = vi.spyOn(api, "patch").mockImplementation(() => new Promise((_, reject) => { fail = reject; }));
  renderCenter(api, "偏好设置", vi.fn(), changed);
  fireEvent.change(screen.getByRole("combobox", { name: "字号" }), { target: { value: "extra_large" } });
  expect(changed).toHaveBeenLastCalledWith(expect.objectContaining({ preferences: { density: "compact", text_size: "extra_large" } }));
  expect(patch).toHaveBeenCalledWith("/api/v2/me", { preferences: { text_size: "extra_large" } });
  expect(screen.getByRole("combobox", { name: "字号" })).toBeDisabled();
  fail(new Error("保存失败"));
  await screen.findByText("保存失败");
  expect(screen.getByRole("combobox", { name: "字号" })).toHaveValue("large");
  expect(changed).toHaveBeenLastCalledWith(profile);
});

it("restores only font defaults and preserves independent density preference", async () => {
  const api = new ApiClient("management");
  const patch = vi.spyOn(api, "patch").mockImplementation(async (_path, body) => ({ ...profile, preferences: { ...profile.preferences, ...(body as { preferences: object }).preferences } }) as never);
  renderCenter(api, "偏好设置");
  fireEvent.click(screen.getByRole("button", { name: "恢复默认字号" }));
  await waitFor(() => expect(screen.getByRole("combobox", { name: "字号" })).not.toBeDisabled());
  expect(patch).toHaveBeenCalledWith("/api/v2/me", { preferences: { text_size: "standard" } });
  expect(screen.getByRole("button", { name: "紧凑" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "舒适" }));
  await waitFor(() => expect(patch).toHaveBeenLastCalledWith("/api/v2/me", { preferences: { density: "comfortable" } }));
});

it("asks inline before discarding display-name edits and blocks closing during save", async () => {
  const api = new ApiClient("management"), close = vi.fn();
  let finish!: (p: Profile) => void;
  vi.spyOn(api, "patch").mockImplementation(() => new Promise(resolve => { finish = resolve as (p: Profile) => void; }));
  renderCenter(api, "资料", close);
  fireEvent.change(screen.getByLabelText("显示名称"), { target: { value: "张先生" } });
  fireEvent.click(screen.getByRole("button", { name: "关闭弹窗" }));
  expect(close).not.toHaveBeenCalled(); expect(screen.getAllByRole("dialog")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
  expect(screen.getByLabelText("显示名称")).toHaveValue("张先生");
  fireEvent.submit(screen.getByRole("button", { name: "保存资料" }).closest("form")!);
  expect(screen.getByRole("button", { name: "关闭弹窗" })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(close).not.toHaveBeenCalled();
  finish({ ...profile, display_name: "张先生" });
  await waitFor(() => expect(screen.getByRole("button", { name: "关闭弹窗" })).not.toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "关闭弹窗" }));
  expect(close).toHaveBeenCalledOnce();
});

it("preserves unfinished key fields across tabs and confirms discard in the same modal", async () => {
  const api = new ApiClient("management"), close = vi.fn(); vi.spyOn(api, "get").mockResolvedValue([]);
  renderCenter(api, "API Key", close);
  fireEvent.click(screen.getByRole("button", { name: "创建 Key" }));
  fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "未保存的门户" } });
  fireEvent.click(screen.getByRole("button", { name: "偏好设置" }));
  fireEvent.click(screen.getByRole("button", { name: "API Key" }));
  expect(screen.getByLabelText(/名称/)).toHaveValue("未保存的门户");
  fireEvent.click(screen.getByRole("button", { name: "取消创建" }));
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.getByRole("alert")).toHaveTextContent("未保存");
  fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
  fireEvent.click(screen.getByRole("button", { name: "关闭弹窗" }));
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
  expect(close).toHaveBeenCalledOnce();
});

it("keeps rotation confirmation and its one-time secret in one modal even if list refresh fails", async () => {
  const api = new ApiClient("management"), close = vi.fn();
  vi.spyOn(api, "get").mockResolvedValueOnce([key]).mockRejectedValueOnce(new Error("offline"));
  const post = vi.spyOn(api, "post").mockResolvedValue({ secret: "synthetic-rotated-key-once" });
  renderCenter(api, "API Key", close);
  fireEvent.click(await screen.findByRole("button", { name: "轮换" }));
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  expect(await screen.findByLabelText("一次性凭据")).toHaveValue("synthetic-rotated-key-once");
  await screen.findByText(/操作已完成，但列表刷新失败/);
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(post).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "关闭弹窗" }));
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByLabelText("一次性凭据")).toHaveValue("synthetic-rotated-key-once");
  fireEvent.click(screen.getByRole("button", { name: "继续保存凭据" }));
  fireEvent.click(screen.getByRole("button", { name: "已保存，关闭" }));
  expect(screen.queryByLabelText("一次性凭据")).toBeNull();
  expect(screen.getByRole("dialog", { name: "个人中心" })).toBeVisible();
});
