import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
});
