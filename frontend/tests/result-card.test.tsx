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
  it("renders returned fields and provenance as text rather than executing markup", () => {
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
    expect(screen.getByText(/cap-1/)).toBeVisible();
  });
});
