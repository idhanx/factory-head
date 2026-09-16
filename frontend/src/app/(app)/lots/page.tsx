"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowRight, Boxes, CheckCircle2, ClipboardCheck, Clock, Cog, Search,
  ShieldCheck, Truck, UserRound, Wrench, X,
} from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { can } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import type { FlowStage, LotFlow, LotTrace, WipLot } from "@/types/api";

/**
 * Lots — §3 traceability and §14.2 stage tracking, on one screen.
 *
 *   "An OEM customer complains about burrs on lot #4471, three weeks after
 *    despatch. Today, answering that means two days of digging through
 *    registers. With the job-order spine, the owner asks and gets the answer
 *    in seconds."
 *
 * Two tabs, because the same lot has two audiences: whoever is holding a
 * complaint letter, and whoever is standing at a stage with the route card.
 */

const SECTION = {
  machining: { label: "Machining", Icon: Cog },
  assembly: { label: "Assembly", Icon: Wrench },
  quality: { label: "Quality", Icon: ClipboardCheck },
  dispatch: { label: "Dispatch", Icon: Truck },
} as const;

export default function LotsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <Lots />
    </Suspense>
  );
}

function Lots() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = params.get("tab") === "wip" ? "wip" : "trace";
  const lotCode = params.get("lot");

  const go = (next: { tab?: string; lot?: string | null }) => {
    const q = new URLSearchParams(params.toString());
    if (next.tab) q.set("tab", next.tab);
    if (next.lot !== undefined) {
      if (next.lot) q.set("lot", next.lot);
      else q.delete("lot");
    }
    router.replace(`${pathname}?${q.toString()}`, { scroll: false });
  };

  return (
    <div className="space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5 flex items-center gap-1.5">
            <Boxes className="size-3" />
            Job-order spine · §3 and §14.2
          </p>
          <h1 className="display text-[1.75rem] font-bold">Lots</h1>
          <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
            Where every lot came from, where it is now, and where parts went missing. None of it
            is typed for traceability&apos;s sake — every fact was captured during a normal shift.
          </p>
        </div>
        <div className="surface-2 flex gap-0.5 rounded-xl border p-1" role="tablist">
          {(
            [
              ["trace", "Find a lot"],
              ["wip", "In progress"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => go({ tab: key })}
              className={cn(
                "rounded-lg px-3.5 py-1.5 text-[0.8125rem] font-medium transition-colors",
                tab === key
                  ? "surface text-[var(--text)] shadow-[var(--shadow-1)]"
                  : "text-2 hover:text-[var(--text)]",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      {tab === "trace" ? (
        <TraceTab lotCode={lotCode} onPick={(lot) => go({ lot })} />
      ) : (
        <WipTab onTrace={(lot) => go({ tab: "trace", lot })} />
      )}
    </div>
  );
}

// ===========================================================================
// Find a lot
// ===========================================================================

function TraceTab({
  lotCode,
  onPick,
}: {
  lotCode: string | null;
  onPick: (lot: string | null) => void;
}) {
  const [query, setQuery] = useState(lotCode ?? "");
  const [debounced, setDebounced] = useState(query);

  useEffect(() => {
    const id = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(id);
  }, [query]);

  const results = useQuery({
    queryKey: ["lot-search", debounced],
    queryFn: () => api.searchLots(debounced),
    enabled: debounced.length >= 2 && !lotCode,
  });
  const trace = useQuery({
    queryKey: ["lot-trace", lotCode],
    queryFn: () => api.traceLot(lotCode!),
    enabled: Boolean(lotCode),
    retry: false,
  });

  return (
    <div className="space-y-4">
      <form
        className="rise relative"
        onSubmit={(e) => {
          e.preventDefault();
          const code = query.trim().replace(/^#/, "");
          if (code) onPick(code);
        }}
      >
        <Search className="text-3 pointer-events-none absolute top-1/2 left-4 size-5 -translate-y-1/2" />
        <Input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (lotCode) onPick(null);
          }}
          placeholder="Lot number off the complaint, a job, a part or a customer — try 4471"
          aria-label="Find a lot"
          className="elev-2 h-14 rounded-2xl pl-12 text-base"
          autoFocus={!lotCode}
        />
      </form>

      {!lotCode ? (
        debounced.length < 2 ? (
          <Empty
            icon={<Search className="size-6" />}
            title="Answer a complaint in seconds"
            hint="Type the lot number from the customer's complaint. You get the job, the machine, the shift, who ran it, who inspected it, what was rejected, and what went wrong on that machine during the run."
          />
        ) : results.isLoading ? (
          <Skeleton className="h-40" />
        ) : (results.data ?? []).length === 0 ? (
          <Empty title="No lot matches that" hint="Lots can be found by lot code, job number, part number or customer." />
        ) : (
          <Card className="rise">
            <ul className="divide-y">
              {results.data!.map((row) => (
                <li key={row.lot_code}>
                  <button
                    type="button"
                    onClick={() => {
                      setQuery(row.lot_code);
                      onPick(row.lot_code);
                    }}
                    className="flex w-full items-center gap-3 px-5 py-3 text-left transition-colors hover:surface-2"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block font-semibold">Lot #{row.lot_code}</span>
                      <span className="text-3 block text-xs">
                        Job {row.job_number ?? "—"} · {row.part_number ?? "—"}
                        {row.customer ? ` · ${row.customer}` : ""}
                      </span>
                    </span>
                    <Badge variant={row.closed ? "neutral" : "brand"}>
                      {row.closed ? "Despatched" : "In progress"}
                    </Badge>
                    <ArrowRight className="text-3 size-4" />
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        )
      ) : trace.isLoading ? (
        <Skeleton className="h-[28rem]" />
      ) : trace.error ? (
        <ErrorNote message={(trace.error as Error).message} />
      ) : trace.data ? (
        <LotStory trace={trace.data} />
      ) : null}
    </div>
  );
}

function LotStory({ trace }: { trace: LotTrace }) {
  const worstStop = trace.runs
    .flatMap((run) => run.events.map((e) => ({ ...e, station: run.station })))
    .sort((a, b) => b.cost_paise - a.cost_paise)[0];

  return (
    <div className="space-y-4">
      {/* ---- The answer, first ------------------------------------------ */}
      <div className="rise rail border-rail rounded-[var(--radius-card)] border p-5 sm:p-6">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Badge variant="brand">Lot #{trace.lot.code}</Badge>
          {trace.job.number ? <Badge variant="neutral">Job {trace.job.number}</Badge> : null}
          {trace.part.number ? <Badge variant="neutral">{trace.part.number}</Badge> : null}
          {trace.job.customer ? <Badge variant="neutral">{trace.job.customer}</Badge> : null}
          <Badge variant={trace.flow.complete ? "recovered" : "warn"}>
            {trace.flow.complete ? `Despatched ${trace.flow.despatched}` : `At ${trace.flow.position ?? "—"}`}
          </Badge>
        </div>
        <p className="text-[1.0625rem] leading-relaxed font-medium sm:text-lg">{trace.summary}</p>
        {trace.no_blame_active ? (
          <p className="text-rail-2 mt-3 flex items-start gap-1.5 text-xs leading-relaxed">
            <ShieldCheck className="mt-px size-3.5 shrink-0" />
            Names appear because traceability requires them. During the no-blame period this
            record is for finding causes — not people.
          </p>
        ) : null}
      </div>

      {/* ---- Through the routing ------------------------------------------ */}
      <Card className="rise" style={{ animationDelay: "60ms" }}>
        <CardHeader className="flex-wrap justify-between gap-2">
          <CardTitle>Through the routing</CardTitle>
          {trace.flow.throughput_minutes ? (
            <span className="text-3 flex items-center gap-1.5 text-xs">
              <Clock className="size-3.5" />
              Throughput {formatDuration(trace.flow.throughput_minutes)}, first operation to despatch
            </span>
          ) : null}
        </CardHeader>
        <CardContent>
          {trace.flow.parts.length ? (
            // A batch holding several parts: each walks its own routing. They
            // are never merged — "Op30" can be despatch for one part and
            // inspection for another.
            <div className="space-y-5">
              {trace.flow.parts.map((part) => (
                <div key={part.part_number}>
                  <p className="mb-2 flex items-center gap-2 text-sm font-semibold">
                    {part.part_number}
                    <Badge variant={part.complete ? "recovered" : "warn"}>
                      {part.complete ? `Despatched ${part.despatched}` : `At ${part.position ?? "—"}`}
                    </Badge>
                  </p>
                  <FlowStepper flow={part} />
                </div>
              ))}
            </div>
          ) : (
            <FlowStepper flow={trace.flow} />
          )}
          <FlowFlags flow={trace.flow} />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ---- Where it was made ---------------------------------------- */}
        <Card className="rise" style={{ animationDelay: "120ms" }}>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5">
              <Cog className="size-3.5" />
              Where it was made
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {trace.runs.length === 0 ? (
              <p className="text-3 text-sm">No production run recorded against this job.</p>
            ) : (
              trace.runs.map((run) => (
                <div key={run.started_at}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="font-semibold">
                      {run.station} · {run.shift} · {formatDay(run.business_date)}
                    </p>
                    <span className="text-2 tnum text-sm">{run.produced} made</span>
                  </div>
                  {run.operator ? (
                    <p className="text-3 mt-0.5 flex items-center gap-1 text-xs">
                      <UserRound className="size-3" />
                      {run.operator}
                    </p>
                  ) : null}
                  {run.events.length ? (
                    <ul className="mt-2.5 space-y-1.5">
                      {run.events.map((event) => (
                        <li
                          key={event.started_at}
                          className={cn(
                            "flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm",
                            worstStop && event.started_at === worstStop.started_at && "border-[color-mix(in_oklab,var(--color-stopped)_40%,transparent)] bg-[var(--color-stopped-dim)]",
                          )}
                        >
                          <span className="min-w-0">
                            <span className="font-medium">{event.reason}</span>
                            <span className="text-3 tnum text-xs">
                              {" "}
                              · {formatTime(event.started_at)} · {Math.round(event.minutes)} min
                            </span>
                          </span>
                          <Money value={event.cost_display} tone="loss" size="sm" />
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-3 mt-2 text-xs">No stops on the machine during this run.</p>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {/* ---- How it was inspected -------------------------------------- */}
        <Card className="rise" style={{ animationDelay: "180ms" }}>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5">
              <ClipboardCheck className="size-3.5" />
              How it was inspected
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {trace.quality.length === 0 ? (
              <p className="text-3 text-sm">No inspection record for this lot.</p>
            ) : (
              trace.quality.map((q) => (
                <div key={q.recorded_at}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <p className="font-semibold">
                      {q.good} good / <span className="text-[var(--color-loss)]">{q.rejected} rejected</span>
                    </p>
                    <span className="text-3 text-xs">{formatDay(q.business_date)}</span>
                  </div>
                  {q.inspector ? (
                    <p className="text-3 mt-0.5 flex items-center gap-1 text-xs">
                      <UserRound className="size-3" />
                      Inspected by {q.inspector}
                    </p>
                  ) : null}
                  {q.defects.length ? (
                    <div className="mt-2.5 flex flex-wrap gap-1.5">
                      {q.defects.map((d) => (
                        <Badge key={d.code} variant="recoverable">
                          {d.count} {d.label.toLowerCase()}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                  {q.scrap_cost_paise > 0 ? (
                    <p className="text-3 mt-2 text-xs">
                      Scrap cost <Money value={q.scrap_cost_display} tone="loss" size="xs" />
                    </p>
                  ) : null}
                </div>
              ))
            )}

            {trace.despatches.length ? (
              <div className="border-t pt-4">
                <p className="label mb-2 flex items-center gap-1.5">
                  <Truck className="size-3" />
                  Despatch
                </p>
                {trace.despatches.map((d) => (
                  <p key={d.despatched_at} className="text-sm">
                    <span className="font-semibold">{d.quantity} parts</span>
                    <span className="text-3">
                      {" "}
                      · {formatDay(d.despatched_at)} {formatTime(d.despatched_at)}
                      {d.vehicle_reference ? ` · ${d.vehicle_reference}` : ""}
                    </span>
                  </p>
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/** The routing as a row of stages. Each shows what arrived, what passed, what
 *  was rejected — and what is waiting in front of it. */
function FlowStepper({ flow }: { flow: LotFlow }) {
  return (
    <div className="overflow-x-auto pb-1">
      <ol className="flex min-w-max items-stretch gap-2">
        {flow.stages.map((stage, index) => (
          <li key={stage.operation_code} className="flex items-stretch gap-2">
            {index > 0 ? (
              <div className="flex w-16 flex-col items-center justify-center text-center">
                <ArrowRight className="text-3 size-4" />
                {stage.wip_before > 0 ? (
                  <span className="mt-1 text-[0.6875rem] font-semibold text-[var(--color-warn)]">
                    {stage.wip_before} waiting
                  </span>
                ) : stage.wip_before < 0 ? (
                  <span className="mt-1 text-[0.6875rem] font-semibold text-[var(--color-stopped)]">
                    +{-stage.wip_before}?
                  </span>
                ) : null}
              </div>
            ) : null}
            <StageBox stage={stage} current={flow.position === stage.operation_code} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function StageBox({ stage, current }: { stage: FlowStage; current: boolean }) {
  const meta = SECTION[stage.section];
  return (
    <div
      className={cn(
        "surface w-40 rounded-2xl border p-3.5",
        current && "ring-2 ring-[var(--color-brand)]",
        !stage.recorded && "border-dashed",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-3 flex items-center gap-1.5 text-xs font-semibold">
          <meta.Icon className="size-3.5" />
          {stage.operation_code}
        </span>
        {current ? <Badge variant="brand">Here</Badge> : null}
      </div>
      <p className="mt-1 text-sm font-semibold">{meta.label}</p>
      {stage.recorded ? (
        <>
          <p className="figure mt-2 text-[1.75rem] leading-none font-bold">{stage.completed}</p>
          <p className="text-3 text-[0.6875rem]">
            {stage.section === "dispatch" ? "despatched" : "passed"}
          </p>
          {stage.rejected > 0 ? (
            <p className="mt-1 text-xs font-semibold text-[var(--color-loss)]">
              {stage.rejected} rejected
            </p>
          ) : null}
        </>
      ) : (
        <p className="text-3 mt-2 text-xs">Not reached yet</p>
      )}
    </div>
  );
}

function FlowFlags({ flow }: { flow: LotFlow }) {
  if (flow.flags.length === 0) {
    return flow.complete ? (
      <p className="mt-4 flex items-center gap-1.5 text-sm text-[var(--color-running)]">
        <CheckCircle2 className="size-4" />
        Every part is accounted for, from first operation to the gate.
      </p>
    ) : null;
  }
  return (
    <div className="mt-4 space-y-2">
      {flow.flags.map((flag) => (
        <div
          key={flag.kind + flag.operation_code}
          role="alert"
          className="flex items-start gap-2.5 rounded-xl border border-[color-mix(in_oklab,var(--color-stopped)_35%,transparent)] bg-[var(--color-stopped-dim)] px-4 py-3 text-sm"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-[var(--color-stopped)]" />
          <span>
            <span className="font-semibold">{flag.gap} parts unaccounted.</span> {flag.message}
          </span>
        </div>
      ))}
    </div>
  );
}

// ===========================================================================
// In progress — the WIP board and stage entry
// ===========================================================================

function WipTab({ onTrace }: { onTrace: (lot: string) => void }) {
  const session = loadSession();
  const canRecord = session ? can(session.roles, "supervisor", "quality", "owner", "stores") : false;
  const [section, setSection] = useState<FlowStage["section"] | "all">("all");
  const [entering, setEntering] = useState<WipLot | null>(null);

  const wip = useQuery({ queryKey: ["wip"], queryFn: api.wip });
  const lots = wip.data ?? [];
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const lot of lots) c[lot.section ?? "unknown"] = (c[lot.section ?? "unknown"] ?? 0) + 1;
    return c;
  }, [lots]);
  const visible = lots.filter((lot) => section === "all" || lot.section === section);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {(["all", "machining", "assembly", "quality", "dispatch"] as const).map((key) => {
          const Icon = key === "all" ? Boxes : SECTION[key].Icon;
          const count = key === "all" ? lots.length : counts[key] ?? 0;
          return (
            <button
              key={key}
              type="button"
              onClick={() => setSection(key)}
              aria-pressed={section === key}
              className={cn(
                "surface flex items-center gap-2 rounded-xl border px-3.5 py-2 text-sm font-medium transition-[box-shadow]",
                section === key ? "ring-2 ring-[var(--color-brand)]" : "hover:shadow-[var(--shadow-1)]",
              )}
            >
              <Icon className="size-4" />
              {key === "all" ? "All stages" : `Waiting at ${SECTION[key].label.toLowerCase()}`}
              <span className="text-3 tnum text-xs">{count}</span>
            </button>
          );
        })}
      </div>

      {wip.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <Empty
          icon={<Boxes className="size-6" />}
          title="Nothing waiting here"
          hint="A lot appears once its first stage is recorded and leaves when it is despatched. A lot nobody has touched is a plan, not work in progress."
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((lot, index) => (
            <Card
              key={lot.lot_code}
              elevation={1}
              className="rise"
              style={{ animationDelay: `${Math.min(index * 30, 300)}ms` }}
            >
              <CardContent className="pt-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-bold">Lot #{lot.lot_code}</p>
                    <p className="text-3 text-xs">
                      Job {lot.job_number} · {lot.part_number}
                    </p>
                  </div>
                  {lot.section ? (
                    <Badge variant="brand">
                      {lot.waiting} at {SECTION[lot.section].label.toLowerCase()}
                    </Badge>
                  ) : null}
                </div>

                {/* Mini routing: filled = passed, ringed = where it sits now. */}
                <div className="mt-3 flex items-center gap-1" aria-label="Progress through the routing">
                  {lot.stages.map((stage) => (
                    <span
                      key={stage.operation_code}
                      title={`${stage.operation_code} ${stage.section}: ${stage.recorded ? `${stage.completed} passed` : "not reached"}`}
                      className={cn(
                        "h-1.5 flex-1 rounded-full",
                        stage.operation_code === lot.position
                          ? "bg-[var(--color-brand)]"
                          : stage.recorded
                            ? "bg-[var(--color-running)]"
                            : "surface-inset",
                      )}
                    />
                  ))}
                </div>
                <p className="text-3 mt-1.5 text-[0.6875rem]">
                  {lot.stages.map((s) => s.operation_code).join(" → ")}
                  {lot.age_hours !== null ? ` · opened ${formatAge(lot.age_hours)} ago` : ""}
                </p>

                {lot.flags > 0 ? (
                  <p className="mt-2 flex items-center gap-1 text-xs font-semibold text-[var(--color-stopped)]">
                    <AlertTriangle className="size-3.5" />
                    Counts disagree
                  </p>
                ) : null}

                <div className="mt-3 flex gap-2">
                  {canRecord ? (
                    <Button size="sm" onClick={() => setEntering(lot)}>
                      Record {lot.position ?? "stage"}
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" onClick={() => onTrace(lot.lot_code)}>
                    Trace
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {entering ? <StageSheet lot={entering} onClose={() => setEntering(null)} /> : null}
    </div>
  );
}

/** §14.2 — "the operator scans it (or taps the lot from a short list) and
 *  enters the quantity". One screen, two numbers at most, then the verdict. */
function StageSheet({ lot, onClose }: { lot: WipLot; onClose: () => void }) {
  const queryClient = useQueryClient();
  const stage = lot.stages.find((s) => s.operation_code === lot.position);
  const isDispatch = stage?.section === "dispatch";

  const [passed, setPassed] = useState(String(lot.waiting || ""));
  const [rejected, setRejected] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [result, setResult] = useState<LotFlow | null>(null);

  const submit = useMutation({
    mutationFn: () =>
      isDispatch
        ? api.recordDispatch({
            lot_code: lot.lot_code,
            quantity: Number(passed),
            vehicle_reference: vehicle.trim() || undefined,
          })
        : api.recordStage({
            lot_code: lot.lot_code,
            operation_code: lot.position!,
            quantity_completed: Number(passed || 0),
            quantity_rejected: Number(rejected || 0),
          }),
    onSuccess: (response) => {
      setResult(response.flow);
      void queryClient.invalidateQueries({ queryKey: ["wip"] });
      void queryClient.invalidateQueries({ queryKey: ["lot-trace", lot.lot_code] });
    },
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-[oklch(20%_0.01_70/0.45)] backdrop-blur-sm sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Record ${lot.position} for lot ${lot.lot_code}`}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="rise surface elev-4 max-h-[92dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl border p-5 sm:rounded-2xl">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="label mb-1">
              {stage ? SECTION[stage.section].label : "Stage"} · {lot.position}
            </p>
            <h2 className="display text-xl font-bold">Lot #{lot.lot_code}</h2>
            <p className="text-2 text-sm">
              {lot.waiting} parts waiting here · {lot.part_number}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-3 grid size-10 place-items-center rounded-xl border hover:surface-2"
          >
            <X className="size-4" />
          </button>
        </div>

        {result ? (
          <div className="space-y-3">
            <p className="flex items-center gap-1.5 font-semibold text-[var(--color-running)]">
              <CheckCircle2 className="size-5" />
              Recorded
            </p>
            <FlowStepper flow={result} />
            <FlowFlags flow={result} />
            <Button className="w-full" size="lg" onClick={onClose}>
              Done
            </Button>
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              submit.mutate();
            }}
          >
            <div className={cn("grid gap-3", !isDispatch && "grid-cols-2")}>
              <div>
                <Label htmlFor="passed">{isDispatch ? "Despatched" : "Passed"}</Label>
                <Input
                  id="passed"
                  inputMode="numeric"
                  value={passed}
                  onChange={(e) => setPassed(e.target.value.replace(/\D/g, ""))}
                  className="money h-16 text-center text-3xl font-bold"
                  autoFocus
                />
              </div>
              {!isDispatch ? (
                <div>
                  <Label htmlFor="rejected">Rejected</Label>
                  <Input
                    id="rejected"
                    inputMode="numeric"
                    value={rejected}
                    onChange={(e) => setRejected(e.target.value.replace(/\D/g, ""))}
                    placeholder="0"
                    className="money h-16 text-center text-3xl font-bold"
                  />
                </div>
              ) : null}
            </div>
            {isDispatch ? (
              <div>
                {/* §14.2: "vehicle/challan reference (one field)". */}
                <Label htmlFor="vehicle">Vehicle or challan</Label>
                <Input
                  id="vehicle"
                  value={vehicle}
                  onChange={(e) => setVehicle(e.target.value)}
                  placeholder="TN 70 AB 1234 / DC-44718"
                />
              </div>
            ) : null}
            {submit.error ? <ErrorNote message={(submit.error as Error).message} /> : null}
            <Button
              type="submit"
              size="touch"
              className="w-full"
              disabled={submit.isPending || (!passed && !rejected)}
            >
              {submit.isPending ? "Saving…" : isDispatch ? "Record despatch" : `Record ${lot.position}`}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function formatDay(iso: string) {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
}

function formatDuration(minutes: number) {
  const days = Math.floor(minutes / 1440);
  const hours = Math.round((minutes % 1440) / 60);
  return days ? `${days}d ${hours}h` : `${hours}h`;
}

function formatAge(hours: number) {
  return hours >= 48 ? `${Math.round(hours / 24)} days` : `${Math.round(hours)} h`;
}
