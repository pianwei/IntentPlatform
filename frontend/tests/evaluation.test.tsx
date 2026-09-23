import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EvaluationPage } from "../console/src/EvaluationPage";
import { ApiClient } from "../shared/api";
import type { Agent } from "../shared/types";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const agent: Agent = {
  id: "agent-a",
  workspace_id: "space-a",
  name: "用户选择的智能体",
  current_release_id: "release-a",
  suggested_questions: [],
  fixture_mode: true,
};
const cases = [
  { text: "用户录入的表达一", expected_capability_id: "cap-one" },
  { text: "用户录入的表达二", expected_capability_id: "cap-two" },
];
function page(writable = true) {
  return render(
    <EvaluationPage
      api={new ApiClient("management")}
      workspace="space-a"
      agents={[agent]}
      functions={[]}
      writable={writable}
      fixtureMode={true}
    />,
  );
}
function fill(raw = JSON.stringify(cases)) {
  fireEvent.change(screen.getByLabelText("评测标识"), {
    target: { value: "业务样本复核" },
  });
  fireEvent.change(screen.getByLabelText("用例 JSON"), {
    target: { value: raw },
  });
}
describe("user supplied routing diagnostic", () => {
  it("submits unchanged user cases to the scoped API and retains failed rows in the reported denominator", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "evaluation-a",
          label: "业务样本复核",
          total: 2,
          correct: 1,
          accuracy: 0.5,
          results: [
            { correct: true, actual: ["cap-one"] },
            { correct: false, error: "Routing diagnostic failed" },
          ],
          fixture_mode: true,
          release_id: "release-actual",
          acceptance_evidence: false,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    page();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "运行路由诊断" }));
    await screen.findByText("1 / 2");
    expect(screen.getByText("50.00%")).toBeVisible();
    expect(screen.getByText("Routing diagnostic failed")).toBeVisible();
    expect(screen.getByText("用户录入的表达二")).toBeVisible();
    expect(screen.getByText("非正式验收证据")).toBeVisible();
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v2/management/workspaces/space-a/evaluations",
    );
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      agent_id: "agent-a",
      label: "业务样本复核",
      cases,
    });
    fireEvent.change(screen.getByLabelText("用例 JSON"), {
      target: { value: "[]" },
    });
    expect(screen.getByText("用户录入的表达二")).toBeVisible();
  });
  it("does not invent expected IDs or submit malformed cases", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    page();
    fill('[{"text":"缺少金标"}]');
    fireEvent.click(screen.getByRole("button", { name: "运行路由诊断" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "expected_capability_id",
    );
    expect(fetch).not.toHaveBeenCalled();
  });
  it("imports and preserves JSON file content without executing the diagnostic until requested", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    page();
    const raw = JSON.stringify(cases, null, 2);
    const file = new File([raw], "business-cases.json", {
      type: "application/json",
    });
    Object.defineProperty(file, "text", { value: async () => raw });
    fireEvent.change(screen.getByLabelText("导入诊断用例 JSON"), {
      target: { files: [file] },
    });
    await waitFor(() =>
      expect(screen.getByLabelText("用例 JSON")).toHaveValue(raw),
    );
    expect(fetch).not.toHaveBeenCalled();
  });
  it("does not enable diagnostics for a reader identity", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    page(false);
    expect(screen.getByRole("button", { name: "运行路由诊断" })).toBeDisabled();
    expect(screen.getByLabelText("用例 JSON")).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("keeps submitted input and reports server failure without displaying successful metrics", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: "Publish agent first" }), {
            status: 409,
          }),
        ),
    );
    page();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "运行路由诊断" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent("Publish agent first");
    expect(screen.getByLabelText("用例 JSON")).toHaveValue(
      JSON.stringify(cases),
    );
    expect(screen.queryByText("实际正确 / 总数")).not.toBeInTheDocument();
  });
});
