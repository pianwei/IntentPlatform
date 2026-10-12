import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResultCard } from "../agent-web/src/ResultCard";
import type { Run, RuntimeEvent } from "../shared/types";
afterEach(cleanup);
const question: RuntimeEvent = {
  schema_version: "1",
  conversation_id: "c",
  run_id: "r",
  revision: 2,
  sequence: 5,
  event_type: "QUESTION",
  payload: {
    question_id: "q",
    fields: [{ name: "网点编号", type: "string", required: true }],
  },
};
const run: Run = {
  run_id: "r",
  conversation_id: "c",
  revision: 2,
  status: "WAITING_INPUT",
};
describe("structured business interaction", () => {
  it("submits fields bound to the original question and leaves optional values absent", () => {
    const onAction = vi.fn().mockResolvedValue(undefined);
    render(<ResultCard event={question} run={run} onAction={onAction} />);
    fireEvent.change(screen.getByLabelText("网点编号 *"), {
      target: { value: "B001" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交补充信息" }));
    expect(onAction).toHaveBeenCalledWith(question, "answers", {
      question_id: "q",
      values: { 网点编号: "B001" },
    });
  });
  it("disables old questions after a revision change and prevents cross-run confirmation", () => {
    const onAction = vi.fn();
    const { rerender } = render(
      <ResultCard
        event={question}
        run={{ ...run, revision: 3 }}
        onAction={onAction}
      />,
    );
    expect(screen.getByRole("button", { name: "提交补充信息" })).toBeDisabled();
    const confirmation = {
      ...question,
      event_type: "CONFIRMATION" as const,
      payload: { name: "申请办理", arguments: { record_id: "1" } },
    };
    rerender(
      <ResultCard
        event={confirmation}
        run={{ ...run, run_id: "other-run", status: "WAITING_CONFIRMATION" }}
        onAction={onAction}
      />,
    );
    expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled();
    expect(onAction).not.toHaveBeenCalled();
  });
  it("does not execute a write until the user explicitly confirms and attributes the decision", () => {
    const onAction = vi.fn().mockResolvedValue(undefined);
    const confirmation = {
      ...question,
      event_type: "CONFIRMATION" as const,
      payload: { name: "申请办理", arguments: { record_id: "1" } },
    };
    render(
      <ResultCard
        event={confirmation}
        run={{ ...run, status: "WAITING_CONFIRMATION" }}
        onAction={onAction}
      />,
    );
    expect(onAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));
    expect(onAction).toHaveBeenCalledOnce();
    expect(onAction).toHaveBeenCalledWith(confirmation, "confirmations", {
      confirmed: true,
    });
  });
  it("renders approved fields as text and hides internal provenance", () => {
    const result = {
      ...question,
      event_type: "RESULT" as const,
      payload: {
        status: "SUCCEEDED",
        component: "detail",
        fields: [
          { label: "办理网点", value: "<script>window.pwned=true</script>" },
        ],
        provenance: { capability_id: "cap-1", version: 2 },
        fixture_mode: true,
      },
    };
    const { container } = render(
      <ResultCard event={result} run={null} onAction={vi.fn()} />,
    );
    expect(
      screen.getByText("<script>window.pwned=true</script>"),
    ).toBeVisible();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.queryByText(/cap-1/)).not.toBeInTheDocument();
  });
  it("shows confirmed write success without rendering an invalid returned value or download", () => {
    render(<ResultCard event={{ ...question, event_type: "RESULT", payload: { status: "SUCCEEDED", component: "file", title: "办理结果", result_unavailable: true, data: {}, fields: [], files: [{ id: "unexpected", filename: "raw.pdf", media_type: "application/pdf", size: 1 }] } }} run={null} onAction={vi.fn()} />);
    expect(screen.getByText(/业务操作已确认成功，但返回内容未通过/)).toBeVisible();
    expect(screen.queryByText("raw.pdf")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "下载" })).not.toBeInTheDocument();
  });
  it("shows a named collection from a published table result as readable rows", () => {
    render(<ResultCard event={{ ...question, event_type: "RESULT", payload: {
      status: "SUCCEEDED", component: "table", title: "查询客户账户列表",
      data: { accounts: [{ account_id: "A001", currency: "CNY" }, { account_id: "A002", currency: "CNY" }] },
    } }} run={null} onAction={vi.fn()} />);
    expect(screen.getByRole("columnheader", { name: "账户编号" })).toBeVisible();
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(screen.getByText("A002")).toBeVisible();
    expect(screen.queryByText(/\[{"account_id"/)).not.toBeInTheDocument();
  });
  it("renders final summary, table and approved detail fields without restoring intermediate steps", () => {
    render(<ResultCard event={{ ...question, event_type: "RESULT", payload: {
      title: "最终回答", status: "SUCCEEDED", component: "text", data: "已完成账户查询。",
      components: [
        { title: "账户余额", component: "table", data: { accounts: [{ account_id: "A001", balance: 120 }] }, fields: [], files: [], step_id: "private-step", input: { secret: "private-input" } },
        { title: "核查结论", component: "detail", data: { hidden: "not-a-displayed-field" }, fields: [{ label: "状态", value: "正常" }], files: [] },
      ],
    } }} run={null} onAction={vi.fn()} />);
    expect(screen.getByText("已完成账户查询。")).toBeVisible();
    expect(screen.queryByText(/查看业务明细/)).toBeNull();
    expect(screen.getByRole("heading", { name: "账户余额" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "账户编号" })).toBeVisible();
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByText("120")).toBeVisible(); expect(screen.getByText("正常")).toBeVisible();
    expect(screen.queryByText(/private-step|private-input|not-a-displayed-field/)).toBeNull();
  });
  it("uses safe Markdown for final citations and treats table content as inert text", () => {
    const { container } = render(<ResultCard event={{ ...question, event_type: "RESULT", payload: {
      component: "text", data: "已查询资料", components: [
        { title: "业务依据", component: "citation", data: '[说明](https://bank.internal/rules) [恶意链接](javascript:alert(1))\n<img src=x onerror=alert(1)>\n![远程图片](https://outside.example/pixel)', fields: [], files: [] },
        { title: "安全展示", component: "table", data: [{ name: "<script>window.pwned=true</script>" }], fields: [], files: [] },
      ],
    } }} run={null} onAction={vi.fn()} />);
    expect(screen.getByText("已查询资料")).toBeVisible();
    expect(screen.queryByText(/查看业务明细/)).toBeNull();
    expect(screen.getByRole("link", { name: "说明" })).toHaveAttribute("href", "https://bank.internal/rules");
    expect(screen.getByRole("link", { name: "说明" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: "恶意链接" })).toBeNull();
    expect(container.querySelector("img, script, iframe")).toBeNull();
    expect(screen.getByText("<script>window.pwned=true</script>")).toBeVisible();
  });
  it("presents a funds overview's approved balances as a table and nested summaries as business fields", () => {
    render(<ResultCard event={{ ...question, event_type: "RESULT", payload: {
      component: "text", data: "已完成资金概览。", components: [
        { title: "客户资金概览", component: "detail", data: { internal: "private-raw-response" }, fields: [
          { label: "balances", value: [
            { account_id: "A001", balance: 1500, available: 1200, currency: "CNY" },
            { account_id: "A002", balance: 2300, available: 2300, currency: "CNY" },
          ] },
          { label: "summary", value: { total_balance: 3800, detail: { status: "正常" } } },
          { label: "schedules", value: [{ loan_id: "L001", due_on: "2026-10-01", amount: 300 }] },
        ], files: [] },
      ],
    } }} run={null} onAction={vi.fn()} />);
    expect(screen.getByText("已完成资金概览。")).toBeVisible();
    expect(screen.queryByText(/查看业务明细/)).toBeNull();
    expect(screen.getAllByRole("table")).toHaveLength(2);
    for (const column of ["账户编号", "余额", "可用余额", "币种", "合同编号", "到期日", "金额"]) {
      expect(screen.getByRole("columnheader", { name: column })).toBeVisible();
    }
    for (const text of ["账户余额", "汇总", "详情", "还款安排", "总余额", "A001", "A002", "3800", "正常", "L001"]) {
      expect(screen.getByText(text)).toBeVisible();
    }
    expect(screen.queryByText(/private-raw-response|\[{"account_id"|^balances$|^summary$|^schedules$/)).toBeNull();
  });
  it("limits nested result rows, fields and depth without falling back to JSON", () => {
    const rows = Array.from({ length: 51 }, (_, index) => ({ account_id: `visible-account-${index}` }));
    render(<ResultCard event={{ ...question, event_type: "RESULT", payload: {
      component: "detail", fields: [
        { label: "balances", value: rows },
        { label: "summary", value: { detail: { details: { secret: "too-deep-value" } } } },
        { label: "明细字段", value: Object.fromEntries(Array.from({ length: 51 }, (_, index) => [`field-${index}`, `field-value-${index}`])) },
      ],
    } }} run={null} onAction={vi.fn()} />);
    expect(screen.getAllByRole("row")).toHaveLength(51);
    expect(screen.getByText("visible-account-49")).toBeVisible();
    expect(screen.queryByText("visible-account-50")).toBeNull();
    expect(screen.getByText("共 51 条记录，仅展示前 50 条。")).toBeVisible();
    expect(screen.getByText("内容层级较深，未展开。")).toBeVisible();
    expect(screen.getByText("字段较多，仅展示前 50 项。")).toBeVisible();
    expect(screen.queryByText(/too-deep-value|field-value-50|{"details"/)).toBeNull();
  });
  it("keeps an explicitly empty detail allowlist empty and treats nested values as inert text", () => {
    const { container } = render(<ResultCard event={{ ...question, event_type: "RESULT", payload: {
      component: "text", data: "完成", components: [
        { title: "已批准结果", component: "detail", data: { balances: [{ account_id: "sensitive-unapproved-account" }] }, fields: [], files: [] },
        { title: "获准备注", component: "detail", fields: [{ label: "detail", value: { description: "<img src=x onerror=alert(1)>", history: ["[点击](javascript:alert(1))"] } }], files: [] },
      ],
    } }} run={null} onAction={vi.fn()} />);
    expect(screen.getByText("完成")).toBeVisible();
    expect(screen.queryByText(/查看业务明细/)).toBeNull();
    expect(screen.queryByText(/sensitive-unapproved-account/)).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(screen.getByText("[点击](javascript:alert(1))")).toBeVisible();
    expect(container.querySelector("img, script, a")).toBeNull();
  });
  it("ignores nested files, unsupported components and component diagnostics", async () => {
    const download = vi.fn().mockResolvedValue(undefined);
    render(<ResultCard event={{ ...question, event_type: "RESULT", payload: {
      component: "text", data: "最终内容", files: [{ id: "allowed", filename: "最终文件.csv", media_type: "text/csv", size: 5 }],
      components: [
        { title: "业务说明", component: "text", data: "允许展示的说明", fields: [], files: [{ id: "not-allowed", filename: "嵌套文件.txt" }], components: [{ title: "嵌套步骤", component: "text", data: "内部步骤" }] },
        { title: "模型诊断", component: "trace", data: "内部规划" },
        { title: "不可用结构", component: "detail", data: "内部字段", fields: [null] },
      ],
    } }} run={null} onAction={vi.fn()} onDownload={download} />);
    expect(screen.getByText("最终内容")).toBeVisible();
    expect(screen.queryByText(/查看业务明细/)).toBeNull();
    expect(screen.getByText("允许展示的说明")).toBeVisible();
    expect(screen.queryByText(/嵌套文件|嵌套步骤|内部步骤|模型诊断|内部规划|不可用结构|内部字段/)).toBeNull();
    expect(screen.getAllByRole("button", { name: "下载" })).toHaveLength(1);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "下载" })); });
    expect(download).toHaveBeenCalledWith({ id: "allowed", filename: "最终文件.csv", media_type: "text/csv", size: 5 });
  });
  it("hides all final components when the output contract marks the result unavailable", () => {
    render(<ResultCard event={{ ...question, event_type: "RESULT", payload: {
      component: "text", status: "SUCCEEDED", result_unavailable: true, data: "invalid-sensitive-summary",
      components: [{ title: "invalid-sensitive-title", component: "table", data: [{ account_id: "invalid-sensitive-account" }], fields: [], files: [] }],
    } }} run={null} onAction={vi.fn()} />);
    expect(screen.getByText(/业务操作已确认成功/)).toBeVisible();
    expect(screen.queryByText(/invalid-sensitive/)).toBeNull(); expect(screen.queryByRole("table")).toBeNull();
  });
});
