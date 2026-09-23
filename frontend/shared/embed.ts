export type EmbedMessage = {
  type: string;
  protocol_version: string;
  agent_id: string;
  instance_nonce: string;
  ticket?: string;
  runtime_session_id?: string;
};
export function trustedEmbedMessage(
  event: Pick<MessageEvent, "origin" | "source" | "data">,
  hostOrigin: string,
  parent: MessageEventSource | null,
  agentId: string,
  nonce: string,
): event is MessageEvent<EmbedMessage> {
  if (
    !hostOrigin ||
    hostOrigin === "null" ||
    !nonce ||
    event.origin !== hostOrigin ||
    event.source !== parent
  )
    return false;
  const value = event.data;
  return (
    !!value &&
    typeof value === "object" &&
    value.protocol_version === "1" &&
    value.agent_id === agentId &&
    value.instance_nonce === nonce &&
    ["AGENT_INIT", "HOST_LOGOUT"].includes(value.type) &&
    (value.type !== "AGENT_INIT" ||
      (typeof value.ticket === "string" && value.ticket.length > 0))
  );
}
export function embeddingContext(location: Location, referrer: string) {
  const isEmbed = location.pathname.startsWith("/embed/");
  let hostOrigin = "";
  try {
    hostOrigin = new URL(referrer).origin;
  } catch {
    /* No trusted host: fail closed. */
  }
  return { isEmbed, hostOrigin };
}
