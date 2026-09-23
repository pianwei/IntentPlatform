import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BindingSummary, ResultCard } from "../agent-web/src/ResultCard";
import type { Run, RuntimeEvent } from "../shared/types";
afterEach(cleanup);
const run: Run = { run_id: "r", conversation_id: "c", revision: 2, status: "WAITING_INPUT" };
const event: RuntimeEvent = { schema_version: "1", conversation_id: "c", run_id: "r", revision: 2, sequence: 4, event_type: "QUESTION", payload: { question_id: "q" } };
it("submits labelled quantity unchanged and no client unit conversion or extra unit field", () => {
  const onAction = vi.fn().mockResolvedValue(undefined); const question = { ...event, payload: { ...event.payload, kind: "arguments", fields: [{ name: "amount", label: "办理金额", unit: "wan_yuan", type: "number", required: true }] } };
  render(<ResultCard event={question} run={run} onAction={onAction} />);
  fireEvent.change(screen.getByLabelText("办理金额（wan_yuan） *"), { target: { value: "1.5" } }); fireEvent.click(screen.getByRole("button", { name: "提交补充信息" }));
  expect(onAction).toHaveBeenCalledWith(question, "answers", { question_id: "q", values: { amount: 1.5 } });
});
it.each(["entity_choice", "history_choice"])("submits opaque option_id for %s without extracting a label as entity ID", kind => {
  const onAction = vi.fn().mockResolvedValue(undefined); const question = { ...event, payload: { ...event.payload, kind, options: [{ id: "opaque-token", label: "客户甲 · 尾号 01" }] } };
  const { rerender } = render(<ResultCard event={question} run={run} onAction={onAction} />); fireEvent.click(screen.getByRole("button", { name: "客户甲 · 尾号 01" }));
  expect(onAction).toHaveBeenCalledWith(question, "answers", { question_id: "q", option_id: "opaque-token" });
  rerender(<ResultCard event={question} run={{ ...run, revision: 3 }} onAction={onAction} />); expect(screen.getByRole("button", { name: "客户甲 · 尾号 01" })).toBeDisabled();
});
it("renders only nonsecret provenance fields even if a future response contains extra entity data", () => {
  render(<BindingSummary bindings={[{ parameter: "customer_id", role: "customer", source: "history", resolver_ref: "approved", source_call_id: "call-1", entity_id: "secret-business-id", credential: "secret-key" }]} />);
  expect(screen.getByText(/同会话历史结果/)).toBeInTheDocument(); expect(screen.queryByText(/secret-business-id|secret-key/)).not.toBeInTheDocument();
});
