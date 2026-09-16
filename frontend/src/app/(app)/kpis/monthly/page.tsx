"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Printer } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";

/**
 * The monthly owner report (§4.4, §5.6).
 *
 *   "The owner's monthly report leads with: total ₹ recovered this month,
 *    cumulative ₹ recovered vs annual fee (the running ROI), and the top open
 *    opportunities. Nobody cancels a system whose home screen shows 19× ROI."
 *
 * Laid out as a document, in that order, because it is read once a month and
 * often printed for a partners' meeting.
 */

function lastMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function MonthlyReportPage() {
  const [month, setMonth] = useState(lastMonth());
  const q = useQuery({ queryKey: ["monthly", month], queryFn: () => api.monthlyReport(month) });

  const title = new Date(`${month}-01T00:00:00`).toLocaleDateString("en-IN", {
    month: "long",
    year: "numeric",
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3 print:hidden">
        <Link href="/kpis?tab=executive" className="text-2 flex items-center gap-1.5 text-sm hover:text-[var(--text)]">
          <ArrowLeft className="size-4" />
          KPIs
        </Link>
        <div className="flex items-center gap-2">
          <input
            type="month"
            value={month}
            max={new Date().toISOString().slice(0, 7)}
            onChange={(e) => e.target.value && setMonth(e.target.value)}
            aria-label="Month"
            className="surface h-10 rounded-xl border px-3 text-sm"
          />
          <Button variant="outline" onClick={() => window.print()}>
            <Printer className="size-4" />
            Print
          </Button>
        </div>
      </div>

      {q.isLoading ? (
        <Skeleton className="h-[40rem]" />
      ) : q.error ? (
        <ErrorNote message={(q.error as Error).message} />
      ) : !q.data ? (
        <Empty title="No report" />
      ) : (
        <Report r={q.data} title={title} />
      )}
    </div>
  );
}

function Report({ r, title }: { r: import("@/types/api").MonthlyReport; title: string }) {
  const change = r.change_vs_previous;
  const maxWeek = Math.max(...r.weekly_trend.map((w) => w.recoverable_paise), 1);

  return (
    <article className="space-y-5">
      <header className="rise">
        <p className="label mb-1.5">Monthly owner report · {r.plant}</p>
        <h1 className="display text-[2rem] font-bold">{title}</h1>
        <p className="text-2 mt-1 text-sm">
          {formatDay(r.period.start)} – {formatDay(r.period.end)}
        </p>
      </header>

      {/* ---- 1. What the system has paid back --------------------------- */}
      <div className="rise rail border-rail grid gap-5 rounded-[var(--radius-card)] border p-6 sm:grid-cols-3">
        <div>
          <p className="label mb-1.5">Recovered this month</p>
          <Money value={r.recovered_this_month_display} tone="recovered" size="xl" />
        </div>
        <div>
          <p className="label mb-1.5">Recovered to date</p>
          <Money value={r.recovered_cumulative_display} tone="recovered" size="xl" />
          <p className="text-rail-2 mt-1 text-xs">against {r.fees_cumulative_display} in fees</p>
        </div>
        <div>
          <p className="label mb-1.5">Running ROI</p>
          <span className="figure text-[2.5rem] leading-none font-bold">
            {r.running_roi.toFixed(1)}
            <span className="text-2xl">×</span>
          </span>
          <p className="text-rail-2 mt-1 text-xs">
            {r.running_roi >= 1 ? "The system has paid for itself." : "Verified savings compound month on month."}
          </p>
        </div>
      </div>

      {/* ---- 2. Where the next money is --------------------------------- */}
      <Card className="rise">
        <CardHeader>
          <CardTitle>Top open opportunities</CardTitle>
          {/* Open as of today, not as of the month's end: this is the list the
              owner acts on next, whichever month the report covers. */}
          <span className="text-3 ml-auto text-xs normal-case">open today</span>
        </CardHeader>
        <CardContent>
          {r.top_opportunities.length === 0 ? (
            <p className="text-3 text-sm">No open actions.</p>
          ) : (
            <ul className="divide-y">
              {r.top_opportunities.map((a) => (
                <li key={a.reference} className="flex flex-wrap items-center justify-between gap-3 py-3">
                  <span className="min-w-0">
                    <span className="block font-semibold">{a.title}</span>
                    <span className="text-3 text-xs">
                      {a.reference} · {a.state.replace(/_/g, " ")}
                      {a.owner ? ` · ${a.owner}` : ""}
                      {a.due_date ? ` · due ${formatDay(a.due_date)}` : ""}
                    </span>
                  </span>
                  <Money value={a.linked_cost_display} tone="loss" size="md" />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ---- 3. The loss, and whether it is falling ---------------------- */}
      <Card className="rise">
        <CardHeader className="flex-wrap justify-between gap-2">
          <CardTitle>Recoverable loss</CardTitle>
          {change !== null ? (
            <Badge variant={change <= 0 ? "recovered" : "warn"}>
              {change <= 0 ? "" : "+"}
              {Math.round(change * 100)}% vs {r.previous_recoverable_loss_display} last month
            </Badge>
          ) : (
            <Badge variant="neutral">No previous month to compare</Badge>
          )}
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <p className="label mb-1">This month</p>
              <Money value={r.recoverable_loss_display} tone="loss" size="lg" />
            </div>
            <div>
              <p className="label mb-1">Structural, never added in</p>
              <Money value={r.structural_loss_display} tone="structural" size="lg" />
            </div>
            <div>
              <p className="label mb-1 flex items-center gap-1.5">
                Annualised <Badge variant="warn">Projection</Badge>
              </p>
              <Money value={r.annualised_projection_display} tone="loss" size="lg" />
              <p className="text-3 mt-1 text-xs">last 4 weeks × 13 — not a forecast</p>
            </div>
          </div>

          <div>
            <p className="label mb-2">By week</p>
            <ol className="space-y-2">
              {r.weekly_trend.map((w) => (
                <li key={w.week_start} className="grid grid-cols-[6.5rem_1fr_auto] items-center gap-3 text-sm">
                  <span className="text-2 tnum text-xs">
                    wk of {formatDay(w.week_start)}
                    {w.partial ? "*" : ""}
                  </span>
                  <span className="surface-inset h-2 overflow-hidden rounded-full">
                    <span
                      className="block h-full rounded-full"
                      style={{ width: `${Math.max(2, (w.recoverable_paise / maxWeek) * 100)}%`, background: "var(--color-loss)" }}
                    />
                  </span>
                  <span className="money text-sm font-semibold">{w.recoverable_display}</span>
                </li>
              ))}
            </ol>
            {r.weekly_trend.some((w) => w.partial) ? (
              <p className="text-3 mt-2 text-xs">* part of the week falls outside the month.</p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {/* ---- 4. What keeps coming back ----------------------------------- */}
      <Card className="rise">
        <CardHeader>
          <CardTitle>Top recurring problems</CardTitle>
        </CardHeader>
        <CardContent>
          {r.top_recurring.length === 0 ? (
            <p className="text-3 text-sm">Nothing repeated three times or more on one machine this month.</p>
          ) : (
            <ul className="divide-y">
              {r.top_recurring.map((line) => (
                <li key={`${line.station}-${line.reason}`} className="flex items-center justify-between gap-3 py-3">
                  <span>
                    <span className="block font-semibold">{line.reason}</span>
                    <span className="text-3 text-xs">
                      {line.station} · {line.event_count} times · {Math.round(line.minutes)} min
                    </span>
                  </span>
                  <Money value={line.cost_display} tone="loss" size="md" />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ---- 5. Is the discipline holding? ------------------------------- */}
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Actions" value={`${r.actions.closed} closed`} sub={`${r.actions.raised} raised · ${r.actions.verified} verified`} />
        <Stat
          label="Shifts closed clean"
          value={r.discipline.closed_clean_share === null ? "—" : `${Math.round(r.discipline.closed_clean_share * 100)}%`}
          sub={`${r.discipline.shifts_closed} shifts · ${r.discipline.closed_with_override} needed an owner override`}
          warn={r.discipline.closed_clean_share !== null && r.discipline.closed_clean_share < 0.9}
        />
        <Stat label="Quality" value={`${Math.round(r.quality_ppm).toLocaleString("en-IN")} PPM`} sub="rejected per million inspected" />
      </div>
      <p className="text-3 text-xs leading-relaxed">
        Shifts closed without an override is the early warning that data-entry discipline is slipping
        (§12, risk 1). Savings are credited only after a fix has held for two consecutive weeks, never
        counted twice, and never below zero.
      </p>
    </article>
  );
}

function Stat({ label, value, sub, warn }: { label: string; value: string; sub: string; warn?: boolean }) {
  return (
    <div className="rise surface elev-1 rounded-[var(--radius-card)] border p-4">
      <p className="label mb-1.5">{label}</p>
      <p className={cn("figure text-2xl font-bold", warn && "text-[var(--color-loss)]")}>{value}</p>
      <p className="text-3 mt-1 text-xs">{sub}</p>
    </div>
  );
}

function formatDay(iso: string) {
  return new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
