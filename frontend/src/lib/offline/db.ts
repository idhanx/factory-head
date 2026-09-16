"use client";

import type { SyncOpKind } from "@/types/api";

/**
 * The tablet's own copy of entries not yet accepted by the server.
 *
 * IndexedDB rather than localStorage: it survives a reload and a browser
 * restart, it is not capped at a few megabytes, and a write is transactional —
 * a half-saved stop is worse than none. The wrapper is deliberately tiny: two
 * stores, promise helpers, nothing clever.
 *
 * If IndexedDB is unavailable (some private windows refuse it) the same calls
 * fall back to memory, so capture still works — it just will not survive a
 * reload, which is the most any browser in that mode allows.
 */

export type OutboxStatus = "pending" | "rejected";

export interface OutboxOp {
  /** Made on the device. The API dedupes on it, so a retry never doubles a stop. */
  client_ref: string;
  kind: SyncOpKind;
  shift_session_id: string | null;
  /** When it happened on the floor — not when it reached the server. */
  occurred_at: string;
  payload: Record<string, unknown>;
  status: OutboxStatus;
  message?: string;
  attempts: number;
  created_at: string;
  /** Tie-break for two entries made in the same millisecond. */
  seq: number;
  /** Local only, never sent: what the sync list shows, and whose entry it is. */
  meta: OutboxMeta;
}

export interface OutboxMeta {
  user_id: string;
  user_name: string;
  plant_id: string;
  station_id?: string;
  station_code?: string;
  part_number?: string;
  reason_label?: string;
  /** One line for the sync list, e.g. "12 good · 1 rejected". */
  summary?: string;
}

/** An entry the server kept for owner review because its shift was already locked. */
export interface HeldLocal {
  client_ref: string;
  kind: SyncOpKind;
  occurred_at: string;
  received_at: string;
  message: string | null;
  meta: OutboxMeta;
}

const DB_NAME = "fm-offline";
const DB_VERSION = 1;
export const OUTBOX = "outbox";
export const HELD = "held";
type StoreName = typeof OUTBOX | typeof HELD;

let opening: Promise<IDBDatabase | null> | null = null;
const memory: Record<StoreName, Map<string, unknown>> = { outbox: new Map(), held: new Map() };

function open(): Promise<IDBDatabase | null> {
  if (opening) return opening;
  opening = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") return resolve(null);
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(DB_NAME, DB_VERSION);
    } catch {
      return resolve(null);
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(OUTBOX)) {
        const store = db.createObjectStore(OUTBOX, { keyPath: "client_ref" });
        store.createIndex("created", "created_at");
      }
      if (!db.objectStoreNames.contains(HELD)) {
        const store = db.createObjectStore(HELD, { keyPath: "client_ref" });
        store.createIndex("received", "received_at");
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
  return opening;
}

function done<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function committed(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
  });
}

export async function putAll<T extends { client_ref: string }>(store: StoreName, rows: T[]): Promise<void> {
  const db = await open();
  if (!db) {
    for (const row of rows) memory[store].set(row.client_ref, row);
    return;
  }
  const tx = db.transaction(store, "readwrite");
  for (const row of rows) tx.objectStore(store).put(row);
  await committed(tx);
}

export const put = <T extends { client_ref: string }>(store: StoreName, row: T) => putAll(store, [row]);

export async function removeAll(store: StoreName, refs: string[]): Promise<void> {
  const db = await open();
  if (!db) {
    for (const ref of refs) memory[store].delete(ref);
    return;
  }
  const tx = db.transaction(store, "readwrite");
  for (const ref of refs) tx.objectStore(store).delete(ref);
  await committed(tx);
}

export const remove = (store: StoreName, ref: string) => removeAll(store, [ref]);

/** Every row, oldest first by the store's time index. */
export async function all<T>(store: StoreName): Promise<T[]> {
  const db = await open();
  if (!db) return [...memory[store].values()] as T[];
  const tx = db.transaction(store, "readonly");
  const index = tx.objectStore(store).index(store === OUTBOX ? "created" : "received");
  return (await done(index.getAll())) as T[];
}

export async function get<T>(store: StoreName, ref: string): Promise<T | undefined> {
  const db = await open();
  if (!db) return memory[store].get(ref) as T | undefined;
  const tx = db.transaction(store, "readonly");
  return (await done(tx.objectStore(store).get(ref))) as T | undefined;
}
