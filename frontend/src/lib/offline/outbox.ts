"use client";

import { ApiError, api, loadSession } from "@/lib/api";
import type { SyncOpIn, SyncOpKind, SyncResult } from "@/types/api";
import * as db from "./db";
import type { HeldLocal, OutboxMeta, OutboxOp } from "./db";
import {
  markSynced, recordResults, reportRequest, setHeld, setNetworkOnline, setOpenStarts, setOps, setSyncing,
  useSync,
} from "./status";

/**
 * The outbox: every floor entry is written to the tablet first, then sent.
 *
 * Capture never waits on the network. A stop is on the tablet the instant it
 * is tapped, with the time it happened; the server gets it now if it can, or
 * whenever the Wi-Fi comes back. Each entry carries a client id the API
 * dedupes on, so a retry after a timeout can never log the same stop twice.
 *
 * Outcomes, per entry:
 *   applied / duplicate  → gone from the tablet; it is on the server.
 *   held                 → gone, but listed: its shift was locked before it
 *                          arrived, and the owner decides (apply or discard).
 *   rejected             → kept, with the server's reason, until someone
 *                          discards it. It will never succeed as it is.
 */

const CHUNK = 50;
const BACKOFF_START_MS = 5_000;
const BACKOFF_CAP_MS = 60_000;
const INTERVAL_MS = 30_000;
const HELD_LIMIT = 30;
const DEVICE_KEY = "fm.device";

// ---------------------------------------------------------------------------
// Ids
// ---------------------------------------------------------------------------

/**
 * crypto.randomUUID exists only in a secure context. A tablet opening the app
 * as http://192.168.x.x on the plant LAN is not one, but getRandomValues works
 * everywhere, so the fallback is still a proper random v4 id.
 */
export function uuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Stable per browser profile, so the server can tell which tablet sent what. */
export function deviceId(): string {
  try {
    const existing = window.localStorage.getItem(DEVICE_KEY);
    if (existing) return existing;
    const id = uuid();
    window.localStorage.setItem(DEVICE_KEY, id);
    return id;
  } catch {
    return "unknown-device";
  }
}

// ---------------------------------------------------------------------------
// Store refresh, shared across tabs
// ---------------------------------------------------------------------------

const channel =
  typeof window !== "undefined" && "BroadcastChannel" in window ? new BroadcastChannel("fm-outbox") : null;

let lastSeq = 0;

const byCreation = (a: OutboxOp, b: OutboxOp) =>
  a.created_at === b.created_at ? a.seq - b.seq : a.created_at < b.created_at ? -1 : 1;

/** Reload the store from IndexedDB. */
export async function refresh(): Promise<void> {
  const [ops, held] = await Promise.all([db.all<OutboxOp>(db.OUTBOX), db.all<HeldLocal>(db.HELD)]);
  ops.sort(byCreation);
  lastSeq = Math.max(lastSeq, ...ops.map((op) => op.seq));
  setOps(ops);
  setHeld(held.reverse());
}

async function changed() {
  await refresh();
  channel?.postMessage("changed");
}

// ---------------------------------------------------------------------------
// Enqueue
// ---------------------------------------------------------------------------

export interface NewOp {
  kind: SyncOpKind;
  payload: Record<string, unknown>;
  shift_session_id: string | null;
  meta?: Omit<OutboxMeta, "user_id" | "user_name" | "plant_id">;
}

/** Keep the entry on this tablet, then try to send it. Resolves once it is saved locally. */
export async function enqueue(input: NewOp): Promise<OutboxOp> {
  const session = loadSession();
  if (!session) throw new Error("Your session has ended. Log in again to record this.");

  const now = new Date();
  lastSeq = Math.max(lastSeq + 1, now.getTime());
  const op: OutboxOp = {
    client_ref: uuid(),
    kind: input.kind,
    shift_session_id: input.shift_session_id,
    occurred_at: now.toISOString(),
    payload: input.payload,
    status: "pending",
    attempts: 0,
    created_at: now.toISOString(),
    seq: lastSeq,
    meta: { ...input.meta, user_id: session.user_id, user_name: session.full_name, plant_id: session.plant_id },
  };

  await db.put(db.OUTBOX, op);
  trackOpenStart(op);
  await changed();
  void flush({ force: true });
  return op;
}

