import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RunTracePage } from "../console/src/RunTracePage";
import { ApiClient } from "../shared/api";

const turn = (id: string, phases = [{ layer: "entity", count: 1, status: "RECORDED" }], results: Record<string, unknown>[] = [{ component: "text", data: `${id}最终回答` }]) => ({ run_id: id, question: `${id}问题`, status: "SUCCEEDED", timing: { total_ms: 100, processing_ms: 80, user_wait_ms: 20 }, results, prompts: [], interactions: [], phases });
const detail = (turns = [turn("第一"), turn("第二")]) => ({ id: "c", title: "资金问答", agent_name: "业务助手", turn_count: turns.length, offset: 0, limit: 25, turns });
const trace = { layers: { entity: [{ title: "对象已识别", created_at: "2026-09-29T00:00:00Z", display: [] }], planning: [{ title: "计划校验失败", created_at: "2026-09-29T00:00:00Z", display: [] }] }, steps: [{ name: "账户查询", status: "SUCCEEDED", input: {}, output: "仅属于步骤的输出", elapsed_ms: 30 }], attempts: [] };
beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function client(value = detail()) {
  const api = new ApiClient("management");
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.includes("/conversations/c?")) return value as never;
    if (path.endsWith("/trace")) return trace as never;
    return { items: [{ id: "c", title: "资金问答", agent_name: "业务助手", agent_id: "a", updated_at: "2026-09-29T00:00:00Z", turn_count: 2, status: "SUCCEEDED" }], total: 1, limit: 25, offset: 0 } as never;
  });
  return { api, get };
}

it("retains the selected turn, expanded phases and scroll position after refreshing", async () => {
  const { api, get } = client(); const view = render(<RunTracePage api={api} workspace="s" admin conversationId="c" />);
  await screen.findByText("第二最终回答");
  fireEvent.click(screen.getByRole("tab", { name: "第 1 轮" }));
  await screen.findByText("第一最终回答");
  fireEvent.click(screen.getByText("实体层").closest("summary")!);
  await screen.findByText("对象已识别");
  const scroll = view.container.querySelector(".detail-scroll") as HTMLElement;
  scroll.scrollTop = 300; fireEvent.scroll(scroll);
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  await waitFor(() => expect(get.mock.calls.filter(([path]) => path.includes("/conversations/c?"))).toHaveLength(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "刷新" })).not.toBeDisabled());
  expect(screen.getByRole("tab", { name: "第 1 轮" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("第一最终回答")).toBeVisible();
  expect(screen.getByText("实体层").closest("details")).toHaveAttribute("open");
  expect((view.container.querySelector(".detail-scroll") as HTMLElement).scrollTop).toBe(300);
});

it("keeps each turn's chosen phase expansion when moving between turns", async () => {
  const { api } = client(); render(<RunTracePage api={api} workspace="s" admin conversationId="c" />);
  await screen.findByText("第二最终回答");
  fireEvent.click(screen.getByRole("tab", { name: "第 1 轮" }));
  fireEvent.click(screen.getByText("实体层").closest("summary")!);
  await screen.findByText("对象已识别");
  fireEvent.click(screen.getByRole("tab", { name: "第 2 轮" }));
  await waitFor(() => expect(screen.getByText("实体层").closest("details")).not.toHaveAttribute("open"));
  fireEvent.click(screen.getByRole("tab", { name: "第 1 轮" }));
  await waitFor(() => expect(screen.getByText("实体层").closest("details")).toHaveAttribute("open"));
});

it("finds the same selected turn when a new question moves it onto an older page", async () => {
  const api = new ApiClient("management");
  let requests = 0;
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path.includes("/conversations/c?")) {
      requests++;
      if (path.endsWith("offset=25")) return { ...detail([turn("第1"), turn("第2")]), turn_count: 27, offset: 25 } as never;
      const first = requests === 1 ? 2 : 3;
      return { ...detail(Array.from({ length: 25 }, (_, i) => turn(`第${i + first}`))), turn_count: requests === 1 ? 26 : 27 } as never;
    }
    return { items: [], total: 0, offset: 0, limit: 25 } as never;
  });
  render(<RunTracePage api={api} workspace="s" admin conversationId="c" />);
  await screen.findByText("第26最终回答");
  fireEvent.click(screen.getByRole("tab", { name: "第 2 轮" }));
  expect(screen.getByText("第2最终回答")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "刷新" }));
  await waitFor(() => expect(get.mock.calls.some(([path]) => path.includes("/conversations/c?") && path.endsWith("offset=25"))).toBe(true));
  await waitFor(() => expect(screen.getByRole("button", { name: "刷新" })).not.toBeDisabled());
  expect(screen.getByRole("tab", { name: "第 2 轮" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("第2最终回答")).toBeVisible();
});

it("opens the recorded failing phase directly and never substitutes a step output for an absent answer", async () => {
  const { api } = client(detail([turn("异常", [{ layer: "planning", count: 1, status: "FAILED" }, { layer: "execution", count: 1, status: "RECORDED" }], [])]));
  render(<RunTracePage api={api} workspace="s" admin conversationId="c" />);
  const missing = await screen.findByText("本轮尚无已记录的最终答复。");
  expect(missing).toBeVisible();
  expect(within(missing.closest(".answer-block") as HTMLElement).queryByText("仅属于步骤的输出")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看策划层异常" }));
  await waitFor(() => expect(screen.getByText("策划层").closest("summary")).toHaveFocus());
  expect(screen.getByText("计划校验失败")).toBeVisible();
  expect(screen.getByRole("button", { name: "返回问答" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByText("执行结果").closest("summary")!);
  fireEvent.click(screen.getByText(/账户查询 · 已完成/).closest("summary")!);
  expect(screen.getByText("仅属于步骤的输出")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "返回问答" }));
  const answer = screen.getByText("本轮尚无已记录的最终答复。").closest(".answer-block") as HTMLElement;
  expect(within(answer).queryByText("仅属于步骤的输出")).toBeNull();
});

it("starts with the final conclusion and lets the reader expand supplementary approved fields", async () => {
  const { api } = client(detail([turn("汇总", [], [{ component: "text", data: "最终结论", components: [{ title: "获准明细", component: "detail", data: { balance: 100 } }] }])]));
  render(<RunTracePage api={api} workspace="s" admin conversationId="c" />);
  expect(await screen.findByText("最终结论")).toBeVisible();
  expect(screen.getByText("获准明细")).not.toBeVisible();
  fireEvent.click(screen.getByText("业务明细 · 1"));
  expect(screen.getByText("获准明细")).toBeVisible();
});
