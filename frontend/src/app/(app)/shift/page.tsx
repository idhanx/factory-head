"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Repeat, WifiOff } from "lucide-react";
import { api } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { useSync } from "@/lib/offline/status";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Textarea } from "@/components/ui/input";
import { ErrorNote, Skeleton } from "@/components/ui/misc";
import {
  JOB_STATUS_LABEL, NewJobOrderSheet, RepeatJobOrderSheet, isAssignable, jobLabel,
} from "@/components/job-orders";
import type { JobOrder, Station } from "@/types/api";

/**
 * Screens 1 and 6 — shift start, and the closure lock.
 *
 * §2 is unambiguous about why the lock exists: it is the mechanism that makes
 * manually-entered data honest. "Closing the shift is how they go home — so the
 * data gets filled."
 *
 * Starting and closing a shift are online-only. Floor entries can wait on the
 * tablet; the lock cannot — closing on a tablet that has not sent its stops
 * would lock the shift without them.
 */
export default function ShiftPage() {
  const queryClient = useQueryClient();
  const shift = useQuery({ queryKey: ["shift"], queryFn: api.currentShift });

  if (shift.isLoading) return <Skeleton className="h-64" />;
  if (shift.error) {
    return (
      <div className="space-y-4">
        <h1 className="display text-[1.75rem] font-bold">Shift</h1>
        <ErrorNote message={`Could not check for an open shift: ${shift.error.message}`} />
        <Button size="lg" variant="outline" onClick={() => void shift.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  return shift.data ? (
    <CloseShift
      sessionId={shift.data.id}
      businessDate={shift.data.business_date}
      startedAt={shift.data.started_at}
      onDone={() => void queryClient.invalidateQueries({ queryKey: ["shift"] })}
    />
  ) : (
    <StartShift onDone={() => void queryClient.invalidateQueries({ queryKey: ["shift"] })} />
  );
}

/** Screen 1 — under 60 seconds. */
function StartShift({ onDone }: { onDone: () => void }) {
  const [shiftId, setShiftId] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [repeating, setRepeating] = useState<JobOrder | null>(null);
  const [created, setCreated] = useState<JobOrder | null>(null);
  const online = useSync((s) => s.online);

  const shifts = useQuery({ queryKey: ["shifts"], queryFn: api.shifts });
  const stations = useQuery({ queryKey: ["stations"], queryFn: api.stations });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.jobOrders });

  const start = useMutation({
    mutationFn: () =>
      api.startShift(
        shiftId!,
        Object.entries(assignments).map(([station_id, job_order_id]) => ({
          station_id,
          job_order_id,
        })),
      ),
    onSuccess: onDone,
    onError: (e: Error) => setError(e.message),
  });

  const producing = (stations.data ?? []).filter(
    (s: Station) => s.section === "machining" || s.section === "assembly",
  );
  const assignable = (jobs.data ?? []).filter(isAssignable);
  const loadError = shifts.error ?? stations.error ?? jobs.error;

  return (
    <div className="space-y-6">
      <header className="rise">
        <p className="label mb-1.5">Screen 1</p>
        <h1 className="display text-[1.75rem] font-bold">Start shift</h1>
        <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
          Pick the shift, confirm what each line is running, tap Start. Everything recorded
          after this inherits the job&apos;s part, customer, lot and operator — nobody types
          them again.
        </p>
      </header>

      {error ? <ErrorNote message={error} /> : null}
      {loadError ? <ErrorNote message={`Could not load shift setup: ${loadError.message}`} /> : null}

      <Card>
        <CardHeader><CardTitle>Shift</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {(shifts.data ?? []).map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setShiftId(s.id)}
              className={`touch rounded-lg border px-6 text-left ${
                shiftId === s.id ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]" : ""
              }`}
            >
              <span className="block font-semibold">{s.name}</span>
              <span className="block text-xs text-2">
                {String(s.start_time).slice(0, 5)}–{String(s.end_time).slice(0, 5)}
              </span>
            </button>
          ))}
        </CardContent>
      </Card>

      {/* Today's jobs first: a station select with nothing to pick is the one
          thing that stops a shift starting in under a minute. */}
      <Card>
        <CardHeader className="flex-wrap justify-between">
          <CardTitle>Job orders</CardTitle>
          <Button size="lg" onClick={() => setCreating(true)}>
            <Plus className="size-4" />
            New job order
          </Button>
        </CardHeader>
        <CardContent>
          {created ? (
            <p role="status" className="surface-2 rise mb-3 rounded-xl border px-4 py-3 text-sm">
              Job <span className="font-semibold">#{created.job_number}</span> created — pick it
              for a station below.
            </p>
          ) : null}
          {jobs.isLoading ? (
            <Skeleton className="h-24" />
          ) : assignable.length === 0 ? (
            <p className="text-3 rounded-xl border border-dashed px-3 py-6 text-center text-sm">
              No open job orders. Tap <strong>New job order</strong> to add today&apos;s work.
            </p>
          ) : (
            <ul className="divide-y">
              {assignable.map((job) => (
                <li key={job.id} className="flex flex-wrap items-center gap-3 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="font-semibold">
                      #{job.job_number}
                      {job.part_number ? <span className="text-2 font-normal"> · {job.part_number}</span> : null}
                    </p>
                    <p className="text-3 text-xs">
                      {job.planned_quantity} pcs
                      {job.customer_name ? ` · ${job.customer_name}` : ""}
                      {job.due_date ? ` · due ${job.due_date}` : ""}
                    </p>
                  </div>
                  {job.status !== "planned" ? (
                    <Badge variant="neutral">{JOB_STATUS_LABEL[job.status] ?? job.status}</Badge>
                  ) : null}
                  <Button size="lg" variant="outline" onClick={() => setRepeating(job)}>
                    <Repeat className="size-4" />
                    Repeat
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Jobs per station</CardTitle></CardHeader>
        <CardContent>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {producing.map((station) => (
              <div key={station.id} className="rounded-lg border p-3">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{station.code}</span>
                  {station.is_bottleneck ? <Badge variant="warn">Bottleneck</Badge> : null}
                </div>
                <select
                  value={assignments[station.id] ?? ""}
                  onChange={(e) =>
                    setAssignments((prev) => {
                      const next = { ...prev };
                      if (e.target.value) next[station.id] = e.target.value;
                      else delete next[station.id];
                      return next;
                    })
                  }
                  className="mt-2 h-11 w-full rounded-lg border surface px-2 text-sm"
                  aria-label={`Job for ${station.code}`}
                >
                  <option value="">Not running</option>
                  {assignable.map((job: JobOrder) => (
                    <option key={job.id} value={job.id}>
                      {jobLabel(job)}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {!online ? <NeedsConnection what="Starting a shift" /> : null}
      <Button
        size="xl"
        className="w-full sm:w-auto"
        disabled={!shiftId || start.isPending || !online}
        onClick={() => start.mutate()}
      >
        {start.isPending ? "Starting…" : "Start shift"}
      </Button>

      {creating ? (
        <NewJobOrderSheet onClose={() => setCreating(false)} onCreated={setCreated} />
      ) : null}
      {repeating ? (
        <RepeatJobOrderSheet job={repeating} onClose={() => setRepeating(null)} onCreated={setCreated} />
      ) : null}
    </div>
  );
}

/** Screen 6 — the closure lock. */
function CloseShift({
  sessionId,
  businessDate,
  startedAt,
  onDone,
}: {
  sessionId: string;
  businessDate: string;
  startedAt: string;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [overrideReason, setOverrideReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const online = useSync((s) => s.online);
  const unsent = useSync((s) => s.pendingCount);

  const check = useQuery({
    queryKey: ["closure", sessionId],
    queryFn: () => api.closureCheck(sessionId),
    refetchInterval: 30_000,
  });
  const kpis = useQuery({ queryKey: ["kpis", sessionId], queryFn: () => api.kpis(sessionId) });

  // An open escalation is acknowledged here rather than on a phone: the person
  // closing the shift is the one who knows the stop was dealt with.
  const acknowledge = useMutation({
    mutationFn: (escalationId: string) => api.acknowledgeEscalation(escalationId),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["closure", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["escalations"] });
    },
    onError: (e: Error) => setError(`Could not acknowledge: ${e.message}`),
  });

  const close = useMutation({
    mutationFn: (reason?: string) => api.closeShift(sessionId, reason),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries();
      onDone();
    },
    onError: (e: Error) => setError(e.message),
  });

  const blockers = check.data?.blockers ?? [];
  const canClose = check.data?.can_close ?? false;

  return (
    <div className="space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5">Screen 6 · the closure lock</p>
          <h1 className="display text-[1.75rem] font-bold">Close shift</h1>
          <p className="text-2 mt-1 text-sm">
            {businessDate} · started {formatClock(startedAt)}
          </p>
        </div>
        {check.data ? (
          <Badge variant={canClose ? "recovered" : "warn"}>
            {canClose ? "Ready to close" : `${blockers.length} to resolve`}
          </Badge>
        ) : null}
      </header>

      {error ? <ErrorNote message={error} /> : null}
      {check.error ? (
        <ErrorNote message={`Could not run the closure check: ${check.error.message}`} />
      ) : null}

      {kpis.data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="OEE" value={`${(kpis.data.oee * 100).toFixed(0)}%`} />
          <Stat label="Produced" value={`${kpis.data.total_count}`} sub={`target ${kpis.data.target}`} />
          <Stat
            label="Downtime"
            value={`${Math.round(kpis.data.downtime_minutes)} min`}
            sub={
              typeof kpis.data.downtime_percent === "number"
                ? `${pct(kpis.data.downtime_percent)} of planned · ${pct(kpis.data.recoverable_downtime_percent ?? 0)} recoverable`
                : undefined
            }
          />
          <div className="surface elev-1 rounded-xl border p-4">
            <p className="label mb-1.5">Recoverable loss</p>
            <Money
              value={`₹${(kpis.data.recoverable_loss_paise / 100).toLocaleString("en-IN")}`}
              tone="loss"
              size="lg"
            />
          </div>
          {typeof kpis.data.man_hours === "number" ? (
            <>
              <Stat label="Man-hours" value={kpis.data.man_hours.toFixed(1)} sub="booked on this shift" />
              <Stat
                label="Productivity"
                value={
                  kpis.data.productivity_per_man_hour === null
                    ? "—"
                    : kpis.data.productivity_per_man_hour.toFixed(1)
                }
                sub={kpis.data.productivity_per_man_hour === null ? "no man-hours booked" : "good parts per man-hour"}
              />
              {typeof kpis.data.structural_downtime_percent === "number" ? (
                <Stat
                  label="Structural downtime"
                  value={pct(kpis.data.structural_downtime_percent)}
                  sub="of planned time — logged, not actionable"
                />
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      {!online ? <NeedsConnection what="Closing the shift" /> : null}
      {online && unsent > 0 ? (
        <p role="status" className="rounded-xl border border-[var(--color-warn)] px-4 py-3 text-sm text-[var(--color-warn)]">
          {unsent} entr{unsent === 1 ? "y is" : "ies are"} still on this tablet, waiting to sync. Let
          them reach the server before closing, or they will be held for the owner to review.
        </p>
      ) : null}

      <Card className={canClose ? "" : "border-[var(--color-warn)]"}>
        <CardHeader>
          <CardTitle>Before you go home</CardTitle>
        </CardHeader>
        <CardContent>
          {check.isLoading ? (
            <Skeleton className="h-24" />
          ) : check.error ? (
            <p className="text-3 py-4 text-sm">
              The checklist could not be loaded, so it is not known what is still open.
            </p>
          ) : canClose ? (
            <p className="py-4 text-sm">
              Everything is recorded. Closing locks this shift permanently — after this,
              nothing inside it can be edited, by anyone.
            </p>
          ) : (
            <ul className="divide-y">
              {blockers.map((blocker) => (
                <li
                  key={`${blocker.kind}-${blocker.reference_id}`}
                  className="flex flex-wrap items-center gap-3 py-3"
                >
                  <span className="grid size-5 shrink-0 place-items-center rounded-md border-2 border-[var(--color-stopped)]" />
                  <span className="min-w-0 flex-1 text-sm">{blocker.detail}</span>
                  <Badge variant="stopped">{blocker.kind.replace(/_/g, " ")}</Badge>
                  {blocker.kind === "open_escalation" && blocker.reference_id ? (
                    <Button
                      size="lg"
                      variant="outline"
                      disabled={acknowledge.isPending || !online}
                      onClick={() => acknowledge.mutate(blocker.reference_id!)}
                    >
                      {acknowledge.isPending && acknowledge.variables === blocker.reference_id
                        ? "Acknowledging…"
                        : "Acknowledge"}
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {canClose ? (
        <Button size="xl" onClick={() => close.mutate(undefined)} disabled={close.isPending || !online}>
          {close.isPending ? "Closing…" : "Close and lock shift"}
        </Button>
      ) : (
        <Card>
          <CardHeader><CardTitle>Owner override</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-3 text-sm text-2">
              A supervisor should never have to invent a reason code to go home. An owner can
              override — but the override, and this reason, are written to the audit trail and
              appear in the monthly report.
            </p>
            <Label htmlFor="override">Why is it being overridden?</Label>
            <Textarea
              id="override"
              rows={2}
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              placeholder="Operator left before logging the 1:10 PM stop; reason unknown"
            />
            <Button
              className="mt-3"
              variant="outline"
              disabled={!overrideReason.trim() || close.isPending || !online}
              onClick={() => close.mutate(overrideReason.trim())}
            >
              Close with override
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function NeedsConnection({ what }: { what: string }) {
  return (
    <p role="status" className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-3 text-sm text-[var(--color-warn)]">
      <WifiOff className="size-4 shrink-0" />
      Needs a connection. {what} cannot be saved on the tablet for later — try again when the
      connection is back.
    </p>
  );
}

function pct(ratio: number) {
  return `${(ratio * 100).toFixed(1)}%`;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="surface elev-1 rounded-xl border p-4">
      <p className="label mb-1.5">{label}</p>
      <p className="figure text-[1.75rem] font-bold leading-none">{value}</p>
      {sub ? <p className="text-3 mt-1 text-xs">{sub}</p> : null}
    </div>
  );
}
