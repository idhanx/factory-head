"use client";

import { create } from "zustand";
import type { SyncResult } from "@/types/api";
import type { HeldLocal, OutboxOp } from "./db";

/**
 * What the header pill, the sync sheet and the capture screens know about the
 * outbox. The outbox module writes here; components only read.
 *
 * "Online" is two facts together: the browser thinks it has a network, and
 * the last request to the API actually got an answer. Plant Wi-Fi is often
 * connected to an access point that reaches nothing — navigator.onLine alone
 * would call that online.
 */

export interface AppliedOp {
  op: OutboxOp;
  /** Epoch ms. The grid overlay drops the op once the grid is newer than this. */
  at: number;
}

/** A stop started on this tablet that the tablet has not yet seen closed. */
export interface OpenStart {
  client_ref: string;
  /** Filled once the server applied the start. */
  event_id: string | null;
}

interface SyncState {
  networkOnline: boolean;
  lastRequestOk: boolean;
  online: boolean;
  ops: OutboxOp[];
  held: HeldLocal[];
  /** Recent outcomes by client_ref, for screens that show "Synced" on their own entry. */
  results: Record<string, SyncResult>;
  applied: AppliedOp[];
  pendingCount: number;
  rejectedCount: number;
  heldCount: number;
  lastSyncedAt: string | null;
  syncing: boolean;
  /** station_id → the open offline stop, so "Running" can close it before the server has it. */
  openStarts: Record<string, OpenStart>;
  sheetOpen: boolean;
  /** A newer service worker took over this tab. */
  updateReady: boolean;
}

const OPEN_STARTS_KEY = "fm.openStarts";

function loadOpenStarts(): Record<string, OpenStart> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(OPEN_STARTS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, OpenStart>) : {};
  } catch {
    return {};
  }
}

export const useSync = create<SyncState>()(() => ({
  networkOnline: typeof navigator === "undefined" ? true : navigator.onLine,
  lastRequestOk: true,
  online: typeof navigator === "undefined" ? true : navigator.onLine,
  ops: [],
  held: [],
  results: {},
  applied: [],
  pendingCount: 0,
  rejectedCount: 0,
  heldCount: 0,
  lastSyncedAt: null,
  syncing: false,
  openStarts: loadOpenStarts(),
  sheetOpen: false,
  updateReady: false,
}));

const set = useSync.setState;

export function setNetworkOnline(value: boolean) {
  // The network coming back is a reason to try again; the next request
  // decides whether the API really answers.
  set(value ? { networkOnline: true, lastRequestOk: true, online: true } : { networkOnline: false, online: false });
}

/** Called by every API request: did the server answer at all? */
export function reportRequest(ok: boolean) {
  const state = useSync.getState();
  if (state.lastRequestOk === ok && state.online === (ok && state.networkOnline)) return;
  set({ lastRequestOk: ok, online: ok && state.networkOnline });
}

export function setOps(ops: OutboxOp[]) {
  set({
    ops,
    pendingCount: ops.filter((op) => op.status === "pending").length,
    rejectedCount: ops.filter((op) => op.status === "rejected").length,
  });
}

export function setHeld(held: HeldLocal[]) {
  set({ held, heldCount: held.length });
}

export function setSyncing(syncing: boolean) {
  set({ syncing });
}

export function markSynced() {
  set({ lastSyncedAt: new Date().toISOString() });
}

const RESULT_LIMIT = 100;
const APPLIED_TTL_MS = 5 * 60_000;

export function recordResults(results: SyncResult[], appliedOps: OutboxOp[]) {
  const state = useSync.getState();
  const merged = { ...state.results };
  for (const result of results) merged[result.client_ref] = result;
  const keys = Object.keys(merged);
  for (const key of keys.slice(0, Math.max(0, keys.length - RESULT_LIMIT))) delete merged[key];

  const now = Date.now();
  const applied = [
    ...state.applied.filter((entry) => now - entry.at < APPLIED_TTL_MS),
    ...appliedOps.map((op) => ({ op, at: now })),
  ];
  set({ results: merged, applied });
}

export function setOpenStarts(openStarts: Record<string, OpenStart>) {
  set({ openStarts });
  try {
    window.localStorage.setItem(OPEN_STARTS_KEY, JSON.stringify(openStarts));
  } catch {
    /* in-memory is enough for this tab */
  }
}

export function openSyncSheet(open = true) {
  set({ sheetOpen: open });
}

export function setUpdateReady() {
  set({ updateReady: true });
}
