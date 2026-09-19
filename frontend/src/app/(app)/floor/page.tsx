"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, PlayCircle } from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { can } from "@/lib/roles";
import { useLiveChannel } from "@/lib/ws";
import { formatInr } from "@/lib/format";
import { enqueue } from "@/lib/offline/outbox";
import { closeTarget, overlayGrid, type LocalTile } from "@/lib/offline/grid";
import { openSyncSheet, useSync } from "@/lib/offline/status";
import { Money } from "@/components/ui/money";
import { Badge, StatusLed } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import { StationTileCard } from "@/components/station-tile";
import { ChangeJobSheet } from "@/components/change-job";
import type { ReasonCode, StationTile } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * Screens 2, 3 and 4 on one surface.
 *
 * The spec lists them separately, but a supervisor logging a stop is already
 * standing at the grid — sending them to a "Downtime Log" page would spend the
 * 20-second budget on wayfinding. So the grid IS the home, and stop/count are
 * sheets over it.
 *
 * Every stop, restart and count goes to the tablet's outbox first and is sent
 * from there (src/lib/offline). The tile flips the instant it is tapped, with
 * or without a connection; the header pill says whether it has reached the
 * server. Changing a job is the one action here that needs the network.
 */
export default function Floor() {
  const t = useT();
  const queryClient = useQueryClient();
  const session = loadSession();
  const canChangeJob = session ? can(session.roles, "supervisor", "owner") : false;
  const [stopping, setStopping] = useState<LocalTile | null>(null);
  const [restarting, setRestarting] = useState<LocalTile | null>(null);
  const [counting, setCounting] = useState<LocalTile | null>(null);
  const [changing, setChanging] = useState<LocalTile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const grid = useQuery({ queryKey: ["grid"], queryFn: api.grid });
  const reasons = useQuery({ queryKey: ["reasons"], queryFn: api.reasonCodes });
  const shift = useQuery({ queryKey: ["shift"], queryFn: api.currentShift });

  const ops = useSync((s) => s.ops);
  const applied = useSync((s) => s.applied);
  const openStarts = useSync((s) => s.openStarts);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["grid"] });

  // Live pushes: a stop logged on another tablet must appear here without
  // anyone refreshing.
  useLiveChannel((event) => {
    if (event.type === "ticker.tick") {
      queryClient.setQueryData<StationTile[]>(["grid"], (tiles) =>
        tiles?.map((tile) => {
          const tick = event.payload.events.find((e) => e.station_id === tile.station_id);
          return tick
            ? { ...tile, live_cost_paise: tick.cost, stopped_minutes: tick.minutes }
            : tile;
        }),
      );
      return;
    }
    refresh();
  });

  /** Save to the tablet; the outbox sends it. Only a local failure is an error here. */
  const record = async (work: Parameters<typeof enqueue>[0], after?: () => void): Promise<boolean> => {
    try {
      await enqueue(work);
      setError(null);
      after?.();
      return true;
    } catch (e) {
      setError(t("Could not save this on the tablet: {0}", { 0: (e as Error).message }));
      return false;
    }
  };

  const shiftSessionId = shift.data?.id ?? null;

  const stop = (tile: LocalTile, reason: ReasonCode, note: string) =>
    record(
      {
        kind: "downtime_start",
        shift_session_id: shiftSessionId,
        payload: {
          station_id: tile.station_id,
          reason_code_id: reason.id,
          ...(note.trim() ? { note: note.trim() } : {}),
        },
        meta: {
          station_id: tile.station_id,
          station_code: tile.code,
          part_number: tile.part_number ?? undefined,
          reason_label: reason.label,
        },
      },
      () => setStopping(null),
    );

  /** "Running". A stop with no reason gets one first — never closed blank. */
  const start = async (tile: LocalTile, reason?: ReasonCode): Promise<boolean> => {
    const target = closeTarget(tile, ops, openStarts);
    if (!target) {
      setError(t("{0}: this stop has not reached the tablet yet. Refresh the page, then try again.", { 0: tile.code }));
      return false;
    }
    if (!tile.reason_label && !reason) {
      setRestarting(tile);
      return true;
    }
    return record(
      {
        kind: "downtime_close",
        shift_session_id: shiftSessionId,
        payload: { ...target, ...(reason ? { reason_code_id: reason.id } : {}) },
        meta: {
          station_id: tile.station_id,
          station_code: tile.code,
          part_number: tile.part_number ?? undefined,
          reason_label: reason?.label ?? tile.reason_label ?? undefined,
        },
      },
      () => setRestarting(null),
    );
  };

  const count = (tile: LocalTile, value: number) =>
    record(
      {
        kind: "production",
        shift_session_id: shiftSessionId,
        payload: { station_id: tile.station_id, count: value },
        meta: {
          station_id: tile.station_id,
          station_code: tile.code,
          part_number: tile.part_number ?? undefined,
          summary: `${value} part${value === 1 ? "" : "s"}`,
        },
      },
      () => setCounting(null),
    );

  const tiles = useMemo(
    () => overlayGrid(grid.data ?? [], ops, applied, grid.dataUpdatedAt),
    [grid.data, grid.dataUpdatedAt, ops, applied],
  );
  const rejectedHere = ops.filter((op) => op.status === "rejected" && op.kind !== "quality");

  const totals = useMemo(() => {
    const stoppedTiles = tiles.filter((tile) => tile.status === "stopped");
    return {
      stopped: stoppedTiles.length,
      running: tiles.filter((tile) => tile.status === "running").length,
      idle: tiles.filter((tile) => tile.status === "idle").length,
      liveLoss: stoppedTiles.reduce((sum, tile) => sum + tile.live_cost_paise, 0),
      shiftLoss: tiles.reduce((sum, tile) => sum + tile.shift_cost_paise, 0),
    };
  }, [tiles]);

  const lines = useMemo(() => {
    const grouped = new Map<string, LocalTile[]>();
    for (const tile of tiles) {
      const key = tile.line ?? "Other";
      grouped.set(key, [...(grouped.get(key) ?? []), tile]);
    }
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [tiles]);

  // Only a definite "no open shift" sends them to Start. Not being able to
  // ask (offline, no saved copy) is a different answer: keep the grid usable.
  if (!shift.isLoading && !shift.error && !shift.data) {
    return (
      <Empty
        icon={<PlayCircle className="size-6" />}
        title={t("No shift is open")}
        hint={t("Start the shift to begin recording. Under a minute — pick the shift, confirm what each line is running, tap Start.")}
        action={
          <Button asChild className="mt-2">
            <a href="/shift">{t("Start shift")}</a>
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* ---- shift banner ------------------------------------------------ */}
      <header className="rise surface elev-2 flex flex-wrap items-center justify-between gap-x-8 gap-y-4 rounded-[var(--radius-card)] border p-5">
        <div>
          <p className="label mb-1.5">{t("Shopfloor")}</p>
          <h1 className="display text-[1.75rem] font-bold">{t("Machine grid")}</h1>
          <div className="text-2 mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <span className="flex items-center gap-1.5">
              <StatusLed state="running" /> {t("{0} running", { 0: totals.running })}
            </span>
            <span className="flex items-center gap-1.5">
              <StatusLed state="stopped" /> {t("{0} stopped", { 0: totals.stopped })}
            </span>
            <span className="flex items-center gap-1.5">
              <StatusLed state="idle" /> {t("{0} idle", { 0: totals.idle })}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-8">
          {totals.liveLoss > 0 ? (
            <div>
              <p className="label mb-1 flex items-center gap-1.5 text-[var(--color-loss)]">
                <AlertTriangle className="size-3" />
                {t("Stopped right now")}
              </p>
              <Money value={formatInr(totals.liveLoss)} tone="loss" size="xl" live />
            </div>
          ) : null}
          <div>
            <p className="label mb-1">{t("Lost this shift")}</p>
            <Money value={formatInr(totals.shiftLoss)} tone="loss" size="xl" />
          </div>
        </div>
      </header>

      {error ? <ErrorNote message={error} /> : null}
      {notice ? (
        <p role="status" className="surface-2 rise flex flex-wrap items-center justify-between gap-2 rounded-xl border px-4 py-2 text-sm">
          <span>{notice}</span>
          <Button size="sm" variant="ghost" className="h-11" onClick={() => setNotice(null)}>
            {t("Dismiss")}
          </Button>
        </p>
      ) : null}
      {shift.error && !shift.data ? (
        <ErrorNote message={t("Could not check the open shift ({0}). Entries are still kept on this tablet.", { 0: shift.error.message })} />
      ) : null}
      {rejectedHere.length ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[color-mix(in_oklab,var(--color-stopped)_35%,transparent)] bg-[var(--color-stopped-dim)] px-4 py-2 text-sm text-[var(--color-loss)]">
          <span>
            {t("{0} entr{1} not accepted by the server. {2}{3}", { 0: rejectedHere.length, 1: rejectedHere.length === 1 ? "y was" : "ies were", 2: rejectedHere[0].meta.station_code ? `${rejectedHere[0].meta.station_code}: ` : "", 3: rejectedHere[0].message })}
          </span>
          <Button size="sm" variant="outline" className="h-11" onClick={() => openSyncSheet(true)}>
            {t("See entries")}
          </Button>
        </div>
      ) : null}

      {grid.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-52" />
          ))}
        </div>
      ) : grid.error && !grid.data ? (
        <div className="space-y-3">
          <ErrorNote message={t("Could not load the machine grid: {0}", { 0: grid.error.message })} />
          <Button size="lg" variant="outline" onClick={() => void grid.refetch()}>
            {t("Try again")}
          </Button>
        </div>
      ) : tiles.length === 0 ? (
        <Empty
          title="No machines on the grid"
          hint={t("Stations appear here once they are set up in Settings → Stations, or loaded from template T1.")}
        />
      ) : (
        lines.map(([line, group], lineIndex) => (
          <section key={line}>
            <div className="mb-2.5 flex items-center gap-3">
              <h2 className="label">{line}</h2>
              <span className="hairline flex-1" />
              <span className="text-3 tnum text-xs">
                {t("{0} parts", { 0: group.reduce((s, tile) => s + tile.count, 0) })}
              </span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {group.map((tile, i) => (
                <StationTileCard
                  key={tile.station_id}
                  tile={tile}
                  index={lineIndex * 4 + i}
                  onStop={(tile) => {
                    setError(null);
                    setStopping(tile);
                  }}
                  onStart={(tile) => {
                    setError(null);
                    void start(tile);
                  }}
                  onCount={(tile) => {
                    setError(null);
                    setCounting(tile);
                  }}
                  onChangeJob={canChangeJob ? setChanging : undefined}
                />
              ))}
            </div>
          </section>
        ))
      )}

      {stopping ? (
        <ReasonSheet
          tile={stopping}
          mode="stop"
          reasons={reasons.data ?? []}
          reasonsError={reasons.error?.message ?? null}
          error={error}
          onPick={(reason, note) => stop(stopping, reason, note)}
          onClose={() => setStopping(null)}
        />
      ) : null}

      {restarting ? (
        <ReasonSheet
          tile={restarting}
          mode="restart"
          reasons={reasons.data ?? []}
          reasonsError={reasons.error?.message ?? null}
          error={error}
          onPick={(reason) => start(restarting, reason)}
          onClose={() => setRestarting(null)}
        />
      ) : null}

      {counting ? (
        <CountSheet
          tile={counting}
          error={error}
          onSubmit={(value) => count(counting, value)}
          onClose={() => setCounting(null)}
        />
      ) : null}

      {changing ? (
        <ChangeJobSheet
          tile={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging(null);
            setNotice(message);
          }}
        />
      ) : null}
    </div>
  );
}

