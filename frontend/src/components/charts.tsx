"use client";

import { useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatInr, formatInrCompact } from "@/lib/format";

/**
 * Charts for the owner's screens.
 *
 * Colour decisions, in the order they were made:
 *
 * 1. **Ranked losses use ONE hue, not a categorical palette.** The bars compare
 *    magnitudes of a single measure (rupees lost) across reasons — that is a
 *    sequential job, not an identity job. Giving each reason its own hue would
 *    imply the colours mean something they don't.
 *
 * 2. **Recoverable and structural are a status pair: red and grey.** Grey is
 *    deliberate. Structural loss is real money reported honestly, and it is not
 *    actionable — desaturating it is the whole point, so it never competes for
 *    attention with the money someone can still go and get. The pair separates
 *    at ΔE 10.3 under deuteranopia, and carries a legend plus direct labels.
 *
 * 3. **Loss and recovered profit never share a chart.** They are different
 *    measures on different scales, so a second axis would invite a relationship
 *    the data does not support. Red-vs-green also sits in the marginal CVD band
 *    (ΔE 6.8 deutan), and on a shop floor where red-green deficiency is common
 *    that is a bad bet. Two charts instead.
 */

const LOSS = "var(--color-loss)";
const STRUCTURAL = "var(--color-structural)";
const RECOVERED = "var(--color-recovered)";
const GRID = "var(--border)";
const TICK = "var(--text-3)";

