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
import { useT } from "@/lib/i18n";

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
  const t = useT();
  const queryClient = useQueryClient();
  const shift = useQuery({ queryKey: ["shift"], queryFn: api.currentShift });

  if (shift.isLoading) return <Skeleton className="h-64" />;
  if (shift.error) {
    return (
      <div className="space-y-4">
        <h1 className="display text-[1.75rem] font-bold">{t("Shift")}</h1>
        <ErrorNote message={t("Could not check for an open shift: {0}", { 0: shift.error.message })} />
        <Button size="lg" variant="outline" onClick={() => void shift.refetch()}>
          {t("Try again")}
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
  const t = useT();
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
        <p className="label mb-1.5">{t("Screen 1")}</p>
        <h1 className="display text-[1.75rem] font-bold">{t("Start shift")}</h1>
        <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
          {t("Pick the shift, confirm what each line is running, tap Start. Everything recorded after this inherits the job's part, customer, lot and operator — nobody types them again.")}
        </p>
      </header>

      {error ? <ErrorNote message={error} /> : null}
      {loadError ? <ErrorNote message={t("Could not load shift setup: {0}", { 0: loadError.message })} /> : null}

      <Card>
        <CardHeader><CardTitle>{t("Shift")}</CardTitle></CardHeader>
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
          <CardTitle>{t("Job orders")}</CardTitle>
          <Button size="lg" onClick={() => setCreating(true)}>
            <Plus className="size-4" />
            {t("New job order")}
          </Button>
        </CardHeader>
        <CardContent>
          {created ? (
            <p role="status" className="surface-2 rise mb-3 rounded-xl border px-4 py-3 text-sm">
              {t("Job")}{" "}<span className="font-semibold">#{created.job_number}</span> {t("created — pick it for a station below.")}
            </p>
          ) : null}
          {jobs.isLoading ? (
            <Skeleton className="h-24" />
          ) : assignable.length === 0 ? (
            <p className="text-3 rounded-xl border border-dashed px-3 py-6 text-center text-sm">
              {t("No open job orders. Tap")}{" "}<strong>{t("New job order")}</strong> {t("to add today's work.")}
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
                      {t("{0} pcs{1}{2}", { 0: job.planned_quantity, 1: job.customer_name ? ` · ${job.customer_name}` : "", 2: job.due_date ? t(" · due {0}", { 0: job.due_date }) : "" })}
                    </p>
                  </div>
                  {job.status !== "planned" ? (
                    <Badge variant="neutral">{JOB_STATUS_LABEL[job.status] ?? job.status}</Badge>
                  ) : null}
                  <Button size="lg" variant="outline" onClick={() => setRepeating(job)}>
                    <Repeat className="size-4" />
                    {t("Repeat")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("Jobs per station")}</CardTitle></CardHeader>
        <CardContent>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {producing.map((station) => (
              <div key={station.id} className="rounded-lg border p-3">
                <div className="flex items-center gap-2">
                  <span className="font-semibold">{station.code}</span>
                  {station.is_bottleneck ? <Badge variant="warn">{t("Bottleneck")}</Badge> : null}
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
                  aria-label={t("Job for {0}", { 0: station.code })}
                >
                  <option value="">{t("Not running")}</option>
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

      {!online ? <NeedsConnection what={t("Starting a shift")} /> : null}
      <Button
        size="xl"
        className="w-full sm:w-auto"
        disabled={!shiftId || start.isPending || !online}
        onClick={() => start.mutate()}
      >
        {start.isPending ? t("Starting…") : t("Start shift")}
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
  const t = useT();
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
    onError: (e: Error) => setError(t("Could not acknowledge: {0}", { 0: e.message })),
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
          <p className="label mb-1.5">{t("Screen 6 · the closure lock")}</p>
          <h1 className="display text-[1.75rem] font-bold">{t("Close shift")}</h1>
          <p className="text-2 mt-1 text-sm">
            {t("{0} · started {1}", { 0: businessDate, 1: formatClock(startedAt) })}
          </p>
        </div>
        {check.data ? (
          <Badge variant={canClose ? "recovered" : "warn"}>
            {canClose ? t("Ready to close") : t("{0} to resolve", { 0: blockers.length })}
          </Badge>
        ) : null}
      </header>

      {error ? <ErrorNote message={error} /> : null}
      {check.error ? (
        <ErrorNote message={t("Could not run the closure check: {0}", { 0: check.error.message })} />
      ) : null}

      {kpis.data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat label="OEE" value={`${(kpis.data.oee * 100).toFixed(0)}%`} />
          <Stat label={t("Produced")} value={`${kpis.data.total_count}`} sub={`target ${kpis.data.target}`} />
          <Stat
            label={t("Downtime")}
            value={`${Math.round(kpis.data.downtime_minutes)} min`}
            sub={
              typeof kpis.data.downtime_percent === "number"
                ? t("{0} of planned · {1} recoverable", {
                    0: pct(kpis.data.downtime_percent),
                    1: pct(kpis.data.recoverable_downtime_percent ?? 0),
                  })
                : undefined
            }
          />
          <div className="surface elev-1 rounded-xl border p-4">
            <p className="label mb-1.5">{t("Recoverable loss")}</p>
            <Money
              value={`₹${(kpis.data.recoverable_loss_paise / 100).toLocaleString("en-IN")}`}
              tone="loss"
              size="lg"
            />
          </div>
          {typeof kpis.data.man_hours === "number" ? (
            <>
              <Stat label={t("Man-hours")} value={kpis.data.man_hours.toFixed(1)} sub={t("booked on this shift")} />
              <Stat
                label={t("Productivity")}
                value={
                  kpis.data.productivity_per_man_hour === null
                    ? "—"
                    : kpis.data.productivity_per_man_hour.toFixed(1)
                }
                sub={
                  kpis.data.productivity_per_man_hour === null
                    ? t("no man-hours booked")
                    : t("good parts per man-hour")
                }
              />
              {typeof kpis.data.structural_downtime_percent === "number" ? (
                <Stat
                  label={t("Structural downtime")}
                  value={pct(kpis.data.structural_downtime_percent)}
                  sub={t("of planned time — logged, not actionable")}
                />
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      {!online ? <NeedsConnection what={t("Closing the shift")} /> : null}
      {online && unsent > 0 ? (
        <p role="status" className="rounded-xl border border-[var(--color-warn)] px-4 py-3 text-sm text-[var(--color-warn)]">
          {t("{0} entr{1} still on this tablet, waiting to sync. Let them reach the server before closing, or they will be held for the owner to review.", { 0: unsent, 1: unsent === 1 ? "y is" : "ies are" })}
        </p>
      ) : null}

      <Card className={canClose ? "" : "border-[var(--color-warn)]"}>
        <CardHeader>
          <CardTitle>{t("Before you go home")}</CardTitle>
        </CardHeader>
        <CardContent>
          {check.isLoading ? (
            <Skeleton className="h-24" />
          ) : check.error ? (
            <p className="text-3 py-4 text-sm">
              {t("The checklist could not be loaded, so it is not known what is still open.")}
            </p>
          ) : canClose ? (
            <p className="py-4 text-sm">
              {t("Everything is recorded. Closing locks this shift permanently — after this, nothing inside it can be edited, by anyone.")}
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
                        ? t("Acknowledging…")
                        : t("Acknowledge")}
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
          {close.isPending ? t("Closing…") : t("Close and lock shift")}
        </Button>
      ) : (
        <Card>
          <CardHeader><CardTitle>{t("Owner override")}</CardTitle></CardHeader>
          <CardContent>
            <p className="mb-3 text-sm text-2">
              {t("A supervisor should never have to invent a reason code to go home. An owner can override — but the override, and this reason, are written to the audit trail and appear in the monthly report.")}
            </p>
            <Label htmlFor="override">{t("Why is it being overridden?")}</Label>
            <Textarea
              id="override"
              rows={2}
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              placeholder={t("Operator left before logging the 1:10 PM stop; reason unknown")}
            />
            <Button
              className="mt-3"
              variant="outline"
              disabled={!overrideReason.trim() || close.isPending || !online}
              onClick={() => close.mutate(overrideReason.trim())}
            >
              {t("Close with override")}
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function NeedsConnection({ what }: { what: string }) {
  const t = useT();
  return (
    <p role="status" className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-3 text-sm text-[var(--color-warn)]">
      <WifiOff className="size-4 shrink-0" />
      {t("Needs a connection.")}{" "}{what} {" "}{t("cannot be saved on the tablet for later — try again when the connection is back.")}
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
