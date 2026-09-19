"use client";

import { useEffect, useRef } from "react";
import { loadSession } from "./api";

/**
 * The live channel (§3 Layer 1).
 *
 * What genuinely needs to be live is narrower than it looks: downtime is
 * event-driven, so a stopped machine's rupee ticker is real-time; production
 * counts are hourly and only need to refresh when someone saves. So this is a
 * small event bus, not a streaming client.
 */

export type LiveEvent =
  | { type: "machine.stopped"; payload: Record<string, unknown> }
  | { type: "machine.started"; payload: Record<string, unknown> }
  | { type: "production.recorded"; payload: Record<string, unknown> }
  | { type: "quality.recorded"; payload: Record<string, unknown> }
  | { type: "escalation.fired"; payload: Record<string, unknown> }
  | { type: "action.created"; payload: Record<string, unknown> }
  | { type: "shift.closed"; payload: Record<string, unknown> }
  | {
      type: "ticker.tick";
      payload: {
        as_of: string;
        events: Array<{ event_id: string; station_id: string; minutes: number; cost: number }>;
      };
    };

const WS_BASE =
  process.env.NEXT_PUBLIC_WS_URL ??
  (typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`
    : "");

/** Plant Wi-Fi drops: a supervisor should never look at a dead screen for long. */
const LIVE_RETRY_CAP_MS = 15_000;
/**
 * A handshake that never opens is a different failure from a dropped one. The
 * usual cause is a token the server will not accept — it lasts a shift, and a
 * tablet left on the grid overnight outlives it. A browser cannot read the
 * server's close code on a rejected handshake, so it reads like any other
 * drop; retrying every fifteen seconds until someone picks the tablet up is
 * pointless traffic. Back these off much further instead.
 */
const LIVE_REJECTED_CAP_MS = 5 * 60_000;

export function useLiveChannel(onEvent: (event: LiveEvent) => void) {
  const handler = useRef(onEvent);
  // Assigned in an effect, not during render: a render React discards must not
  // leave the channel calling a handler that was never committed.
  useEffect(() => {
    handler.current = onEvent;
  });

  useEffect(() => {
    if (!WS_BASE) return;

    let socket: WebSocket | null = null;
    let keepalive: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let closed = false;

    const connect = () => {
      if (closed) return;
      // Read the session on every attempt. Captured once, a reconnect would
      // keep presenting a token the server has already refused, and a sign-out
      // would leave the loop running against a session that no longer exists.
      const session = loadSession();
      if (!session) return;

      let opened = false;
      socket = new WebSocket(`${WS_BASE}/ws/live?token=${session.access_token}`);

      socket.onopen = () => {
        opened = true;
        attempt = 0;
        // Reading is how the server notices a tablet that walked out of range.
        keepalive = setInterval(() => socket?.send("ping"), 25_000);
      };

      socket.onmessage = (message) => {
        try {
          handler.current(JSON.parse(message.data) as LiveEvent);
        } catch {
          /* malformed frame — ignore rather than tear down the channel */
        }
      };

      socket.onclose = () => {
        if (keepalive) clearInterval(keepalive);
        if (closed) return;
        attempt += 1;
        const cap = opened ? LIVE_RETRY_CAP_MS : LIVE_REJECTED_CAP_MS;
        retry = setTimeout(connect, Math.min(cap, 1000 * 2 ** Math.min(attempt, 9)));
      };
    };

    connect();

    return () => {
      closed = true;
      if (keepalive) clearInterval(keepalive);
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, []);
}
