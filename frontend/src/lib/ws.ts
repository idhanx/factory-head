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

export function useLiveChannel(onEvent: (event: LiveEvent) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    const session = loadSession();
    if (!session || !WS_BASE) return;

    let socket: WebSocket | null = null;
    let keepalive: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let closed = false;

    const connect = () => {
      if (closed) return;
      socket = new WebSocket(`${WS_BASE}/ws/live?token=${session.access_token}`);

      socket.onopen = () => {
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
        // Plant Wi-Fi drops. Back off, but cap it: a supervisor should never
        // be looking at a dead screen for more than ~15 seconds.
        attempt += 1;
        const delay = Math.min(15_000, 1000 * 2 ** Math.min(attempt, 4));
        retry = setTimeout(connect, delay);
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