/**
 * Remember a stop started on this tablet until it is closed here, so "Running"
 * can reference it by client id before (or without) the server's event id.
 */
function trackOpenStart(op: OutboxOp) {
  const stationId = op.meta.station_id;
  if (!stationId) return;
  const { openStarts } = useSync.getState();
  if (op.kind === "downtime_start") {
    setOpenStarts({ ...openStarts, [stationId]: { client_ref: op.client_ref, event_id: null } });
  } else if (op.kind === "downtime_close" && openStarts[stationId]) {
    const next = { ...openStarts };
    delete next[stationId];
    setOpenStarts(next);
  }
}

// ---------------------------------------------------------------------------
// Rejected entries: fix-and-retry, or discard
// ---------------------------------------------------------------------------

export async function retryRejected(clientRef: string): Promise<void> {
  const op = await db.get<OutboxOp>(db.OUTBOX, clientRef);
  if (!op) return;
  await db.put(db.OUTBOX, { ...op, status: "pending", message: undefined });
  await changed();
  await flush({ force: true });
}

export async function discard(clientRef: string): Promise<void> {
  const op = await db.get<OutboxOp>(db.OUTBOX, clientRef);
  await db.remove(db.OUTBOX, clientRef);
  if (op?.kind === "downtime_start" && op.meta.station_id) {
    const { openStarts } = useSync.getState();
    if (openStarts[op.meta.station_id]?.client_ref === clientRef) {
      const next = { ...openStarts };
      delete next[op.meta.station_id];
      setOpenStarts(next);
    }
  }
  await changed();
}

export async function dismissHeld(clientRef: string): Promise<void> {
  await db.remove(db.HELD, clientRef);
  await changed();
}

// ---------------------------------------------------------------------------
// Flush
// ---------------------------------------------------------------------------

let inflight: Promise<void> | null = null;
let again = false;
let failures = 0;
let backoffUntil = 0;
let retryTimer: ReturnType<typeof setTimeout> | null = null;

function toWire(op: OutboxOp): SyncOpIn {
  return {
    client_ref: op.client_ref,
    kind: op.kind,
    ...(op.shift_session_id ? { shift_session_id: op.shift_session_id } : {}),
    occurred_at: op.occurred_at,
    payload: op.payload,
  };
}

/**
 * Transient: nothing answered, the API is down or busy, or it does not have the
 * sync endpoint yet (404/405 — an older server mid-upgrade). Keep the entry and
 * try later; marking a whole day's stops "rejected" over a deploy would be wrong.
 */
function transient(e: unknown): boolean {
  if (!(e instanceof ApiError)) return true;
  return e.status === 0 || e.status >= 500 || [404, 405, 408, 429].includes(e.status);
}

function scheduleRetry() {
  failures += 1;
  const delay = Math.min(BACKOFF_CAP_MS, BACKOFF_START_MS * 2 ** (failures - 1));
  backoffUntil = Date.now() + delay;
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = setTimeout(() => void flush(), delay);
}

/**
 * Send pending entries, oldest first, in chunks of 50. Only one flush runs at a
 * time; a request for another while one is running is folded into a single
 * follow-up pass. `force` skips the backoff (a new entry, the network coming
 * back, "Retry now").
 */
export function flush({ force = false }: { force?: boolean } = {}): Promise<void> {
  if (inflight) {
    if (force) again = true;
    return inflight;
  }
  if (!force && Date.now() < backoffUntil) return Promise.resolve();
  if (force) backoffUntil = 0;

  inflight = run()
    .catch(() => undefined)
    .finally(() => {
      inflight = null;
      if (again) {
        again = false;
        void flush({ force: true });
      }
    });
  return inflight;
}

