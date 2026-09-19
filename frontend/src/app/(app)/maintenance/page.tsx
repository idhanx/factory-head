"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, CheckCircle2, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { formatClock, formatInr, formatMinutes, minutesSince } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import { Grid, Page, PageHeader, Panel, Section, Stat } from "@/components/ui/page";
import { Gauge } from "@/components/charts";
import { Money } from "@/components/ui/money";
import { Sheet } from "@/components/ui/sheet";
import type { ClosedBreakdown, Escalation, MaintenanceKpis, OpenStop } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * The maintenance in-charge's desk (§1).
 *
 *   Maintenance In-charge | Receives escalation alerts. Logs what was done to
 *   fix breakdowns. | When events occur.
 *
 * "When events occur" is the whole design brief. Nobody in this job opens an
 * app to browse — they open it because a machine is down and a message just
 * arrived. So this is one ranked list and one form, not a dashboard: the stop
 * that is costing the most right now, and somewhere to write what was done
 * about it before walking back to the floor.
 *
 * What lands here is decided by the reason code's escalation route — the same
 * rule §3 Layer 2 uses to pick who gets the 15-minute WhatsApp, so the screen
 * and the ladder can never disagree about whose problem a stop is.
 */
/**
 * A span a person can picture.
 *
 * MTBF is often hundreds of hours, and "1133h 6m" is a number nobody converts
 * in their head — at that scale the minutes are noise and days are the unit
 * the answer is actually in.
 */
function formatSpan(minutes: number): string {
  if (minutes >= 2880) {
    const days = minutes / 1440;
    return `${days >= 10 ? Math.round(days) : days.toFixed(1)} days`;
  }
  return formatMinutes(minutes);
}

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 28, label: "28 days" },
  { days: 90, label: "90 days" },
] as const;

