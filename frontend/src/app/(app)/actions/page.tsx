"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Ban, ClipboardList, Repeat2, TriangleAlert } from "lucide-react";
import { api } from "@/lib/api";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import type { Action } from "@/types/api";

/**
 * Screen 9 — the Actions Board.
 *
 * Columns are PDCA states, because §5.4 maps the Action lifecycle onto PDCA
 * exactly — a plant that already runs PDCA finds nothing foreign here.
 *
 * Ranked by money inside each column, never by age: the board answers "what is
 * costing us most that we haven't fixed", not "what is oldest".
 */
const COLUMNS = [
  {
    key: "open",
    label: "Open",
    phase: "PLAN",
    hint: "Problem defined, cost attached",
    states: ["open", "root_cause", "planned"],
    accent: "var(--color-loss)",
  },
  {
    key: "in_progress",
    label: "In progress",
    phase: "DO",
    hint: "The fix is being executed",
    states: ["in_progress"],
    accent: "var(--color-warn)",
  },
  {
    key: "monitoring",
    label: "Monitoring",
    phase: "CHECK",
    hint: "Did the loss actually stop?",
    states: ["monitoring"],
    accent: "var(--color-accent)",
  },
  {
    key: "verified",
    label: "Verified",
    phase: "ACT",
    hint: "Sustained — savings credited",
    states: ["verified", "reopened"],
    accent: "var(--color-recovered)",
  },
] as const;

/**
 * `?state=` — the owner's numbers link here (§7: every number is a door), so
 * the door has to open on the thing the number counted. `open` means not yet
 * closed, exactly as the API counts it; `cancelled` is its own list, because a
 * cancelled action in a PDCA column would read as work still in the loop.
 */
const STATE_FILTERS = {
  all: { label: "All", states: null },
  open: { label: "Still open", states: ["open", "root_cause", "planned", "in_progress", "reopened"] },
  monitoring: { label: "Monitoring", states: ["monitoring"] },
  verified: { label: "Verified", states: ["verified"] },
  cancelled: { label: "Cancelled", states: ["cancelled"] },
} as const satisfies Record<string, { label: string; states: readonly string[] | null }>;
type StateFilter = keyof typeof STATE_FILTERS;

const GRID_COLS = ["", "xl:grid-cols-1", "xl:grid-cols-2", "xl:grid-cols-3", "xl:grid-cols-4"];

export default function ActionsBoardPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[32rem]" />}>
      <ActionsBoard />
    </Suspense>
  );
}

