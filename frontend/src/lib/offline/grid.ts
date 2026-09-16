"use client";

import type { StationTile } from "@/types/api";
import type { OutboxOp } from "./db";
import type { AppliedOp, OpenStart } from "./status";

/** A grid tile with the tablet's own unsent entries laid over it. */
export type LocalTile = StationTile & {
  /** Its state comes from an entry still on this tablet — no server cost yet. */
  pending?: boolean;
};

/**
 * Lay the outbox over the last grid the server sent.
 *
 * The tile flips the moment "Stopped" is tapped, online or not. Entries still
 * on the tablet always apply. Entries the server has accepted apply only until
 * the grid has been refetched after they landed — from then on the server's
 * figure already includes them, and adding them again would double a count.
 * Rejected entries never apply: they did not happen as far as the plant is
 * concerned.
 */
export function overlayGrid(
  tiles: StationTile[],
  ops: OutboxOp[],
  applied: AppliedOp[],
  gridUpdatedAt: number,
): LocalTile[] {
  const stillQueued = new Set(ops.map((op) => op.client_ref));
  const relevant = [
    ...applied
      .filter((entry) => entry.at > gridUpdatedAt && !stillQueued.has(entry.op.client_ref))
      .map((entry) => ({ op: entry.op, pending: false })),
    ...ops.filter((op) => op.status === "pending").map((op) => ({ op, pending: true })),
  ]
    .filter(({ op }) => op.kind !== "quality" && op.meta.station_id)
    .sort((a, b) => (a.op.created_at === b.op.created_at ? a.op.seq - b.op.seq : a.op.created_at < b.op.created_at ? -1 : 1));

  if (relevant.length === 0) return tiles;

  return tiles.map((tile) => {
    let next: LocalTile = tile;
    for (const { op, pending } of relevant) {
      if (op.meta.station_id !== tile.station_id) continue;
      if (op.kind === "downtime_start") {
        next = {
          ...next,
          status: "stopped",
          stopped_since: op.occurred_at,
          stopped_minutes: 0,
          live_cost_paise: 0,
          ticker_rate_per_minute: 0,
          reason_label: op.meta.reason_label ?? null,
          open_event_id: null,
          pending: pending || next.pending,
        };
      } else if (op.kind === "downtime_close") {
        next = {
          ...next,
          status: next.job_number ? "running" : "idle",
          stopped_since: null,
          stopped_minutes: 0,
          live_cost_paise: 0,
          reason_label: null,
          open_event_id: null,
          pending: pending || next.pending,
        };
      } else if (op.kind === "production") {
        next = { ...next, count: next.count + Number(op.payload.count ?? 0), pending: pending || next.pending };
      }
    }
    return next;
  });
}

/**
 * What a "Running" tap should close: the stop this tablet started (by client
 * id, which works before the server has it), or the server's open event.
 */
export function closeTarget(
  tile: LocalTile,
  ops: OutboxOp[],
  openStarts: Record<string, OpenStart>,
): { event_id: string } | { start_client_ref: string } | null {
  const entry = openStarts[tile.station_id];
  if (entry && ops.some((op) => op.client_ref === entry.client_ref)) return { start_client_ref: entry.client_ref };
  if (tile.open_event_id) return { event_id: tile.open_event_id };
  if (entry) return entry.event_id ? { event_id: entry.event_id } : { start_client_ref: entry.client_ref };
  return null;
}