export default function Maintenance() {
  const t = useT();
  const queryClient = useQueryClient();
  const [days, setDays] = useState<number>(28);
  const [writing, setWriting] = useState<{ id: string; station: string; reason: string } | null>(
    null,
  );

  const desk = useQuery({
    queryKey: ["maintenance-desk"],
    queryFn: api.maintenanceDesk,
    // A stop that is down now has a cost climbing on screen. Thirty seconds is
    // often enough to feel live without turning a shared tablet into a radio.
    refetchInterval: 30_000,
  });

  const kpis = useQuery({
    queryKey: ["kpis", "maintenance", days],
    queryFn: () => api.roleKpis("maintenance", days),
  });

  const acknowledge = useMutation({
    mutationFn: (id: string) => api.acknowledgeEscalation(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["maintenance-desk"] }),
  });

  if (desk.isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  if (desk.error) {
    return (
      <div className="mx-auto max-w-4xl">
        <ErrorNote message={t("Could not load the desk: {0}", { 0: (desk.error as Error).message })} />
      </div>
    );
  }

  const data = desk.data!;
  const nothingOpen =
    data.alerts.length === 0 &&
    data.open_breakdowns.length === 0 &&
    data.awaiting_repair_note.length === 0;

  return (
    <Page width="wide">
      <PageHeader
        eyebrow={t("Maintenance")}
        icon={<Wrench className="size-3" />}
        title={t("What is down now")}
        description={t("Your stops ranked by what they are costing — and the machines that keep taking your day.")}
        actions={
          <div className="surface-2 flex gap-0.5 rounded-xl border p-1">
            {RANGES.map((range) => (
              <button
                key={range.days}
                type="button"
                onClick={() => setDays(range.days)}
                className={
                  days === range.days
                    ? "surface rounded-lg px-3 py-1.5 text-[0.8125rem] font-medium shadow-[var(--shadow-1)]"
                    : "text-2 rounded-lg px-3 py-1.5 text-[0.8125rem] font-medium hover:text-[var(--text)]"
                }
              >
                {t(range.label)}
              </button>
            ))}
          </div>
        }
      />

      {/* ---- The one live number, plus §8's three -----------------------
          The live figure is deliberately maintenance's OWN open stops, not the
          whole floor's: putting Stores' material waits on a fitter's screen
          would make the number one nobody could own. */}
      <Grid cols={4}>
        <Stat
          label={t("Costing you right now")}
          tone="feature"
          money
          display={data.open_cost_display}
          hint={
            data.open_breakdowns.length === 0
              ? t("Nothing of yours is stopped")
              : t("{0} stop{1} waiting on maintenance", { 0: data.open_breakdowns.length, 1: data.open_breakdowns.length > 1 ? "s" : "" })
          }
          delay={0}
        />
        <Stat
          label={t("Breakdowns")}
          value={kpis.data ? kpis.data.breakdowns.toLocaleString("en-IN") : "—"}
          hint={t("Recorded in the last {0} days", { 0: days })}
          delay={60}
        />
        <Stat
          label={t("Mean time between failures")}
          value={kpis.data?.mtbf_minutes ? formatSpan(kpis.data.mtbf_minutes) : "—"}
          hint={t("Longer is better — how long a machine runs before it stops")}
          delay={120}
        />
        <Stat
          label={t("Mean time to repair")}
          value={kpis.data?.mttr_minutes ? formatMinutes(kpis.data.mttr_minutes) : "—"}
          hint={t("Shorter is better — stop to running again")}
          delay={180}
        />
      </Grid>

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,25rem)]">
        {/* ---- The desk: what needs a person right now ------------------- */}
        <div className="min-w-0 space-y-8">
          {nothingOpen ? (
            <Empty
              icon={<CheckCircle2 className="size-5" />}
              title={t("Nothing needs you right now")}
              hint="Alerts arrive here the moment a stop is routed to maintenance. Every breakdown you close also asks you what fixed it — that is what the plant remembers."
            />
          ) : null}

          {/* §1, duty one: "Receives escalation alerts" */}
          {data.alerts.length > 0 ? (
            <Section title={t("Alerts nobody has answered")}>
              <div className="space-y-2.5">
                {data.alerts.map((alert) => (
                  <AlertCard
                    key={alert.id}
                    alert={alert}
                    onAcknowledge={() => acknowledge.mutate(alert.id)}
                    pending={acknowledge.isPending && acknowledge.variables === alert.id}
                  />
                ))}
              </div>
            </Section>
          ) : null}

          {data.open_breakdowns.length > 0 ? (
            <Section title={t("Stopped now")}>
              <div className="space-y-2.5">
                {data.open_breakdowns.map((stop) => (
                  <StopCard
                    key={stop.event_id}
                    stop={stop}
                    onWriteUp={() =>
                      setWriting({
                        id: stop.event_id,
                        station: stop.station_code,
                        reason: stop.reason_label ?? t("Stop"),
                      })
                    }
                  />
                ))}
              </div>
            </Section>
          ) : null}

          {/* §1, duty two: "Logs what was done to fix breakdowns". A closed
              breakdown with no note is knowledge that leaves the plant with
              whoever fixed it — the exact loss Factory Memory prevents. */}
          {data.awaiting_repair_note.length > 0 ? (
            <Section
              title={t("Fixed, but not written down")}
              description={t("Sixty seconds each. Next time this happens — to anyone, on any shift — the plant will already know what worked.")}
            >
              <div className="space-y-2.5">
                {data.awaiting_repair_note.map((event) => (
                  <UnwrittenCard
                    key={event.event_id}
                    event={event}
                    onWriteUp={() =>
                      setWriting({
                        id: event.event_id,
                        station: event.station_code,
                        reason: event.reason_label ?? t("Breakdown"),
                      })
                    }
                  />
                ))}
              </div>
            </Section>
          ) : null}

          {data.other_open_stops.length > 0 ? (
            <Section title={t("Elsewhere on the floor")} description={t("Not yours to fix — shown so you know.")}>
              <Panel className="p-0">
                <div className="divide-y">
                  {data.other_open_stops.map((stop) => (
                    <div
                      key={stop.event_id}
                      className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm"
                    >
                      <span className="font-semibold">{stop.station_code}</span>
                      <span className="text-2">{stop.reason_label ?? t("No reason yet")}</span>
                      <span className="text-3 ml-auto">{formatMinutes(stop.minutes)}</span>
                      <Money value={stop.cost_display} size="sm" tone="structural" />
                    </div>
                  ))}
                </div>
              </Panel>
            </Section>
          ) : null}
        </div>

        {/* ---- §8: "Turns 'CNC-3 keeps breaking' into a number that ranks
            their day." Without this the desk answers only what is broken now,
            and never what keeps breaking. */}
        <div className="min-w-0">
          <WorstMachines kpis={kpis.data} loading={kpis.isLoading} days={days} />
        </div>
      </div>

      {writing ? (
        <RepairSheet
          eventId={writing.id}
          station={writing.station}
          reason={writing.reason}
          onClose={() => setWriting(null)}
          onSaved={() => {
            setWriting(null);
            void queryClient.invalidateQueries({ queryKey: ["maintenance-desk"] });
          }}
        />
      ) : null}
    </Page>
  );
}

