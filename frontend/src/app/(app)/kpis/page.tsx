"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ArrowDownRight, ArrowRight, ArrowUpRight, BarChart3, FileText } from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { can } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import type { DowntimePercent, Productivity, Role } from "@/types/api";

/**
 * KPIs (§8) — one executive page, a drill-down per role.
 *
 *   "No KPI zoo. Launch with roughly a dozen KPIs across all roles."
 *   "Every KPI is derived from data already being entered."
 *
 * Every OEE figure here is aggregated by the kernel from per-station OEE — the
 * same path as a shift's KPIs — so a line cannot disagree with the shifts it
 * came from. Rows are machines, lines, reasons and defects; never people (§10).
 */

type Tab = "executive" | "supervisor" | "quality" | "maintenance";

const TABS: Array<{ key: Tab; label: string; why: string }> = [
  { key: "executive", label: "Executive", why: "Money first. The owner should never need to understand OEE to use this product." },
  { key: "supervisor", label: "Supervisor", why: "What they control this shift, and nothing they don't." },
  { key: "quality", label: "Quality", why: "Points them at the machine causing tomorrow's complaint." },
  { key: "maintenance", label: "Maintenance", why: "Turns 'CNC-3 keeps breaking' into a number that ranks their day." },
];

/** Money figures are owner-confidential: the API answers 403 to anyone else. */
function seesMoney(roles: Role[]): boolean {
  return can(roles, "owner", "consultant");
}

function defaultTab(roles: Role[]): Tab {
  if (seesMoney(roles)) return "executive";
  if (roles.includes("quality")) return "quality";
  if (roles.includes("maintenance")) return "maintenance";
  return "supervisor";
}

// The readable end of the brand family: marks need ~3:1 against the surface,
// and the light apricot fill does not have it.
const MARK = "var(--color-brand-deep)";
const GRID = "var(--border)";

export default function KpisPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <Kpis />
    </Suspense>
  );
}

function Kpis() {
  const session = loadSession();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const roles = session?.roles ?? [];
  const money = seesMoney(roles);
  const tabs = TABS.filter((t) => t.key !== "executive" || money);
  const requested = params.get("tab") as Tab | null;
  const tab = requested && tabs.some((t) => t.key === requested) ? requested : defaultTab(roles);
  const [days, setDays] = useState(7);
  const active = tabs.find((t) => t.key === tab) ?? tabs[0];

  return (
    <div className="space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5 flex items-center gap-1.5">
            <BarChart3 className="size-3" />
            §8 · Role KPIs
          </p>
          <h1 className="display text-[1.75rem] font-bold">KPIs</h1>
          <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">{active.why}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {money ? (
            <Link
              href="/kpis/monthly"
              className="text-2 flex items-center gap-1.5 rounded-xl border px-3.5 py-2 text-sm font-medium transition-colors hover:surface-2 hover:text-[var(--text)]"
            >
              <FileText className="size-4" />
              Monthly report
            </Link>
          ) : null}
          <div className="surface-2 flex gap-0.5 rounded-xl border p-1">
            {[7, 28].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                aria-pressed={days === d}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-[0.8125rem] font-medium transition-colors",
                  days === d ? "surface text-[var(--text)] shadow-[var(--shadow-1)]" : "text-2 hover:text-[var(--text)]",
                )}
              >
                {d} days
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="surface-2 flex w-fit max-w-full gap-0.5 overflow-x-auto rounded-xl border p-1" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => router.replace(`${pathname}?tab=${t.key}`, { scroll: false })}
            className={cn(
              "rounded-lg px-3.5 py-1.5 text-[0.8125rem] font-medium whitespace-nowrap transition-colors",
              tab === t.key ? "brand font-semibold" : "text-2 hover:text-[var(--text)]",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "executive" && money ? <Executive days={days} /> : null}
      {tab === "supervisor" ? <Supervisor days={days} /> : null}
      {tab === "quality" ? <Quality days={days} /> : null}
      {tab === "maintenance" ? <Maintenance days={days} /> : null}
    </div>
  );
}

