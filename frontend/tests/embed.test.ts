import { describe, expect, it } from "vitest";
import { trustedEmbedMessage } from "../shared/embed";
describe("iframe identity boundary", () => {
  const parent = {} as Window;
  const origin = "https://host.internal";
  const data = {
    type: "AGENT_INIT",
    protocol_version: "1",
    agent_id: "agent-a",
    instance_nonce: "nonce-a",
    ticket: "one-time-ticket",
  };
  const trusted = { origin, source: parent, data };
  it("accepts only an exact host, window, agent, protocol and nonce match", () => {
    expect(
      trustedEmbedMessage(trusted, origin, parent, "agent-a", "nonce-a"),
    ).toBe(true);
    for (const candidate of [
      { ...trusted, origin: "https://evil.internal" },
      { ...trusted, source: {} as Window },
      { ...trusted, data: { ...data, instance_nonce: "old-nonce" } },
      { ...trusted, data: { ...data, agent_id: "another-agent" } },
      { ...trusted, data: { ...data, protocol_version: "2" } },
      { ...trusted, data: { ...data, ticket: "" } },
    ])
      expect(
        trustedEmbedMessage(candidate, origin, parent, "agent-a", "nonce-a"),
      ).toBe(false);
  });
  it("requires the same envelope for host logout and rejects absent referrer origins", () => {
    expect(
      trustedEmbedMessage(
        { ...trusted, data: { ...data, type: "HOST_LOGOUT" } },
        origin,
        parent,
        "agent-a",
        "nonce-a",
      ),
    ).toBe(true);
    expect(trustedEmbedMessage(trusted, "", parent, "agent-a", "nonce-a")).toBe(
      false,
    );
    expect(
      trustedEmbedMessage(trusted, "null", parent, "agent-a", "nonce-a"),
    ).toBe(false);
  });
});