/**
 * Which machines take the most of this person's time, ranked by what their
 * breakdowns cost. Availability sits beside each one because a machine that
 * breaks often but briefly is a different problem from one that breaks rarely
 * and stays down — and the two want different fixes.
 */
function WorstMachines({
  kpis,
  loading,
  days,
}: {
  kpis: MaintenanceKpis | undefined;
  loading: boolean;
  days: number;
}) {
  const t = useT();
  const machines = (kpis?.machines ?? []).filter((m) => m.breakdowns > 0);

  return (
    <Section
      title={t("What keeps breaking")}
      description={t("Ranked by what the stops cost, last {0} days.", { 0: days })}
    >
      <Panel className="xl:sticky xl:top-24">
        {loading ? (
          <Skeleton className="h-64" />
        ) : machines.length === 0 ? (
          <Empty
            title={t("No breakdowns recorded")}
            hint={t("Machines appear here once a stop is logged against a breakdown reason.")}
          />
        ) : (
          <ol className="divide-y">
            {machines.map((machine, index) => (
              <li key={machine.station} className="py-3 first:pt-0 last:pb-0">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="flex min-w-0 items-baseline gap-2">
                    <span className="text-3 tnum w-4 shrink-0 text-xs font-semibold">
                      {index + 1}
                    </span>
                    <span className="truncate text-sm font-semibold">{machine.station}</span>
                    {machine.is_bottleneck ? (
                      <Badge variant="warn" className="shrink-0">
                        {t("Bottleneck")}
                      </Badge>
                    ) : null}
                  </span>
                  <Money value={machine.breakdown_cost_display} size="sm" tone="loss" />
                </div>

                <div className="text-3 mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-0.5 pl-6 text-xs">
                  <span>
                    <span className="tnum font-semibold text-[var(--text)]">
                      {machine.breakdowns}
                    </span>{" "}
                    {machine.breakdowns === 1 ? t("stop") : t("stops")}
                  </span>
                  <span>{t("{0} down", { 0: formatMinutes(machine.breakdown_minutes) })}</span>
                  {machine.breakdowns > 1 && machine.mttr_minutes ? (
                    <span>{t("MTTR {0}", { 0: formatMinutes(machine.mttr_minutes) })}</span>
                  ) : null}
                </div>

                <div className="mt-2 pl-6">
                  <div className="mb-1 flex items-baseline justify-between gap-2">
                    <span className="text-3 text-[0.6875rem]">{t("Availability")}</span>
                    <span className="tnum text-[0.6875rem] font-semibold">
                      {Math.round(machine.availability * 100)}%
                    </span>
                  </div>
                  <Gauge value={machine.availability * 100} height={6} />
                </div>
              </li>
            ))}
          </ol>
        )}
      </Panel>
    </Section>
  );
}