const axisProps = {
  stroke: GRID,
  tick: { fill: TICK, fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

function shortDate(iso: string) {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** Tooltip chrome. Values wear text tokens; the colour sits in the swatch. */
function TipShell({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ label: string; value: string; colour?: string }>;
}) {
  return (
    <div className="surface elev-3 min-w-44 rounded-lg border px-3 py-2.5">
      <p className="label mb-1.5">{title}</p>
      <div className="space-y-1">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-4 text-xs">
            <span className="flex items-center gap-1.5">
              {row.colour ? (
                <span
                  aria-hidden
                  className="size-2 rounded-[2px]"
                  style={{ background: row.colour }}
                />
              ) : null}
              <span className="text-2">{row.label}</span>
            </span>
            <span className="money font-semibold">{row.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Loss over time                                                          */
/* ---------------------------------------------------------------------- */

export interface LossPoint {
  date: string;
  recoverable: number;
  structural: number;
  minutes: number;
}

export function LossTrend({ data, height = 240 }: { data: LossPoint[]; height?: number }) {
  const [asTable, setAsTable] = useState(false);
  const peak = Math.max(...data.map((d) => d.recoverable), 0);
  const peakDay = data.find((d) => d.recoverable === peak);

  if (asTable) {
    return (
      <div>
        <button
          type="button"
          onClick={() => setAsTable(false)}
          className="text-2 mb-3 text-xs font-medium underline-offset-2 hover:underline"
        >
          Show chart
        </button>
        <div className="max-h-64 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="surface sticky top-0">
              <tr className="border-b">
                <th className="label py-1.5 text-left">Day</th>
                <th className="label py-1.5 text-right">Recoverable</th>
                <th className="label py-1.5 text-right">Structural</th>
                <th className="label py-1.5 text-right">Downtime</th>
              </tr>
            </thead>
            <tbody>
              {[...data].reverse().map((point) => (
                <tr key={point.date} className="border-b last:border-0">
                  <td className="py-1.5">{shortDate(point.date)}</td>
                  <td className="money py-1.5 text-right text-[var(--color-loss)]">
                    {formatInr(point.recoverable)}
                  </td>
                  <td className="money text-3 py-1.5 text-right">
                    {formatInr(point.structural)}
                  </td>
                  <td className="tnum text-2 py-1.5 text-right">
                    {Math.round(point.minutes)} min
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Legend, always present for two series — identity is never carried by
          colour alone. */}
      <div className="mb-3 flex flex-wrap items-center gap-4">
        <LegendKey colour={LOSS} label="Recoverable" />
        <LegendKey colour={STRUCTURAL} label="Structural" muted />
        {peakDay ? (
          <span className="text-3 ml-auto text-xs">
            Worst day {shortDate(peakDay.date)} · {formatInr(peak)}
          </span>
        ) : null}
        {/* A table twin, so no value is reachable only by hovering. */}
        <button
          type="button"
          onClick={() => setAsTable(true)}
          className="text-3 text-xs font-medium underline-offset-2 hover:underline"
        >
          Table
        </button>
      </div>

      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
          <defs>
            <linearGradient id="lossFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={LOSS} stopOpacity={0.22} />
              <stop offset="100%" stopColor={LOSS} stopOpacity={0.02} />
            </linearGradient>
          </defs>

          {/* Solid hairline. A dashed grid reads as "threshold" or
              "projection" when it is only a grid. */}
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            minTickGap={28}
            {...axisProps}
          />
          <YAxis
            tickFormatter={(v: number) => formatInrCompact(v)}
            width={56}
            {...axisProps}
          />
          <Tooltip
            cursor={{ stroke: GRID, strokeWidth: 1 }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TipShell
                  title={shortDate(String(label))}
                  rows={[
                    {
                      label: "Recoverable",
                      value: formatInr(Number(payload[0]?.payload.recoverable ?? 0)),
                      colour: LOSS,
                    },
                    {
                      label: "Structural",
                      value: formatInr(Number(payload[0]?.payload.structural ?? 0)),
                      colour: STRUCTURAL,
                    },
                    {
                      label: "Downtime",
                      value: `${Math.round(Number(payload[0]?.payload.minutes ?? 0))} min`,
                    },
                  ]}
                />
              ) : null
            }
          />

          {/* Structural sits behind, flat and quiet: context, not the subject. */}
          <Area
            type="monotone"
            dataKey="structural"
            stroke={STRUCTURAL}
            strokeWidth={1.5}
            strokeDasharray="3 3"
            fill="none"
            dot={false}
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="recoverable"
            stroke={LOSS}
            strokeWidth={2}
            fill="url(#lossFill)"
            dot={false}
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ---------------------------------------------------------------------- */
/* Recovered profit — its own chart, deliberately                          */
/* ---------------------------------------------------------------------- */

export function RecoveredTrend({
  data,
  height = 180,
}: {
  data: Array<{ week: string; saving: number; cumulative: number }>;
  height?: number;
}) {
  if (data.length === 0) {
    return (
      <p className="text-3 py-10 text-center text-sm">
        Nothing verified yet. Savings are credited only after a fix has held for
        two consecutive weeks.
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="week" tickFormatter={shortDate} minTickGap={28} {...axisProps} />
        <YAxis tickFormatter={(v: number) => formatInrCompact(v)} width={56} {...axisProps} />
        <Tooltip
          cursor={{ stroke: GRID, strokeWidth: 1 }}
          content={({ active, payload, label }) =>
            active && payload?.length ? (
              <TipShell
                title={`Week of ${shortDate(String(label))}`}
                rows={[
                  {
                    label: "Credited",
                    value: formatInr(Number(payload[0]?.payload.saving ?? 0)),
                    colour: RECOVERED,
                  },
                  {
                    label: "Cumulative",
                    value: formatInr(Number(payload[0]?.payload.cumulative ?? 0)),
                  },
                ]}
              />
            ) : null
          }
        />
        {/* One series, so no legend box — the card title names it. */}
        <Line
          type="monotone"
          dataKey="cumulative"
          stroke={RECOVERED}
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

/* ---------------------------------------------------------------------- */
/* Ranked losses — one hue, because this compares magnitude, not identity  */
/* ---------------------------------------------------------------------- */

export interface RankedItem {
  label: string;
  sublabel?: string;
  value: number;
  display: string;
}

export function RankedBars({ items, max }: { items: RankedItem[]; max?: number }) {
  const ceiling = max ?? Math.max(...items.map((i) => i.value), 1);

  // A plain HTML bar list rather than a chart component: at this size it gives
  // better label control, wraps properly on a phone, and stays readable when
  // printed for a morning meeting.
  return (
    <ol className="space-y-2.5">
      {items.map((item, index) => {
        const pct = Math.max(2, (item.value / ceiling) * 100);
        return (
          <li key={`${item.label}-${index}`} className="group">
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <span className="flex min-w-0 items-baseline gap-2">
                <span className="text-3 tnum w-4 shrink-0 text-xs font-semibold">
                  {index + 1}
                </span>
                <span className="truncate text-sm font-medium">{item.label}</span>
                {item.sublabel ? (
                  <span className="text-3 shrink-0 text-xs">{item.sublabel}</span>
                ) : null}
              </span>
              {/* Direct label on every bar — the secondary encoding that keeps
                  this readable without relying on the fill. */}
              <span className="money shrink-0 text-sm font-semibold text-[var(--color-loss)]">
                {item.display}
              </span>
            </div>
            <div className="surface-inset h-2 overflow-hidden rounded-full">
              {/* One colour for every bar. Downtime reasons are nominal — they
                  have no natural order — so shading them darker-where-bigger
                  would double-encode the length the bar already shows, and burn
                  the only free channel on information that is already there. */}
              <div
                className="h-full rounded-full bg-[var(--color-loss)] transition-[width] duration-700 ease-out"
                style={{ width: `${pct}%` }}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/* ---------------------------------------------------------------------- */
/* Sparkline — a stat tile's supporting mark, no axes                      */
/* ---------------------------------------------------------------------- */

export function Sparkline({
  data,
  dataKey = "value",
  tone = "loss",
  height = 40,
}: {
  data: Array<Record<string, number | string>>;
  dataKey?: string;
  tone?: "loss" | "recovered" | "accent";
  height?: number;
}) {
  const colour = {
    loss: LOSS,
    recovered: RECOVERED,
    accent: "var(--color-accent)",
  }[tone];

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={`spark-${tone}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={colour} stopOpacity={0.28} />
            <stop offset="100%" stopColor={colour} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey={dataKey}
          stroke={colour}
          strokeWidth={1.75}
          fill={`url(#spark-${tone})`}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ---------------------------------------------------------------------- */
/* OEE bar — A x P x Q, shown as its three factors                         */
/* ---------------------------------------------------------------------- */

export function OeeBreakdown({
  availability,
  performance,
  quality,
  oee,
}: {
  availability: number;
  performance: number;
  quality: number;
  oee: number;
}) {
  const factors = [
    { label: "Availability", value: availability },
    { label: "Performance", value: performance },
    { label: "Quality", value: quality },
  ];

  return (
    <div>
      <div className="mb-3 flex items-baseline gap-2">
        <span className="figure text-4xl font-bold">
          {(oee * 100).toFixed(0)}%
        </span>
        <span className="text-3 text-xs">
          {factors.map((f) => `${(f.value * 100).toFixed(0)}%`).join(" × ")}
        </span>
      </div>
      <div className="space-y-2">
        {factors.map((factor) => (
          <div key={factor.label}>
            <div className="mb-1 flex justify-between text-xs">
              <span className="text-2">{factor.label}</span>
              <span className="money font-semibold">
                {(factor.value * 100).toFixed(0)}%
              </span>
            </div>
            <div className="surface-inset h-1.5 overflow-hidden rounded-full">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.min(100, factor.value * 100)}%`,
                  background:
                    factor.value >= 0.85
                      ? "var(--color-running)"
                      : factor.value >= 0.65
                        ? "var(--color-warn)"
                        : "var(--color-stopped)",
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function LegendKey({
  colour,
  label,
  muted,
}: {
  colour: string;
  label: string;
  muted?: boolean;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="h-0.5 w-4 rounded-full"
        style={{
          background: colour,
          opacity: muted ? 0.7 : 1,
        }}
      />
      <span className="text-2 text-xs font-medium">{label}</span>
    </span>
  );
}

export { Bar, BarChart, Cell };
