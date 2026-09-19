"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CloudOff, CloudUpload, Hourglass, RefreshCw, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatClock, formatDate } from "@/lib/format";
import { loadSession } from "@/lib/api";
import { discard, dismissHeld, flush, retryRejected, startSyncAgent } from "@/lib/offline/outbox";
import { openSyncSheet, useSync } from "@/lib/offline/status";
import type { HeldLocal, OutboxOp } from "@/lib/offline/db";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorNote } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import { useT } from "@/lib/i18n";

/**
 * Where the tablet's unsent entries are visible.
 *
 * The pill answers one question at a glance from across the bench — "has what
 * I entered reached the office?" — green, amber or red, always with the words,
 * because colour alone fails in a sun-washed bay. The sheet behind it is the
 * detail: what is waiting, what the server refused and why, and what was held
 * for the owner because its shift had already been locked.
 */

export const KIND_LABEL: Record<OutboxOp["kind"], string> = {
  downtime_start: "Stop",
  downtime_close: "Restart",
  production: "Count",
  quality: "Quality batch",
};

/** Start the outbox triggers and refresh the screens its results change. */
export function useSyncAgent() {
  const queryClient = useQueryClient();
  const applied = useSync((s) => s.applied);
  const heldCount = useSync((s) => s.heldCount);
  const seen = useRef(0);

  useEffect(() => startSyncAgent(), []);

  useEffect(() => {
    const latest = applied.reduce((max, entry) => Math.max(max, entry.at), 0);
    if (latest <= seen.current) return;
    seen.current = latest;
    // The overlay keeps these entries on the grid until this refetch lands.
    void queryClient.invalidateQueries({ queryKey: ["grid"] });
    void queryClient.invalidateQueries({ queryKey: ["closure"] });
    void queryClient.invalidateQueries({ queryKey: ["kpis"] });
    void queryClient.invalidateQueries({ queryKey: ["shift-entries"] });
  }, [applied, queryClient]);

  useEffect(() => {
    if (heldCount) void queryClient.invalidateQueries({ queryKey: ["holds"] });
  }, [heldCount, queryClient]);
}

export function SyncPill() {
  const t = useT();
  const online = useSync((s) => s.online);
  const pending = useSync((s) => s.pendingCount);
  const rejected = useSync((s) => s.rejectedCount);
  const syncing = useSync((s) => s.syncing);
  const sheetOpen = useSync((s) => s.sheetOpen);
  const updateReady = useSync((s) => s.updateReady);
  const saved = pending + rejected;

  const view = !online
    ? {
        tone: "bg-[var(--color-stopped-dim)] text-[var(--color-loss)] border-transparent",
        Icon: CloudOff,
        long: saved ? t("Offline — {0} saved on this tablet", { 0: saved }) : t("Offline"),
        short: saved ? t("Offline · {0}", { 0: saved }) : t("Offline"),
      }
    : rejected
      ? {
          tone: "bg-[var(--color-stopped-dim)] text-[var(--color-loss)] border-transparent",
          Icon: TriangleAlert,
          long: t("{0} not accepted", { 0: rejected }),
          short: t("{0} not accepted", { 0: rejected }),
        }
      : pending
        ? {
            tone: "bg-[var(--color-warn-dim)] text-[var(--color-warn)] border-transparent",
            Icon: syncing ? RefreshCw : CloudUpload,
            long: t("{0} waiting to sync", { 0: pending }),
            short: t("{0} waiting", { 0: pending }),
          }
        : {
            tone: "bg-[var(--color-running-dim)] text-[var(--color-running)] border-transparent",
            Icon: CircleCheck,
            long: t("Synced"),
            short: t("Synced"),
          };

  return (
    <>
      {updateReady ? (
        <Button
          size="sm"
          variant="outline"
          className="h-11"
          onClick={() => window.location.reload()}
          title={t("A newer version of the app is ready")}
        >
          <RefreshCw className="size-4" />
          <span className="hidden md:inline">{t("Update ready — reload")}</span>
          <span className="md:hidden">{t("Update")}</span>
        </Button>
      ) : null}
      <button
        type="button"
        onClick={() => openSyncSheet(true)}
        aria-haspopup="dialog"
        aria-label={t("Sync status: {0}. Open the list.", { 0: view.long })}
        className={cn(
          "flex h-11 shrink-0 items-center gap-2 rounded-full border px-3.5 text-[0.8125rem] font-semibold transition-[filter] hover:brightness-95",
          view.tone,
        )}
      >
        <view.Icon className={cn("size-4 shrink-0", syncing && view.Icon === RefreshCw && "animate-spin")} />
        <span className="hidden whitespace-nowrap lg:inline">{view.long}</span>
        <span className="whitespace-nowrap lg:hidden">{view.short}</span>
      </button>
      {sheetOpen ? <SyncSheet onClose={() => openSyncSheet(false)} /> : null}
    </>
  );
}