/**
 * Screen 3 — the reason list. Biggest buttons for the commonest reasons.
 *
 * A fixed list, never free text: free text cannot be costed, cannot be ranked,
 * and cannot decide who gets the 15-minute WhatsApp. Two taps, twenty seconds.
 * The note is optional and folded away, so it never costs the two-tap path —
 * it adds context to a reason, it never replaces one.
 *
 * `restart` is the same list for a stop that was logged without a reason (a
 * sensor, a changeover): the machine does not go back to Running blank.
 */
function ReasonSheet({
  tile,
  mode,
  reasons,
  reasonsError,
  error,
  onPick,
  onClose,
}: {
  tile: LocalTile;
  mode: "stop" | "restart";
  reasons: ReasonCode[];
  reasonsError: string | null;
  error: string | null;
  /** Resolves false when the tablet could not save it, so the list unlocks. */
  onPick: (reason: ReasonCode, note: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [common, rest] = useMemo(() => {
    const sorted = [...reasons].sort((a, b) => a.display_order - b.display_order);
    return [sorted.slice(0, 6), sorted.slice(6)];
  }, [reasons]);

  // Saving to the tablet is near-instant; this only stops a double tap
  // logging the same stop twice.
  const pick = async (reason: ReasonCode) => {
    if (busy) return;
    setBusy(true);
    if (!(await onPick(reason, note))) setBusy(false);
  };

  return (
    <Sheet
      title={mode === "stop" ? t("{0} stopped", { 0: tile.code }) : t("{0} — why did it stop?", { 0: tile.code })}
      subtitle={mode === "stop" ? t("Why?") : t("No reason was recorded. Pick one and the machine goes back to Running.")}
      onClose={onClose}
    >
      {mode === "stop" ? (
        noteOpen ? (
          <div className="mb-4">
            <Label htmlFor="stop-note">{t("Note (optional)")}</Label>
            <Textarea
              id="stop-note"
              rows={2}
              maxLength={1000}
              value={note}
              autoFocus
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("Coolant pump tripped twice before this")}
              aria-describedby="stop-note-hint"
            />
            <p id="stop-note-hint" className="text-3 mt-1 text-xs">
              {t("{0}/1000 · then tap the reason below.", { 0: note.length })}
            </p>
          </div>
        ) : (
          <Button type="button" size="sm" variant="outline" className="mb-4 h-11 px-4" onClick={() => setNoteOpen(true)}>
            {t("Add a note")}
          </Button>
        )
      ) : null}

      {error ? (
        <div className="mb-4">
          <ErrorNote message={error} />
        </div>
      ) : null}
      {reasonsError && reasons.length === 0 ? (
        <ErrorNote message={t("Could not load the reason list: {0}", { 0: reasonsError })} />
      ) : null}

      <div className="grid gap-2.5 sm:grid-cols-2">
        {common.map((reason) => (
          <button
            key={reason.id}
            type="button"
            disabled={busy}
            onClick={() => void pick(reason)}
            className="touch-lg surface elev-1 group rounded-xl border px-4 text-left transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-[var(--shadow-2)] disabled:opacity-50"
          >
            <span className="block text-[0.9375rem] font-semibold">{reason.label}</span>
            <span className="mt-1 flex flex-wrap items-center gap-1.5">
              <Badge variant={reason.recoverability === "R" ? "recoverable" : "structural"}>
                {reason.recoverability === "R" ? t("Recoverable") : t("Structural")}
              </Badge>
              {reason.escalation_route !== "none" ? (
                <span className="text-3 text-[0.6875rem] capitalize">
                  → {reason.escalation_route}
                </span>
              ) : null}
            </span>
          </button>
        ))}
      </div>

      {rest.length ? (
        <details className="mt-4">
          <summary className="text-2 flex min-h-11 cursor-pointer items-center text-sm font-medium">
            {t("Other reasons ({0})", { 0: rest.length })}
          </summary>
          <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
            {rest.map((reason) => (
              <button
                key={reason.id}
                type="button"
                disabled={busy}
                onClick={() => void pick(reason)}
                className="touch surface rounded-lg border px-3.5 text-left text-sm transition-colors hover:surface-2 disabled:opacity-50"
              >
                {reason.label}
              </button>
            ))}
          </div>
        </details>
      ) : null}
    </Sheet>
  );
}

/** Screen 4 — count since the last entry. Number pad, fifteen seconds. */
function CountSheet({
  tile,
  error,
  onSubmit,
  onClose,
}: {
  tile: LocalTile;
  error: string | null;
  onSubmit: (value: number) => Promise<boolean>;
  onClose: () => void;
}) {
  const t = useT();
  const [value, setValue] = useState("");
  const [sent, setSent] = useState(false);

  return (
    <Sheet title={tile.code} subtitle={t("Parts since your last entry")} onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const parsed = Number.parseInt(value, 10);
          if (sent || !Number.isFinite(parsed) || parsed < 0) return;
          setSent(true);
          if (!(await onSubmit(parsed))) setSent(false);
        }}
      >
        <Input
          autoFocus
          // inputMode numeric brings up the number pad, not a full keyboard.
          inputMode="numeric"
          pattern="[0-9]*"
          value={value}
          onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))}
          placeholder="0"
          aria-label={t("Parts produced since last entry")}
          className="money h-28 text-center text-6xl font-bold"
        />
        <p className="text-3 mt-2.5 text-center text-xs">
          {t("Not the running total — just what came off since you last looked.")}
        </p>
        {error ? (
          <div className="mt-3">
            <ErrorNote message={error} />
          </div>
        ) : null}
        <Button type="submit" size="touch" className="mt-4 w-full" disabled={sent || !value}>
          {sent ? t("Saving…") : t("Save count")}
        </Button>
      </form>
    </Sheet>
  );
}