// ===========================================================================

function Executive({ days }: { days: number }) {
  const q = useQuery({ queryKey: ["kpis", "executive", days], queryFn: () => api.roleKpis("executive", days) });
  if (q.isLoading) return <Skeleton className="h-80" />;
  if (q.error) return <ErrorNote message={`Could not load these KPIs: ${q.error.message}`} />;
  if (!q.data) return <Empty title="No data" />;
  const d = q.data;
  const recoveredDelta = d.recovered_this_month_paise - d.recovered_last_month_paise;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label={`Recoverable loss, ${days} days`}>
          <Money value={d.recoverable_loss_display} tone="loss" size="lg" />
        </Tile>
        <Tile label="Recoverable loss, this month">
          <Money value={d.month_recoverable_loss_display} tone="loss" size="lg" />
        </Tile>
        <Tile label="Recovered, this month vs last">
          <Money value={d.recovered_this_month_display} tone="recovered" size="lg" />
          <p className="text-3 mt-1 text-xs">
            last month {d.recovered_last_month_display}
            {d.recovered_last_month_paise ? (
              <Delta good={recoveredDelta >= 0} text={`${recoveredDelta >= 0 ? "+" : ""}${Math.round((recoveredDelta / d.recovered_last_month_paise) * 100)}%`} />
            ) : null}
          </p>
        </Tile>
        <Tile label="Quality PPM">
          <span className="figure text-2xl font-bold">{Math.round(d.ppm).toLocaleString("en-IN")}</span>
          <p className="text-3 mt-1 text-xs">
            previous {days} days {Math.round(d.previous_ppm).toLocaleString("en-IN")}
            {/* Lower is better for PPM, so a fall is the good direction. */}
            {d.previous_ppm ? <Delta good={d.ppm <= d.previous_ppm} text={`${d.ppm <= d.previous_ppm ? "" : "+"}${Math.round(((d.ppm - d.previous_ppm) / d.previous_ppm) * 100)}%`} /> : null}
          </p>
        </Tile>
      </div>

      <ProductivityTiles productivity={d.productivity} downtime={d.downtime_percent} />

      <Card className="rise">
        <CardHeader>
          <CardTitle>Top 5 recoverable losses, {days} days</CardTitle>
        </CardHeader>
        <CardContent>
          <Bars
            unit="₹"
            items={d.top_losses.map((l) => ({
              label: l.reason,
              sub: `${l.station} · ${l.event_count} stop${l.event_count === 1 ? "" : "s"}`,
              value: l.cost,
              display: l.cost_display,
            }))}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function Supervisor({ days }: { days: number }) {
  const q = useQuery({ queryKey: ["kpis", "supervisor", days], queryFn: () => api.roleKpis("supervisor", days) });
  if (q.isLoading) return <Skeleton className="h-96" />;
  if (q.error) return <ErrorNote message={`Could not load these KPIs: ${q.error.message}`} />;
  if (!q.data) return <Empty title="No data" />;
  const d = q.data;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label={`Plant OEE · ${d.shifts} shifts`}>
          <span className="figure text-2xl font-bold">{pct(d.plant.oee)}</span>
          <p className="text-3 mt-1 text-xs">
            A {pct(d.plant.availability)} × P {pct(d.plant.performance)} × Q {pct(d.plant.quality)}
          </p>
        </Tile>
        <Tile label="Produced">
          <span className="figure text-2xl font-bold">{d.plant.produced.toLocaleString("en-IN")}</span>
          <p className="text-3 mt-1 text-xs">parts, all lines</p>
        </Tile>
        <Tile label="Open actions">
          <span className="figure text-2xl font-bold">{d.open_actions}</span>
          <p className="mt-1 text-xs text-[var(--color-loss)]">{d.overdue_actions} overdue</p>
        </Tile>
        <Tile label="Downtime">
          <span className="figure text-2xl font-bold">
            {Math.round(d.downtime_by_reason.reduce((s, r) => s + r.minutes, 0)).toLocaleString("en-IN")}
          </span>
          <p className="text-3 mt-1 text-xs">station-minutes</p>
        </Tile>
      </div>
      <ProductivityTiles productivity={d.productivity} downtime={d.downtime_percent} />
      <DataNotes flags={d.plant.flags} />

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="rise">
          <CardHeader>
            <CardTitle>OEE per line</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    {["Line", "OEE", "A", "P", "Q", "Made", "Target", "vs target"].map((h) => (
                      <th key={h} className="label py-2 pr-3 text-left whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.lines.map((line) => (
                    <tr key={line.line} className="border-b last:border-0">
                      <td className="py-2.5 pr-3 font-semibold whitespace-nowrap">{line.line}</td>
                      <td className="tnum py-2.5 pr-3 font-bold">{pct(line.oee)}</td>
                      <td className="tnum text-2 py-2.5 pr-3">{pct(line.availability)}</td>
                      <td className="tnum text-2 py-2.5 pr-3">{pct(line.performance)}</td>
                      <td className="tnum text-2 py-2.5 pr-3">{pct(line.quality)}</td>
                      <td className="tnum py-2.5 pr-3">{line.produced.toLocaleString("en-IN")}</td>
                      <td className="tnum text-2 py-2.5 pr-3">{line.target.toLocaleString("en-IN")}</td>
                      <td className="tnum py-2.5 pr-3">{line.target ? pct(line.schedule_adherence) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        <Card className="rise">
          <CardHeader>
            <CardTitle>Plant OEE by day</CardTitle>
          </CardHeader>
          <CardContent>
            <Trend
              data={d.trend.map((t) => ({ date: t.date, value: t.oee }))}
              format={(v) => pct(v)}
              domain={[0, 1]}
              label="OEE"
            />
          </CardContent>
        </Card>
      </div>

      <Card className="rise">
        <CardHeader>
          <CardTitle>Downtime minutes by reason</CardTitle>
        </CardHeader>
        <CardContent>
          <Bars
            unit="min"
            items={d.downtime_by_reason.slice(0, 10).map((r) => ({
              label: r.reason,
              sub: `${r.events} stop${r.events === 1 ? "" : "s"} · ${r.cost_display}`,
              value: r.minutes,
              display: `${Math.round(r.minutes)} min`,
              badge: r.recoverability === "S" ? "Structural" : undefined,
            }))}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function Quality({ days }: { days: number }) {
  const q = useQuery({ queryKey: ["kpis", "quality", days], queryFn: () => api.roleKpis("quality", days) });
  if (q.isLoading) return <Skeleton className="h-96" />;
  if (q.error) return <ErrorNote message={`Could not load these KPIs: ${q.error.message}`} />;
  if (!q.data) return <Empty title="No data" />;
  const d = q.data;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="PPM">
          <span className="figure text-2xl font-bold">{Math.round(d.ppm).toLocaleString("en-IN")}</span>
          <p className="text-3 mt-1 text-xs">
            previous {Math.round(d.previous_ppm).toLocaleString("en-IN")}
            {d.previous_ppm ? <Delta good={d.ppm <= d.previous_ppm} text={`${d.ppm <= d.previous_ppm ? "" : "+"}${Math.round(((d.ppm - d.previous_ppm) / d.previous_ppm) * 100)}%`} /> : null}
          </p>
        </Tile>
        <Tile label="First-pass yield">
          <span className="figure text-2xl font-bold">{pct(d.first_pass_yield, 1)}</span>
          <p className="text-3 mt-1 text-xs">rework does not count as first-pass</p>
        </Tile>
        <Tile label="Inspected">
          <span className="figure text-2xl font-bold">{d.inspected.toLocaleString("en-IN")}</span>
          <p className="mt-1 text-xs text-[var(--color-loss)]">{d.rejected.toLocaleString("en-IN")} rejected</p>
        </Tile>
        <Tile label="Scrap cost">
          <Money value={d.scrap_cost_display} tone="loss" size="lg" />
        </Tile>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card className="rise">
          <CardHeader>
            <CardTitle>Top 3 defects — and whether they are rising</CardTitle>
          </CardHeader>
          <CardContent>
            {d.top_defects.length === 0 ? (
              <p className="text-3 py-6 text-center text-sm">No defects recorded.</p>
            ) : (
              <ul className="divide-y">
                {d.top_defects.map((defect) => {
                  const rising = defect.ppm > defect.previous_ppm;
                  return (
                    <li key={defect.code} className="flex items-center justify-between gap-3 py-3">
                      <span>
                        <span className="block font-semibold">{defect.label}</span>
                        <span className="text-3 text-xs">{defect.count} parts</span>
                      </span>
                      <span className="text-right">
                        <span className="tnum block font-bold">{Math.round(defect.ppm).toLocaleString("en-IN")} PPM</span>
                        <span className={cn("flex items-center justify-end gap-0.5 text-xs font-semibold", rising ? "text-[var(--color-loss)]" : "text-[var(--color-running)]")}>
                          {rising ? <ArrowUpRight className="size-3.5" /> : <ArrowDownRight className="size-3.5" />}
                          {rising ? "rising" : "falling"} from {Math.round(defect.previous_ppm).toLocaleString("en-IN")}
                        </span>
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card className="rise">
          <CardHeader>
            <CardTitle>PPM by day</CardTitle>
          </CardHeader>
          <CardContent>
            <Trend
              data={d.trend.map((t) => ({ date: t.date, value: t.ppm }))}
              format={(v) => Math.round(v).toLocaleString("en-IN")}
              label="PPM"
            />
          </CardContent>
        </Card>
      </div>

      <Card className="rise">
        <CardHeader>
          <CardTitle>Rejects by machine</CardTitle>
        </CardHeader>
        <CardContent>
          <Bars
            unit="parts"
            items={d.by_machine.slice(0, 10).map((m) => ({
              label: m.station,
              sub: `${Math.round(m.ppm).toLocaleString("en-IN")} PPM · scrap ${m.scrap_cost_display}`,
              value: m.rejected,
              display: `${m.rejected} rejected`,
            }))}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function Maintenance({ days }: { days: number }) {
  const q = useQuery({ queryKey: ["kpis", "maintenance", days], queryFn: () => api.roleKpis("maintenance", days) });
  if (q.isLoading) return <Skeleton className="h-96" />;
  if (q.error) return <ErrorNote message={`Could not load these KPIs: ${q.error.message}`} />;
  if (!q.data) return <Empty title="No data" />;
  const d = q.data;

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Tile label={`Breakdowns, ${days} days`}>
          <span className="figure text-2xl font-bold">{d.breakdowns}</span>
          <p className="text-3 mt-1 text-xs">stops whose reason is flagged as a breakdown</p>
        </Tile>
        <Tile label="MTBF">
          <span className="figure text-2xl font-bold">{duration(d.mtbf_minutes)}</span>
          <p className="text-3 mt-1 text-xs">run time between breakdowns</p>
        </Tile>
        <Tile label="MTTR">
          <span className="figure text-2xl font-bold">{duration(d.mttr_minutes)}</span>
          <p className="text-3 mt-1 text-xs">average time to repair</p>
        </Tile>
      </div>

      <Card className="rise">
        <CardHeader>
          <CardTitle>Machines, ranked by what breakdowns cost</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b">
                  {["Machine", "Breakdowns", "Down", "Cost", "MTBF", "MTTR", "Availability"].map((h) => (
                    <th key={h} className="label py-2 pr-4 text-left whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.machines.map((m) => (
                  <tr key={m.station} className="border-b last:border-0">
                    <td className="py-2.5 pr-4 whitespace-nowrap">
                      <span className="font-semibold">{m.station}</span>
                      {m.is_bottleneck ? <Badge variant="stopped" className="ml-2">Bottleneck</Badge> : null}
                    </td>
                    <td className="tnum py-2.5 pr-4 font-bold">{m.breakdowns}</td>
                    <td className="tnum text-2 py-2.5 pr-4">{m.breakdowns ? `${Math.round(m.breakdown_minutes)} min` : "—"}</td>
                    <td className="py-2.5 pr-4">{m.breakdowns ? <Money value={m.breakdown_cost_display} tone="loss" size="sm" /> : <span className="text-3">—</span>}</td>
                    <td className="tnum text-2 py-2.5 pr-4">{duration(m.mtbf_minutes)}</td>
                    <td className="tnum text-2 py-2.5 pr-4">{duration(m.mttr_minutes)}</td>
                    <td className="tnum text-2 py-2.5 pr-4">{pct(m.availability)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-3 mt-3 text-xs">
            &ldquo;—&rdquo; means no breakdowns in the period — not a time of zero.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

// ===========================================================================

function Tile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rise surface elev-1 rounded-[var(--radius-card)] border p-4">
      <p className="label mb-2">{label}</p>
      {children}
    </div>
  );
}

/**
 * Output per hour of people and of machines, and the share of planned time
 * lost. Recoverable and structural are shown apart and never merged into one
 * alarming figure: only the recoverable part is anyone's to fix.
 */
function ProductivityTiles({
  productivity,
  downtime,
}: {
  productivity?: Productivity;
  downtime?: DowntimePercent;
}) {
  // An API that predates these figures simply leaves the row out.
  if (!productivity && !downtime) return null;
  const rate = (value: number | null | undefined) =>
    value === null || value === undefined ? "—" : value.toLocaleString("en-IN", { maximumFractionDigits: 1 });
  const hours = (value: number) => Math.round(value).toLocaleString("en-IN");

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {productivity ? (
        <>
          <Tile label="Good parts per man-hour">
            <span className="figure text-2xl font-bold">{rate(productivity.per_man_hour)}</span>
            <p className="text-3 mt-1 text-xs">
              {productivity.per_man_hour === null
                ? "no man-hours booked in this period"
                : `${productivity.good_parts.toLocaleString("en-IN")} good parts · ${hours(productivity.man_hours)} man-hours`}
            </p>
          </Tile>
          <Tile label="Good parts per machine-hour">
            <span className="figure text-2xl font-bold">{rate(productivity.per_machine_hour)}</span>
            <p className="text-3 mt-1 text-xs">
              {productivity.per_machine_hour === null
                ? "no machine run time in this period"
                : `${hours(productivity.machine_hours)} machine-hours`}
            </p>
          </Tile>
        </>
      ) : null}
      {downtime ? (
        <Tile label="Downtime, share of planned time">
          <span className="figure text-2xl font-bold">{pct(downtime.total, 1)}</span>
          <div
            className="surface-inset mt-2 flex h-2 overflow-hidden rounded-full"
            role="img"
            aria-label={`Recoverable ${pct(downtime.recoverable, 1)}, structural ${pct(downtime.structural, 1)}`}
          >
            <div style={{ width: `${Math.min(100, downtime.recoverable * 100)}%`, background: "var(--color-loss)" }} />
            <div style={{ width: `${Math.min(100, downtime.structural * 100)}%`, background: "var(--color-structural)" }} />
          </div>
          <p className="mt-1.5 text-xs">
            <span className="font-semibold text-[var(--color-loss)]">{pct(downtime.recoverable, 1)} recoverable</span>
            <span className="text-3"> · {pct(downtime.structural, 1)} structural</span>
          </p>
        </Tile>
      ) : null}
    </div>
  );
}

function Delta({ good, text }: { good: boolean; text: string }) {
  return (
    <span className={cn("ml-1.5 font-semibold", good ? "text-[var(--color-running)]" : "text-[var(--color-loss)]")}>
      {text}
    </span>
  );
}

/** One measure across categories: one hue, a direct label on every bar. */
function Bars({
  items,
  unit,
}: {
  items: Array<{ label: string; sub?: string; value: number; display: string; badge?: string }>;
  unit: string;
}) {
  if (items.length === 0) return <p className="text-3 py-6 text-center text-sm">Nothing recorded.</p>;
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <ol className="space-y-2.5" aria-label={`Ranked by ${unit}`}>
      {items.map((item, index) => (
        <li key={`${item.label}-${index}`}>
          <div className="mb-1 flex items-baseline justify-between gap-3">
            <span className="flex min-w-0 items-baseline gap-2">
              <span className="text-3 tnum w-4 shrink-0 text-xs font-semibold">{index + 1}</span>
              <span className="truncate text-sm font-medium">{item.label}</span>
              {item.badge ? <Badge variant="structural">{item.badge}</Badge> : null}
              {item.sub ? <span className="text-3 hidden shrink-0 text-xs sm:inline">{item.sub}</span> : null}
            </span>
            <span className="tnum shrink-0 text-sm font-semibold">{item.display}</span>
          </div>
          <div className="surface-inset h-2 overflow-hidden rounded-full">
            <div className="h-full rounded-full" style={{ width: `${Math.max(2, (item.value / max) * 100)}%`, background: MARK }} />
          </div>
        </li>
      ))}
    </ol>
  );
}

/** A single series over time — no legend box; the card title names it. */
function Trend({
  data,
  format,
  label,
  domain,
}: {
  data: Array<{ date: string; value: number }>;
  format: (v: number) => string;
  label: string;
  domain?: [number, number];
}) {
  if (data.length === 0) return <p className="text-3 py-10 text-center text-sm">No closed shifts in this period.</p>;
  const day = (iso: string) => new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -4 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="date" tickFormatter={day} minTickGap={24} tick={{ fill: "var(--text-3)", fontSize: 11 }} tickLine={false} axisLine={false} />
        <YAxis tickFormatter={format} domain={domain ?? ["auto", "auto"]} width={56} tick={{ fill: "var(--text-3)", fontSize: 11 }} tickLine={false} axisLine={false} />
        <Tooltip
          cursor={{ stroke: GRID }}
          content={({ active, payload }) =>
            active && payload?.length ? (
              <div className="surface elev-3 rounded-lg border px-3 py-2 text-xs">
                <p className="label mb-1">{day(String(payload[0].payload.date))}</p>
                <p>
                  <span className="text-2">{label} </span>
                  <span className="tnum font-semibold">{format(Number(payload[0].value))}</span>
                </p>
              </div>
            ) : null
          }
        />
        {/* Straight segments: each point is a separate day, and a smoothed
            curve would imply readings between them that were never taken. */}
        <Line type="linear" dataKey="value" stroke={MARK} strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function DataNotes({ flags }: { flags: string[] }) {
  const notes = flags.map((flag) =>
    flag === "PERFORMANCE_PARTIAL"
      ? "Some stations ran parts with no cycle time, so Performance is measured on the rest."
      : flag.startsWith("PERFORMANCE_OVER_100:")
        ? `Faster than the cycle time on ${flag.split(":")[1]} — check those cycle times or counts.`
        : flag === "NO_PLANNED_TIME"
          ? "No planned time in this period."
          : flag,
  );
  if (notes.length === 0) return null;
  return (
    <div className="text-3 flex flex-wrap gap-x-4 gap-y-1 px-1 text-xs">
      {notes.map((note) => (
        <span key={note} className="flex items-center gap-1">
          <ArrowRight className="size-3" />
          {note}
        </span>
      ))}
    </div>
  );
}

function pct(value: number, digits = 0) {
  return `${(value * 100).toFixed(digits)}%`;
}

function duration(minutes: number | null) {
  if (minutes === null) return "—";
  if (minutes >= 1440) return `${(minutes / 1440).toFixed(1)} d`;
  if (minutes >= 90) return `${(minutes / 60).toFixed(1)} h`;
  return `${Math.round(minutes)} min`;
}