function ActionsBoard() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [filter, setFilter] = useState<"all" | "rca" | "overdue">("all");
  const actions = useQuery({ queryKey: ["actions"], queryFn: () => api.actions() });

  const requested = params.get("state") ?? "all";
  const stateFilter: StateFilter = requested in STATE_FILTERS ? (requested as StateFilter) : "all";
  const setStateFilter = (key: StateFilter) =>
    router.replace(key === "all" ? pathname : `${pathname}?state=${key}`, { scroll: false });

  if (actions.isLoading) return <Skeleton className="h-[32rem]" />;
  if (actions.error) {
    return (
      <div className="space-y-4">
        <h1 className="display text-[1.75rem] font-bold">Actions</h1>
        <ErrorNote message={`Could not load actions: ${actions.error.message}`} />
        <Button size="lg" variant="outline" onClick={() => void actions.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  const everything = actions.data ?? [];
  const cancelled = everything.filter((a) => a.state === "cancelled");
  const all = everything.filter((a) => a.state !== "cancelled");

  const allowedStates: readonly string[] | null = STATE_FILTERS[stateFilter].states;
  const visible = (stateFilter === "cancelled" ? cancelled : all).filter(
    (a) =>
      (!allowedStates || allowedStates.includes(a.state)) &&
      (filter === "rca" ? a.routed_to_rca : filter === "overdue" ? a.is_overdue : true),
  );
  const columns = COLUMNS.filter(
    (column) => !allowedStates || column.states.some((state) => allowedStates.includes(state)),
  );
  const recovered = all
    .filter((a) => a.state === "verified")
    .reduce((sum, a) => sum + a.recovered_profit_paise, 0);
  const overdue = all.filter((a) => a.is_overdue).length;

  return (
    <div className="space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5">Improvement loop</p>
          <h1 className="display text-[1.75rem] font-bold">Actions</h1>
          <p className="text-2 mt-1 text-sm">
            {all.length} tracked
            {overdue ? (
              <span className="text-[var(--color-loss)]"> · {overdue} overdue</span>
            ) : null}
            {cancelled.length ? <span> · {cancelled.length} cancelled</span> : null}
          </p>
        </div>

        <div className="flex flex-wrap items-end gap-6">
          <div>
            <p className="label mb-1">Recovered</p>
            <Money
              value={`₹${(recovered / 100).toLocaleString("en-IN")}`}
              tone="recovered"
              size="lg"
            />
          </div>
          <div className="surface-2 flex gap-0.5 rounded-lg border p-1">
            {(["all", "rca", "overdue"] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setFilter(key)}
                aria-pressed={filter === key}
                className={cn(
                  "min-h-11 rounded-md px-3 py-1.5 text-[0.8125rem] font-medium capitalize transition-colors",
                  filter === key
                    ? "surface text-[var(--text)] shadow-[var(--shadow-1)]"
                    : "text-2 hover:text-[var(--text)]",
                )}
              >
                {key === "rca" ? "RCA" : key}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div
        className="surface-2 flex w-fit max-w-full gap-0.5 overflow-x-auto rounded-xl border p-1"
        role="tablist"
        aria-label="Filter by state"
      >
        {(Object.keys(STATE_FILTERS) as StateFilter[]).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={stateFilter === key}
            onClick={() => setStateFilter(key)}
            className={cn(
              "flex min-h-11 items-center gap-1.5 rounded-lg px-3.5 text-[0.8125rem] font-medium whitespace-nowrap transition-colors",
              stateFilter === key ? "brand font-semibold" : "text-2 hover:text-[var(--text)]",
            )}
          >
            {STATE_FILTERS[key].label}
            {key === "cancelled" ? <span className="tnum opacity-70">{cancelled.length}</span> : null}
          </button>
        ))}
      </div>

      {stateFilter === "cancelled" ? (
        visible.length === 0 ? (
          <Empty
            icon={<Ban className="size-6" />}
            title="No cancelled actions"
            hint="An action cancelled from its detail page lands here — off the board, still on record."
          />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {visible
              .sort((a, b) => b.linked_cost_paise - a.linked_cost_paise)
              .map((action) => (
                <ActionCard key={action.id} action={action} />
              ))}
          </div>
        )
      ) : all.length === 0 ? (
        <Empty
          icon={<ClipboardList className="size-6" />}
          title="No actions yet"
          hint="Actions are created at shift close, from events that cross the plant's cost, duration or recurrence thresholds. Everything below those thresholds stays logged and costed — and generates no work. A system that spawns forty open actions a week dies of action fatigue."
        />
      ) : (
        <div className={cn("grid gap-4", GRID_COLS[columns.length])}>
          {columns.map((column, columnIndex) => {
            const items = visible
              .filter((a) => (column.states as readonly string[]).includes(a.state))
              .sort((a, b) => b.linked_cost_paise - a.linked_cost_paise);
            const columnTotal = items.reduce((s, a) => s + a.linked_cost_paise, 0);

            return (
              <section
                key={column.key}
                className="rise min-w-0"
                style={{ animationDelay: `${columnIndex * 70}ms` }}
              >
                <div className="surface elev-1 mb-2.5 overflow-hidden rounded-xl border">
                  <span
                    aria-hidden
                    className="block h-[3px]"
                    style={{ background: `linear-gradient(90deg, ${column.accent}, transparent)` }}
                  />
                  <div className="px-3.5 py-3">
                    <div className="flex items-baseline justify-between gap-2">
                      <h2 className="text-sm font-bold">{column.label}</h2>
                      <span className="label">{column.phase}</span>
                    </div>
                    <div className="mt-1 flex items-baseline justify-between gap-2">
                      <span className="text-3 text-xs">{column.hint}</span>
                      <span className="tnum text-3 text-xs font-semibold">{items.length}</span>
                    </div>
                    {columnTotal > 0 ? (
                      <Money
                        value={`₹${(columnTotal / 100).toLocaleString("en-IN")}`}
                        tone={column.key === "verified" ? "recovered" : "loss"}
                        size="sm"
                        className="mt-1.5 block"
                      />
                    ) : null}
                  </div>
                </div>

                <div className="space-y-2">
                  {items.length === 0 ? (
                    <p className="text-3 rounded-xl border border-dashed px-3 py-8 text-center text-xs">
                      Nothing here
                    </p>
                  ) : (
                    items.slice(0, 25).map((action) => (
                      <ActionCard key={action.id} action={action} />
                    ))
                  )}
                  {items.length > 25 ? (
                    <p className="text-3 px-1 text-xs">+{items.length - 25} more</p>
                  ) : null}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ActionCard({ action }: { action: Action }) {
  return (
    <Link
      href={`/actions/${action.id}`}
      className="surface elev-1 block rounded-xl border p-3.5 transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-[var(--shadow-2)]"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-3 tnum text-[0.6875rem] font-semibold">{action.reference}</span>
        <Money value={action.linked_cost_display} tone="loss" size="sm" />
      </div>
      <p className="mt-1 line-clamp-2 text-[0.8125rem] font-semibold leading-snug">
        {action.title}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {action.routed_to_rca ? (
          <Badge variant="warn" className="gap-1">
            <TriangleAlert className="size-2.5" />
            RCA
          </Badge>
        ) : null}
        {action.is_overdue ? <Badge variant="stopped">Overdue</Badge> : null}
        {action.state === "cancelled" ? <Badge variant="structural">Cancelled</Badge> : null}
        {action.state === "reopened" ? (
          <Badge variant="stopped" className="gap-1">
            <Repeat2 className="size-2.5" />
            Reopened
          </Badge>
        ) : null}
        {action.occurrence_count > 1 ? (
          <Badge variant="neutral">{action.occurrence_count}× pattern</Badge>
        ) : null}
        {action.recovered_profit_paise > 0 ? (
          <Badge variant="recovered">
            {action.recovered_profit_display} · {action.sustained_weeks}w
          </Badge>
        ) : null}
      </div>

      {action.owner_name ? (
        <p className="text-3 mt-2 text-[0.6875rem]">
          {action.owner_name}
          {action.due_date ? ` · due ${action.due_date}` : ""}
        </p>
      ) : null}
    </Link>
  );
}
