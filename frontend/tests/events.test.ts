import { describe, expect, it } from "vitest";
import { eventCursor, latestRunEvents, mergeEvents } from "../shared/events";
import type { RuntimeEvent } from "../shared/types";
const event = (
  sequence: number,
  extra: Partial<RuntimeEvent> = {},
): RuntimeEvent => ({
  schema_version: "1",
  conversation_id: "conversation-a",
  run_id: "run-a",
  revision: 1,
  sequence,
  event_type: "STATUS",
  payload: { status: "ROUTING" },
  ...extra,
});
describe("cursor event recovery", () => {
  it("reconnect deduplicates overlapping events without losing ordering or replacing a received event", () => {
    const original = event(2, {
      event_type: "RESULT",
      payload: { data: "original" },
    });
    const result = mergeEvents(
      [event(1), original],
      [event(3), event(2, { payload: { data: "duplicate" } }), event(1)],
      "conversation-a",
    );
    expect(result.map((item) => item.sequence)).toEqual([1, 2, 3]);
    expect(result[1].payload.data).toBe("original");
    expect(eventCursor(result)).toBe(3);
  });
  it("never imports events from another conversation or unsupported protocol", () => {
    expect(
      mergeEvents(
        [],
        [
          event(1, { conversation_id: "conversation-b" }),
          event(2, { schema_version: "2" }),
          event(-1),
          event(4),
        ],
        "conversation-a",
      ),
    ).toEqual([event(4)]);
  });
  it("isolates question revisions and run attribution", () => {
    const current = event(3, { revision: 2, event_type: "QUESTION" });
    expect(
      latestRunEvents(
        [event(1), event(2, { run_id: "another" }), current],
        "run-a",
      ),
    ).toEqual([current]);
  });
});