function AlertCard({
  alert,
  onAcknowledge,
  pending,
}: {
  alert: Escalation;
  onAcknowledge: () => void;
  pending: boolean;
}) {
  const t = useT();
  return (
    <Card className="border-[color-mix(in_oklab,var(--color-stopped)_35%,transparent)]">
      <CardContent className="flex flex-wrap items-center gap-4 p-4">
        <AlertTriangle className="size-5 shrink-0 text-[var(--color-stopped)]" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            {alert.station_code ?? t("A machine")}
            <Badge variant="stopped" className="ml-2">
              {t("L{0}", { 0: alert.level })}
            </Badge>
          </p>
          <p className="text-2 mt-0.5 text-sm">
            {alert.message ?? t("Escalated to maintenance.")}
          </p>
          <p className="text-3 mt-1 text-xs">
            {t("Sent {0}{1}", { 0: formatClock(alert.sent_at), 1: alert.recipient_name ? ` to ${alert.recipient_name}` : "" })}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Money value={alert.cost_at_send_display} tone="loss" size="sm" />
          {/* "On it." Recorded with a name and a time, like the escalation
              itself — §3 Layer 2: "nobody can later say they didn't know." */}
          <Button size="sm" variant="outline" onClick={onAcknowledge} disabled={pending}>
            {pending ? t("Saving…") : t("On it")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function StopCard({ stop, onWriteUp }: { stop: OpenStop; onWriteUp: () => void }) {
  const t = useT();
  // The server's figure is authoritative; between refetches the minutes count
  // on locally so the card does not read as frozen while a line is down.
  const minutes = Math.max(stop.minutes, Math.round(minutesSince(stop.started_at)));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {stop.station_code}
          <span className="text-2 text-sm font-normal">{stop.station_name}</span>
          {stop.is_breakdown ? <Badge variant="stopped">{t("Breakdown")}</Badge> : null}
          {stop.escalation_level > 0 ? (
            <Badge variant="warn">{t("Escalated L{0}", { 0: stop.escalation_level })}</Badge>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap items-end justify-between gap-4 pt-0">
        <div className="min-w-0">
          <p className="text-sm font-medium">{stop.reason_label ?? t("No reason logged yet")}</p>
          <p className="text-3 mt-0.5 text-xs">
            {t("Stopped {0} · {1}", { 0: formatClock(stop.started_at), 1: formatMinutes(minutes) })}
          </p>
          {stop.note ? <p className="text-2 mt-1.5 text-sm">{stop.note}</p> : null}
          {stop.repair_note ? (
            <p className="text-2 mt-2 flex items-start gap-1.5 text-sm">
              <Wrench className="mt-0.5 size-3.5 shrink-0" />
              <span>
                {stop.repair_note}
                {stop.repaired_by_name ? (
                  <span className="text-3"> — {stop.repaired_by_name}</span>
                ) : null}
              </span>
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-3">
          <Money value={stop.cost_display} tone="loss" size="lg" live />
          <Button size="sm" variant={stop.repair_note ? "subtle" : "primary"} onClick={onWriteUp}>
            {stop.repair_note ? t("Edit note") : t("What did you do?")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function UnwrittenCard({
  event,
  onWriteUp,
}: {
  event: ClosedBreakdown;
  onWriteUp: () => void;
}) {
  const t = useT();
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-4 p-4">
        <BookOpen className="text-3 size-5 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            {event.station_code}
            <span className="text-2 ml-2 font-normal">{event.reason_label}</span>
          </p>
          <p className="text-3 mt-0.5 text-xs">
            {t("{0}–{1} · {2} · cost {3}", { 0: formatClock(event.started_at), 1: formatClock(event.ended_at), 2: formatMinutes(event.minutes), 3: formatInr(event.cost_paise) })}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={onWriteUp}>
          {t("Write it up")}
        </Button>
      </CardContent>
    </Card>
  );
}

/**
 * The repair note. Two fields, sixty seconds.
 *
 * The second one mirrors Factory Memory's own second capture question — "what
 * did you replace or change?" (§3, Layer 4) — so a fix written here answers the
 * question the Copilot will be asked about this machine next year.
 */
function RepairSheet({
  eventId,
  station,
  reason,
  onClose,
  onSaved,
}: {
  eventId: string;
  station: string;
  reason: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const [note, setNote] = useState("");
  const [part, setPart] = useState("");

  const save = useMutation({
    mutationFn: () => api.logRepair(eventId, note.trim(), part.trim()),
    onSuccess: onSaved,
  });

  return (
    <Sheet title={t("{0} — what did you do?", { 0: station })} subtitle={reason} onClose={onClose}>
      <div className="space-y-4">
        {save.error ? <ErrorNote message={(save.error as Error).message} /> : null}

        <div>
          <Label htmlFor="repair-note">{t("What fixed it?")}</Label>
          <Textarea
            id="repair-note"
            rows={3}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t("Replaced the insert on Station 3 and reset the offset.")}
            autoFocus
          />
        </div>

        <div>
          <Label htmlFor="repair-part">{t("What did you replace or change? (optional)")}</Label>
          <Input
            id="repair-part"
            value={part}
            onChange={(e) => setPart(e.target.value)}
            placeholder={t("CNMG 120408 insert")}
          />
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>
            {t("Cancel")}
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={!note.trim() || save.isPending}
          >
            {save.isPending ? t("Saving…") : t("Save")}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}
