"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight, Clock, TrendingDown, TrendingUp } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import { formatInr, formatMinutes } from "@/lib/format";
import { Money, MoneyCounter } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import { LossTrend, RankedBars, RecoveredTrend, Sparkline } from "@/components/charts";
import { cn } from "@/lib/utils";

/**
 * Screen 8 — Owner Home.
 *
 * §7 is unusually prescriptive here, and worth honouring exactly:
 *
 *   "An owner opening the app sees four numbers and nothing else: Recovered
 *    Profit yesterday, Lost Profit yesterday, Actions completed, running ROI.
 *    Every number is a door — tap it and you walk down to the losses, the
 *    machine, the events, the Action fixing it."
 *
 * So the four numbers come first, at a size nothing else on the page competes
 * with, and each one is a link. The charts sit below the fold of attention:
 * they answer "is this getting better?", which is the owner's second question,
 * never their first.
 *
 * Recovered Profit takes the feature tile. Four equal cards make the owner choose
 * where to look; the one number that says the product is working should not be
 * a choice. The other three stay identical to each other so the hierarchy is
 * one step deep, not four.
 *
 * There is deliberately no OEE on this page. The owner should never need to
 * understand OEE to use this product.
 */
export default function OwnerHome() {
  const home = useQuery({ queryKey: ["owner-home"], queryFn: () => api.ownerHome() });
  const trend = useQuery({ queryKey: ["trend"], queryFn: () => api.trend(28) });

  if (home.isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
        <Skeleton className="h-80" />
      </div>
    );
  }

  // A failed load must never read as "nothing happened yet": an owner told
  // there is no data stops opening the app.
  if (home.error) {
    const forbidden = home.error instanceof ApiError && home.error.status === 403;
    return (
      <div className="space-y-4">
        <h1 className="display text-[2rem] font-bold">Money</h1>
        <ErrorNote
          message={
            forbidden
              ? "These figures are for the plant owner. Your role cannot see them."
              : `Could not load the money figures: ${home.error.message}`
          }
        />
        {!forbidden ? (
          <Button size="lg" variant="outline" onClick={() => void home.refetch()}>
            Try again
          </Button>
        ) : null}
      </div>
    );
  }

  if (!home.data) {
    return <Empty title="No data yet" hint="Numbers appear once the first shift closes." />;
  }

  const d = home.data;
  const loss = trend.data?.loss ?? [];

  // Week-on-week movement, for the direction chip on the loss tile.
  const lastWeek = loss.slice(-7).reduce((s, p) => s + p.recoverable, 0);
  const priorWeek = loss.slice(-14, -7).reduce((s, p) => s + p.recoverable, 0);
  const delta = priorWeek > 0 ? (lastWeek - priorWeek) / priorWeek : 0;

  const completion =
    (d.actions_completed / Math.max(1, d.actions_completed + d.actions_open)) * 100;

  return (
    <div className="space-y-4">
      <header className="rise flex flex-wrap items-end justify-between gap-4 px-1 pb-1">
        <div>
          <h1 className="display text-[2rem] font-bold">Money</h1>
          <p className="text-2 mt-1 text-sm">
            {/* `as_of` is today; every figure on this screen is for the day
                before it. Built from the date parts rather than `new Date(iso)`,
                which parses as UTC midnight and can land on the wrong day. */}
            Yesterday,{" "}
            {(() => {
              const [y, m, day] = d.as_of.split("-").map(Number);
              return new Date(y, m - 1, day - 1).toLocaleDateString("en-IN", {
                weekday: "long",
                day: "numeric",
                month: "long",
              });
            })()}
          </p>
        </div>
        <Link
          href="/meeting"
          className="text-2 surface flex items-center gap-1.5 rounded-full border px-4 py-2.5 text-sm font-medium transition-colors hover:surface-2 hover:text-[var(--text)]"
        >
          Morning meeting
          <ArrowRight className="size-3.5" />
        </Link>
      </header>

      {/* ---- The four numbers ------------------------------------------- */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          href="/actions?state=verified"
          label="Recovered profit"
          hint="Verified, sustained fixes only"
          tone="feature"
          delay={0}
        >
          <MoneyCounter
            paise={d.recovered_profit_paise}
            display={d.recovered_profit_display}
            tone="recovered"
            size="xl"
          />
          {trend.data?.recovered?.length ? (
            <div className="mt-3 -mb-1">
              <Sparkline
                data={trend.data.recovered}
                dataKey="cumulative"
                tone="recovered"
                height={34}
              />
            </div>
          ) : null}
        </StatTile>

        <StatTile
          href="/meeting"
          label="Lost yesterday"
          hint="Recoverable losses only"
          delay={60}
          chip={
            loss.length >= 14 ? (
              <Badge variant={delta <= 0 ? "recovered" : "warn"} className="gap-1">
                {delta <= 0 ? (
                  <TrendingDown className="size-3" />
                ) : (
                  <TrendingUp className="size-3" />
                )}
                {Math.abs(delta * 100).toFixed(0)}% wk
              </Badge>
            ) : undefined
          }
        >
          <MoneyCounter
            paise={d.lost_yesterday_paise}
            display={d.lost_yesterday_display}
            tone="loss"
            size="xl"
          />
          {loss.length ? (
            <div className="mt-3 -mb-1">
              <Sparkline data={loss} dataKey="recoverable" tone="loss" height={34} />
            </div>
          ) : null}
        </StatTile>

        <StatTile
          href="/actions?state=open"
          label="Actions"
          hint={`${d.actions_open} still open`}
          delay={120}
          chip={
            <Badge variant="neutral">{completion.toFixed(0)}% done</Badge>
          }
        >
          <div className="flex items-baseline gap-2">
            <span className="figure text-[2.5rem] font-bold leading-none">
              {d.actions_completed}
            </span>
            <span className="text-3 text-sm">completed</span>
          </div>
          <div className="surface-inset mt-5 h-2 overflow-hidden rounded-full">
            <div
              className="h-full rounded-full bg-[var(--color-brand)] transition-[width] duration-700 ease-out"
              style={{ width: `${completion}%` }}
            />
          </div>
        </StatTile>

        <StatTile
          href="/actions?state=verified"
          label="Running ROI"
          hint="Recovered ÷ fees paid"
          delay={180}
          chip={d.running_roi >= 1 ? <Badge variant="recovered">Paid back</Badge> : undefined}
        >
          <div className="flex items-baseline gap-1">
            <span className="figure text-[2.5rem] font-bold leading-none">
              {d.running_roi.toFixed(1)}
            </span>
            <span className="figure text-3 text-2xl font-bold">×</span>
          </div>
          <p className="text-3 mt-3 text-xs leading-relaxed">
            {d.running_roi >= 1
              ? "The system has paid for itself."
              : "Savings compound as fixes are verified."}
          </p>
        </StatTile>
      </div>

      {/* ---- Is it getting better? -------------------------------------- */}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="rise xl:col-span-2" style={{ animationDelay: "240ms" }}>
          <CardHeader className="justify-between">
            <div>
              <CardTitle>Recoverable loss, last 28 days</CardTitle>
              <p className="text-3 mt-1 text-xs normal-case tracking-normal">
                Structural loss is shown dashed and never added in — it is real
                money, and not money anyone can get back.
              </p>
            </div>
          </CardHeader>
          <CardContent>
            {trend.isLoading ? (
              <Skeleton className="h-60" />
            ) : trend.error ? (
              <ErrorNote message={`Could not load the trend: ${trend.error.message}`} />
            ) : (
              <LossTrend data={loss} />
            )}
          </CardContent>
        </Card>

        <Card tone="feature" className="rise" style={{ animationDelay: "300ms" }}>
          <CardHeader>
            <CardTitle>Recovered profit, cumulative</CardTitle>
          </CardHeader>
          <CardContent>
            <RecoveredTrend data={trend.data?.recovered ?? []} />
            <p className="text-3 mt-3 text-xs leading-relaxed">
              Credited only after a fix has held for two consecutive weeks, never
              counted twice, and never below zero.
            </p>
          </CardContent>
        </Card>
      </div>

      {/* ---- Where it went ---------------------------------------------- */}
      <div className="grid items-start gap-4 xl:grid-cols-3">
        <Card className="rise xl:col-span-2" style={{ animationDelay: "360ms" }}>
          <CardHeader className="justify-between">
            <CardTitle>Yesterday&apos;s biggest recoverable losses</CardTitle>
            <Link
              href="/meeting"
              className="text-brand ml-auto flex items-center gap-1 text-xs font-semibold hover:underline"
            >
              Full table
              <ArrowUpRight className="size-3" />
            </Link>
          </CardHeader>
          <CardContent>
            {d.top_losses.length === 0 ? (
              <p className="text-3 py-10 text-center text-sm">
                Nothing recoverable logged yesterday.
              </p>
            ) : (
              <RankedBars
                items={d.top_losses.map((line) => ({
                  label: line.reason,
                  sublabel: `${line.station} · ${formatMinutes(line.minutes)}`,
                  value: line.cost,
                  display: line.cost_display,
                }))}
              />
            )}
          </CardContent>
        </Card>

        <div className="rise space-y-4" style={{ animationDelay: "420ms" }}>
          <Card>
            <CardHeader>
              <CardTitle>Month so far</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <div>
                <p className="text-3 mb-1 text-xs">Recoverable</p>
                <Money value={d.lost_month_to_date_display} tone="loss" size="lg" />
              </div>
              <div>
                <div className="mb-1 flex items-center gap-2">
                  <p className="text-3 text-xs">Annualised</p>
                  {/* §8.1: "always labelled as a projection, never as fact". */}
                  <Badge variant="warn">Projection</Badge>
                </div>
                <Money value={d.annualised_projection_display} tone="loss" size="lg" />
                <p className="text-3 mt-1.5 text-xs leading-relaxed">
                  Current 4-week run-rate × 13. Not a forecast of what you will
                  lose — a measure of what is on the table.
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-1.5">
                <Clock className="size-3.5" />
                Structural
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Money
                value={formatInr(d.structural_yesterday_paise)}
                tone="structural"
                size="lg"
              />
              <p className="text-3 mt-2 text-xs leading-relaxed">
                Planned maintenance, power cuts, no-order time. Reported
                separately and never added to the recoverable figure — mixing
                them is how a number stops being trusted.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

/**
 * One of the four. A link, because §7 insists every number is a door.
 *
 * The hint row at the foot doubles as the affordance: it is the only part that
 * moves on hover, so the whole tile reads as clickable without a button.
 */
function StatTile({
  href,
  label,
  hint,
  chip,
  delay,
  tone = "plain",
  children,
}: {
  href: string;
  label: string;
  hint: string;
  chip?: React.ReactNode;
  delay: number;
  tone?: "plain" | "feature";
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      style={{ animationDelay: `${delay}ms` }}
      className={cn(
        "rise group elev-2 relative flex flex-col overflow-hidden rounded-[var(--radius-card)] border p-5",
        "transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-[var(--shadow-3)]",
        tone === "feature" ? "rail border-rail" : "surface",
      )}
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <span className="label">{label}</span>
        {chip}
      </div>
      <div className="flex-1">{children}</div>
      <span className="text-3 mt-4 flex items-center gap-1 text-xs transition-colors group-hover:text-[var(--text)]">
        {hint}
        <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}
