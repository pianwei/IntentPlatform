import type { RuntimeEvent } from "./types";
export function mergeEvents(
  previous: RuntimeEvent[],
  incoming: RuntimeEvent[],
  conversationId: string,
): RuntimeEvent[] {
  const events = new Map<number, RuntimeEvent>();
  for (const event of [...previous, ...incoming]) {
    if (
      event.conversation_id !== conversationId ||
      event.schema_version !== "1" ||
      !Number.isSafeInteger(event.sequence) ||
      event.sequence < 1
    )
      continue;
    if (!events.has(event.sequence)) events.set(event.sequence, event);
  }
  return [...events.values()].sort((a, b) => a.sequence - b.sequence);
}
export function eventCursor(events: RuntimeEvent[]) {
  return events.length ? events[events.length - 1].sequence : 0;
}
export function latestRunEvents(events: RuntimeEvent[], runId: string) {
  const related = events.filter((event) => event.run_id === runId);
  const revision = Math.max(0, ...related.map((event) => event.revision));
  return related.filter((event) => event.revision === revision);
}
