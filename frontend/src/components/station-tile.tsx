"use client";

import { useEffect, useState } from "react";
import { ArrowLeftRight, Gauge, Hourglass, Plus, Square, Triangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatInr, formatMinutes, minutesSince } from "@/lib/format";
import { Money } from "@/components/ui/money";
import { StatusLed } from "@/components/ui/badge";
import type { LocalTile } from "@/lib/offline/grid";

/**
 * One machine tile (Screen 2).
 *
 * §2 explains why the money is on this screen rather than buried in a report:
 *
 *   "When the whole shift can see 'Line 3: ₹8,400 lost today' on a screen,
 *    hiding downtime stops being possible and starts being pointless."
 *
 * A stopped tile reads three ways at once — red fill, a heavier ring with a
 * glow, and the word "STOPPED" — because colour alone fails on a sun-washed
 * tablet and for the roughly one man in twelve with a red-green deficiency.
 *
 * A tile whose state comes from an entry still on the tablet (`pending`) says
 * so, and shows minutes but no rupees: the cost is the server's to compute,
 * and a ₹0 on a stopped machine would be a number nobody authorised.
 */
export function StationTileCard({
  tile,
  onStop,
  onStart,
  onCount,
  onChangeJob,
  index = 0,
}: {
  tile: LocalTile;
  onStop: (tile: LocalTile) => void;
  onStart: (tile: LocalTile) => void;
  onCount: (tile: LocalTile) => void;
  /** Supervisors and owners only; omitted for everyone else. */
  onChangeJob?: (tile: LocalTile) => void;
  index?: number;
}) {
  const live = useLiveCost(tile);
  const stopped = tile.status === "stopped";
  const running = tile.status === "running";
  const pace = tile.target > 0 ? tile.count / tile.target : null;

  return (
    <article
      style={{ animationDelay: `${Math.min(index * 25, 300)}ms` }}
      className={cn(
        "rise flex flex-col rounded-[var(--radius-tile)] p-4 transition-[box-shadow,transform] duration-200",
        stopped ? "state-stopped" : running ? "state-running" : "state-idle",
      )}
    >
      {/* ---- identity ---------------------------------------------------- */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="display text-[1.0625rem] font-bold">{tile.code}</h3>
            {stopped ? (
              <span className="rounded bg-[var(--color-stopped)] px-1.5 py-px text-[0.625rem] font-bold uppercase tracking-wider text-white">
                Stopped
              </span>
            ) : (
              <StatusLed state={running ? "running" : "idle"} />
            )}
          </div>
          <p className="text-3 mt-0.5 truncate text-xs">
            {tile.job_number ? (
              <>
                <span className="tnum">#{tile.job_number}</span> · {tile.part_number}
              </>
            ) : (
              tile.name
            )}
          </p>
        </div>
      </div>

      {/* ---- the number that matters ------------------------------------ */}
      <div className="mt-3.5 min-h-[3.25rem]">
        {stopped ? (
          <>
            <p className="text-[0.6875rem] font-semibold uppercase tracking-wide text-[var(--color-loss)]">
              {tile.reason_label ?? "Reason not set"}
            </p>
            <div className="mt-0.5 flex items-baseline gap-2">
              {tile.pending ? (
                <span className="money text-[1.5rem] font-bold leading-none text-[var(--color-loss)]">
                  {formatMinutes(live.minutes)}
                </span>
              ) : (
                <>
                  <Money value={formatInr(live.cost)} tone="loss" size="lg" live />
                  <span className="text-3 tnum text-xs">{formatMinutes(live.minutes)}</span>
                </>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="flex items-baseline gap-1.5">
              <span className="money text-[1.75rem] font-bold leading-none">
                {tile.count}
              </span>
              <span className="text-3 text-sm">/ {tile.target || "—"}</span>
              {pace !== null ? (
                <span
                  className={cn(
                    "tnum ml-auto text-xs font-semibold",
                    pace >= 0.95
                      ? "text-[var(--color-running)]"
                      : pace >= 0.75
                        ? "text-[var(--color-warn)]"
                        : "text-[var(--color-stopped)]",
                  )}
                >
                  {(pace * 100).toFixed(0)}%
                </span>
              ) : null}
            </div>
            {pace !== null ? (
              <div className="surface-inset mt-2.5 h-1.5 overflow-hidden rounded-full">
                <div
                  className={cn("h-full rounded-full transition-[width] duration-700 ease-out")}
                  style={{
                    width: `${Math.min(100, pace * 100)}%`,
                    background:
                      pace >= 0.95
                        ? "var(--color-running)"
                        : pace >= 0.75
                          ? "var(--color-warn)"
                          : "var(--color-stopped)",
                  }}
                />
              </div>
            ) : null}
          </>
        )}
      </div>

      {/* ---- actions. Two taps to log a stop, one to clear it. ----------- */}
      <div className="mt-4 flex gap-2">
        {stopped ? (
          <button
            type="button"
            onClick={() => onStart(tile)}
            className="touch flex flex-1 items-center justify-center gap-2 rounded-xl bg-[var(--color-running)] text-sm font-bold text-white shadow-[var(--shadow-2)] transition-[filter,transform] active:translate-y-px hover:brightness-110"
          >
            <Triangle className="size-4 rotate-90 fill-current" />
            Running
          </button>
        ) : (
          <>
            <button
              type="button"
              onClick={() => onStop(tile)}
              className="touch flex flex-1 items-center justify-center gap-2 rounded-xl bg-[var(--color-stopped)] text-sm font-bold text-white shadow-[var(--shadow-2)] transition-[filter,transform] active:translate-y-px hover:brightness-110"
            >
              <Square className="size-3.5 fill-current" />
              Stopped
            </button>
            <button
              type="button"
              onClick={() => onCount(tile)}
              aria-label={`Enter production count for ${tile.code}`}
              className="touch surface grid w-14 place-items-center rounded-xl border shadow-[var(--shadow-1)] transition-colors hover:surface-2"
            >
              <Plus className="size-5" />
            </button>
          </>
        )}
      </div>

      {onChangeJob ? (
        <button
          type="button"
          onClick={() => onChangeJob(tile)}
          aria-label={`Change job on ${tile.code}`}
          className="text-2 mt-2 flex min-h-11 items-center justify-center gap-2 rounded-xl border text-[0.8125rem] font-medium transition-colors hover:surface-2 hover:text-[var(--text)]"
        >
          <ArrowLeftRight className="size-3.5" />
          Change job
        </button>
      ) : null}

      {tile.pending ? (
        <p className="mt-3 flex items-center gap-1.5 text-[0.6875rem] font-semibold text-[var(--color-warn)]">
          <Hourglass className="size-3" />
          Saved on this tablet — will sync
        </p>
      ) : null}

      {tile.shift_cost_paise > 0 ? (
        <p className="text-3 mt-3 flex items-center gap-1.5 text-[0.6875rem]">
          <Gauge className="size-3" />
          Lost this shift <span className="money font-semibold">{formatInr(tile.shift_cost_paise)}</span>
        </p>
      ) : null}
    </article>
  );
}

/**
 * Interpolate the ticker between server pushes.
 *
 * The authoritative figure always comes from the server, which computed it with
 * the same kernel function that will freeze the final cost. This only keeps the
 * seconds moving in between, so a number people are watching never looks stale.
 *
 * It advances ONLY the time-based terms. Lost contribution steps one whole part
 * at a time, and drawing a smooth curve through it would put a number on screen
 * that the kernel never produced.
 */
function useLiveCost(tile: LocalTile) {
  const [, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (tile.status !== "stopped") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [tile.status, tile.open_event_id]);

  if (tile.status !== "stopped" || !tile.stopped_since) {
    return { cost: tile.live_cost_paise, minutes: 0 };
  }

  const minutes = minutesSince(tile.stopped_since);
  const sinceServer = Math.max(0, minutes - tile.stopped_minutes);
  return {
    cost: tile.live_cost_paise + Math.round(sinceServer * tile.ticker_rate_per_minute),
    minutes,
  };
}