async function run(): Promise<void> {
  const session = loadSession();
  if (!session) return;
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    setNetworkOnline(false);
    return;
  }

  const stored = await db.all<OutboxOp>(db.OUTBOX);
  // Only this login's entries: submitting someone else's stop under your name
  // would put the wrong person in the audit trail. Theirs wait for them.
  const pending = stored
    .filter(
      (op) => op.status === "pending" && op.meta.user_id === session.user_id && op.meta.plant_id === session.plant_id,
    )
    .sort(byCreation);

  if (pending.length === 0) {
    if (stored.every((op) => op.status !== "pending")) markSynced();
    return;
  }

  setSyncing(true);
  const device = deviceId();
  let clean = true;

  try {
    for (let i = 0; i < pending.length; i += CHUNK) {
      const chunk = pending.slice(i, i + CHUNK);
      let results: SyncResult[];
      try {
        results = (await api.syncBatch(device, chunk.map(toWire))).results;
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return; // request() is already sending them to login
        if (transient(e)) {
          await db.putAll(db.OUTBOX, chunk.map((op) => ({ ...op, attempts: op.attempts + 1 })));
          scheduleRetry();
          clean = false;
          break;
        }
        // The whole batch was refused (a malformed entry, a role with no
        // capture rights). Retrying will not change that — show why.
        const message = e instanceof Error ? e.message : "The server refused these entries.";
        await db.putAll(
          db.OUTBOX,
          chunk.map((op) => ({ ...op, status: "rejected" as const, message, attempts: op.attempts + 1 })),
        );
        clean = false;
        continue;
      }

      failures = 0;
      backoffUntil = 0;
      reportRequest(true);
      await settle(chunk, results);
    }
  } finally {
    await changed();
    setSyncing(false);
  }

  if (clean) markSynced();
}

async function settle(chunk: OutboxOp[], results: SyncResult[]) {
  const byRef = new Map(chunk.map((op) => [op.client_ref, op]));
  const gone: string[] = [];
  const applied: OutboxOp[] = [];
  const held: HeldLocal[] = [];
  const rejected: OutboxOp[] = [];
  let openStarts = useSync.getState().openStarts;

  for (const result of results) {
    const op = byRef.get(result.client_ref);
    if (!op) continue;
    if (result.status === "applied" || result.status === "duplicate") {
      gone.push(op.client_ref);
      applied.push(op);
      const stationId = op.meta.station_id;
      if (op.kind === "downtime_start" && stationId && openStarts[stationId]?.client_ref === op.client_ref) {
        openStarts = { ...openStarts, [stationId]: { client_ref: op.client_ref, event_id: result.entity_id } };
      }
    } else if (result.status === "held") {
      gone.push(op.client_ref);
      held.push({
        client_ref: op.client_ref,
        kind: op.kind,
        occurred_at: op.occurred_at,
        received_at: new Date().toISOString(),
        message: result.message,
        meta: op.meta,
      });
    } else {
      rejected.push({ ...op, status: "rejected", message: result.message ?? "Not accepted.", attempts: op.attempts + 1 });
    }
  }

  if (rejected.length) await db.putAll(db.OUTBOX, rejected);
  if (gone.length) await db.removeAll(db.OUTBOX, gone);
  if (held.length) {
    await db.putAll(db.HELD, held);
    const all = await db.all<HeldLocal>(db.HELD);
    if (all.length > HELD_LIMIT) await db.removeAll(db.HELD, all.slice(0, all.length - HELD_LIMIT).map((h) => h.client_ref));
  }
  setOpenStarts(openStarts);
  // Store first, then results: for a moment the grid overlay would otherwise
  // see the same count both as still-pending and as just-applied.
  await refresh();
  recordResults(results, applied);
}

// ---------------------------------------------------------------------------
// Triggers
// ---------------------------------------------------------------------------

let started = 0;
let stopAgent: (() => void) | null = null;

/**
 * Start the background triggers: the network coming back, the window getting
 * focus, every 30 seconds, and another tab changing the outbox. Idempotent and
 * reference-counted, so a remount does not double the timers.
 */
export function startSyncAgent(): () => void {
  started += 1;
  if (started === 1) {
    const onOnline = () => {
      setNetworkOnline(true);
      void flush({ force: true });
    };
    const onOffline = () => setNetworkOnline(false);
    const onFocus = () => void flush();
    const onMessage = () => void refresh();
    const interval = setInterval(() => void flush(), INTERVAL_MS);

    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    window.addEventListener("focus", onFocus);
    channel?.addEventListener("message", onMessage);
    setNetworkOnline(navigator.onLine);
    void refresh().then(() => flush({ force: true }));

    stopAgent = () => {
      clearInterval(interval);
      if (retryTimer) clearTimeout(retryTimer);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("focus", onFocus);
      channel?.removeEventListener("message", onMessage);
    };
  }
  return () => {
    started -= 1;
    if (started === 0) stopAgent?.();
  };
}