function when(iso: string): string {
  const date = new Date(iso);
  const today = new Date().toDateString() === date.toDateString();
  return today ? formatClock(iso) : `${formatDate(iso)}, ${formatClock(iso)}`;
}

function describe(meta: OutboxOp["meta"]): string {
  return [meta.station_code, meta.part_number, meta.reason_label, meta.summary].filter(Boolean).join(" · ") || "—";
}

function SyncSheet({ onClose }: { onClose: () => void }) {
  const t = useT();
  const ops = useSync((s) => s.ops);
  const held = useSync((s) => s.held);
  const online = useSync((s) => s.online);
  const networkOnline = useSync((s) => s.networkOnline);
  const syncing = useSync((s) => s.syncing);
  const lastSyncedAt = useSync((s) => s.lastSyncedAt);
  const [error, setError] = useState<string | null>(null);
  const session = loadSession();

  const rejected = ops.filter((op) => op.status === "rejected");
  const pending = ops.filter((op) => op.status === "pending");

  const run = async (work: () => Promise<void>) => {
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("That did not work."));
    }
  };

  return (
    <Sheet
      title={t("Entries on this tablet")}
      subtitle={
        online
          ? lastSyncedAt
            ? t("Connected · last synced {0}", { 0: formatClock(lastSyncedAt) })
            : t("Connected")
          : t("Offline — entries are kept here and sent when the connection returns.")
      }
      onClose={onClose}
    >
      <div className="space-y-6">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="lg"
            disabled={!networkOnline || syncing || pending.length === 0}
            onClick={() => void run(() => flush({ force: true }))}
          >
            <RefreshCw className={cn("size-4", syncing && "animate-spin")} />
            {syncing ? t("Sending…") : t("Retry now")}
          </Button>
          {!networkOnline ? <span className="text-3 text-sm">{t("Needs a connection.")}</span> : null}
        </div>

        {error ? <ErrorNote message={error} /> : null}

        {rejected.length ? (
          <section aria-labelledby="sync-rejected">
            <h3 id="sync-rejected" className="label mb-2 text-[var(--color-loss)]">
              {t("Not accepted ({0})", { 0: rejected.length })}
            </h3>
            <p className="text-2 mb-3 text-sm leading-relaxed">
              {t("The server refused these and will keep refusing them as they are. Fix the cause (for example, open the shift) and try again, or discard the entry.")}
            </p>
            <ul className="divide-y rounded-xl border">
              {rejected.map((op) => (
                <RejectedRow key={op.client_ref} op={op} onError={setError} />
              ))}
            </ul>
          </section>
        ) : null}

        <section aria-labelledby="sync-pending">
          <h3 id="sync-pending" className="label mb-2">
            {t("Waiting to sync ({0})", { 0: pending.length })}
          </h3>
          {pending.length === 0 ? (
            <p className="text-3 rounded-xl border border-dashed px-4 py-5 text-center text-sm">
              {t("Nothing waiting. Everything entered on this tablet has reached the server.")}
            </p>
          ) : (
            <ul className="divide-y rounded-xl border">
              {pending.map((op) => (
                <li key={op.client_ref} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <Hourglass className="text-3 size-4 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold">
                      {KIND_LABEL[op.kind]} <span className="text-2 font-normal">· {describe(op.meta)}</span>
                    </p>
                    <p className="text-3 text-xs">
                      {when(op.occurred_at)}
                      {op.attempts ? t(" · tried {0}×", { 0: op.attempts }) : ""}
                      {session && op.meta.user_id !== session.user_id
                        ? t(" · waiting for {0} to sign in", { 0: op.meta.user_name })
                        : ""}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {held.length ? (
          <section aria-labelledby="sync-held">
            <h3 id="sync-held" className="label mb-2">
              {t("Held for owner review ({0})", { 0: held.length })}
            </h3>
            <p className="text-2 mb-3 text-sm leading-relaxed">
              {t("These reached the server after their shift was locked. Nothing is lost — the owner decides whether each one is written into that shift.")}
            </p>
            <ul className="divide-y rounded-xl border">
              {held.map((item) => (
                <HeldRow key={item.client_ref} item={item} onError={setError} />
              ))}
            </ul>
          </section>
        ) : null}

        {ops.length === 0 && held.length === 0 && !online ? (
          <Empty title={t("Nothing saved on this tablet yet")} hint={t("Entries made while offline will be listed here.")} />
        ) : null}
      </div>
    </Sheet>
  );
}

function RejectedRow({ op, onError }: { op: OutboxOp; onError: (message: string | null) => void }) {
  const t = useT();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const act = async (work: () => Promise<void>) => {
    setBusy(true);
    onError(null);
    try {
      await work();
    } catch (e) {
      onError(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <TriangleAlert className="size-4 shrink-0 text-[var(--color-loss)]" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            {KIND_LABEL[op.kind]} <span className="text-2 font-normal">· {describe(op.meta)}</span>
          </p>
          <p className="text-3 text-xs">{when(op.occurred_at)}</p>
          {op.message ? <p className="mt-1 text-sm text-[var(--color-loss)]">{op.message}</p> : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" className="h-11 px-4" disabled={busy} onClick={() => void act(() => retryRejected(op.client_ref))}>
            {t("Try again")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-11 border px-4 text-[var(--color-loss)]"
            disabled={busy}
            onClick={() => setConfirming(true)}
          >
            {t("Discard")}
          </Button>
        </div>
      </div>
      {confirming ? (
        <div role="alertdialog" aria-label={t("Confirm discard")} className="surface-2 rise mt-3 rounded-xl border p-3">
          <p className="text-sm font-semibold">{t("Discard this {0}?", { 0: KIND_LABEL[op.kind].toLowerCase() })}</p>
          <p className="text-2 mt-1 text-xs leading-relaxed">
            {t(
              "It is removed from this tablet and never reaches the server. If it really happened, enter it again once the cause is fixed.",
            )}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="lg" variant="stop" disabled={busy} onClick={() => void act(() => discard(op.client_ref))}>
              {t("Yes, discard")}
            </Button>
            <Button size="lg" variant="outline" onClick={() => setConfirming(false)}>
              {t("Keep it")}
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}

function HeldRow({ item, onError }: { item: HeldLocal; onError: (message: string | null) => void }) {
  const t = useT();
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <Badge variant="warn">{t("Held")}</Badge>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">
          {KIND_LABEL[item.kind]} <span className="text-2 font-normal">· {describe(item.meta)}</span>
        </p>
        <p className="text-3 text-xs">
          {when(item.occurred_at)}
          {item.message ? ` · ${item.message}` : ""}
        </p>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="h-11 border px-4"
        onClick={() => dismissHeld(item.client_ref).catch((e: Error) => onError(e.message))}
      >
        {t("Dismiss")}
      </Button>
    </li>
  );
}
