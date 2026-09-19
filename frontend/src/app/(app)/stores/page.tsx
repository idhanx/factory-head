"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, PackageCheck } from "lucide-react";
import { api } from "@/lib/api";
import { formatClock, formatMinutes, minutesSince } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Empty, ErrorNote, PageHeader, Skeleton } from "@/components/ui/misc";
import { Money } from "@/components/ui/money";
import { Sheet } from "@/components/ui/sheet";
import type { Escalation, OpenStop, StagingRow } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * The stores in-charge's desk (§1).
 *
 *   Stores In-charge | Receives material-shortage alerts. Confirms material
 *   staging. | When events occur.
 *
 * §6 is the argument for both halves. At 5:45 AM the Coach asks Stores to
 * confirm Job #4471's material is at Line 2 before 8:00; at 10:40 AM Press-2
 * is stopped on a material wait with a rupee counter climbing, because nobody
 * did. The confirmation is the cheap half of that pair, and until now the
 * plant had nowhere to record it.
 *
 * Nothing on this screen is a register Stores has to keep. The queue is
 * today's job orders, which the supervisor already creates from the production
 * plan; a row is written the moment somebody answers, and never before. A
 * plant that ignores this screen is left exactly as it was — which is what
 * §4.3's 15-minute entry budget requires of every surface in the product.
 */
export default function Stores() {
  const t = useT();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<StagingRow | null>(null);

  const desk = useQuery({
    queryKey: ["stores-desk"],
    queryFn: () => api.storesDesk(),
    refetchInterval: 30_000,
  });

  const acknowledge = useMutation({
    mutationFn: (id: string) => api.acknowledgeEscalation(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["stores-desk"] }),
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

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        eyebrow={t("Stores")}
        title={t("Material today")}
        subtitle={t("Which line is waiting on you, and what the waiting has cost.")}
      />

      {/* ---- What silence has already cost -------------------------------
          §6, 10:40 AM: "Ramesh knows the owner sees the same number at shift
          end." Putting it on his own screen first is the point. */}
      <Card tone={data.wait_cost_today_paise > 0 ? "brand" : "feature"} className="mb-6">
        <CardContent className="flex flex-wrap items-center justify-between gap-4 p-5">
          <div>
            <p className="label mb-1">{t("Lost to material waits today")}</p>
            <Money value={data.wait_cost_today_display} size="xl" />
          </div>
          <p className="text-sm opacity-75">
            {data.pending_count === 0
              ? t("Every job today has an answer.")
              : t("{0} job{1} still unanswered.", { 0: data.pending_count, 1: data.pending_count > 1 ? "s" : "" })}
          </p>
        </CardContent>
      </Card>

      {/* ---- §1, duty one: "Receives material-shortage alerts" -----------
          Lines stopped on material appear whether or not the 15-minute rung
          has fired. Arriving before the escalation is the entire point. */}
      {data.open_waits.length > 0 ? (
        <section className="mb-6">
          <h2 className="label mb-2.5">{t("Stopped on material right now")}</h2>
          <div className="space-y-2.5">
            {data.open_waits.map((wait) => (
              <WaitCard key={wait.event_id} wait={wait} />
            ))}
          </div>
        </section>
      ) : null}

      {data.alerts.length > 0 ? (
        <section className="mb-6">
          <h2 className="label mb-2.5">{t("Alerts nobody has answered")}</h2>
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
        </section>
      ) : null}

      {/* ---- §1, duty two: "Confirms material staging" -------------------- */}
      <section>
        <h2 className="label mb-1.5">{t("Today's jobs")}</h2>
        <p className="text-2 mb-2.5 max-w-xl text-sm leading-relaxed">
          {t("One tap each. Saying a job is short is the most useful thing on this screen — it puts a warning against the line before it stops.")}
        </p>

        {data.queue.length === 0 ? (
          <Empty
            icon={<PackageCheck className="size-5" />}
            title={t("No jobs planned yet")}
            hint={t("Jobs appear here as the supervisor sets them up for the shift. Nothing to keep up to date in between.")}
          />
        ) : (
          <div className="space-y-2.5">
            {data.queue.map((row) => (
              <StagingCard
                key={row.job_order_id}
                row={row}
                onConfirm={() => setConfirming(row)}
              />
            ))}
          </div>
        )}
      </section>

      {confirming ? (
        <ConfirmSheet
          row={confirming}
          onClose={() => setConfirming(null)}
          onSaved={() => {
            setConfirming(null);
            void queryClient.invalidateQueries({ queryKey: ["stores-desk"] });
          }}
        />
      ) : null}
    </div>
  );
}

function WaitCard({ wait }: { wait: OpenStop }) {
  const t = useT();
  const minutes = Math.max(wait.minutes, Math.round(minutesSince(wait.started_at)));
  return (
    <Card className="border-[color-mix(in_oklab,var(--color-stopped)_35%,transparent)]">
      <CardContent className="flex flex-wrap items-center gap-4 p-4">
        <AlertTriangle className="size-5 shrink-0 text-[var(--color-stopped)]" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            {wait.station_code}
            <span className="text-2 ml-2 font-normal">{wait.reason_label}</span>
          </p>
          <p className="text-3 mt-0.5 text-xs">
            {t("Stopped {0} · {1} and counting", { 0: formatClock(wait.started_at), 1: formatMinutes(minutes) })}
          </p>
        </div>
        <Money value={wait.cost_display} tone="loss" size="lg" live />
      </CardContent>
    </Card>
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
    <Card>
      <CardContent className="flex flex-wrap items-center gap-4 p-4">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            {alert.station_code ?? t("A line")}
            <Badge variant="stopped" className="ml-2">
              {t("L{0}", { 0: alert.level })}
            </Badge>
          </p>
          <p className="text-2 mt-0.5 text-sm">{alert.message ?? t("Escalated to stores.")}</p>
          <p className="text-3 mt-1 text-xs">{t("Sent {0}", { 0: formatClock(alert.sent_at) })}</p>
        </div>
        <div className="flex items-center gap-3">
          <Money value={alert.cost_at_send_display} tone="loss" size="sm" />
          <Button size="sm" variant="outline" onClick={onAcknowledge} disabled={pending}>
            {pending ? t("Saving…") : t("On it")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function StagingCard({ row, onConfirm }: { row: StagingRow; onConfirm: () => void }) {
  const t = useT();
  const badge =
    row.status === "staged" ? (
      <Badge variant="running">
        <Check className="size-3" /> {t("Staged")}
      </Badge>
    ) : row.status === "short" ? (
      <Badge variant="stopped">{t("Short")}</Badge>
    ) : (
      <Badge variant="neutral">{t("Not confirmed")}</Badge>
    );

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-4 p-4">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            {t("Job")}{" "}{row.job_number}
            <span className="text-2 font-normal">{row.part_number}</span>
            {row.station_codes.length > 0 ? (
              <Badge variant="neutral">{row.station_codes.join(", ")}</Badge>
            ) : null}
            {badge}
          </p>
          <p className="text-3 mt-0.5 text-xs">
            {t("{0} planned{1}{2}", { 0: row.planned_quantity, 1: row.status === "short" && row.quantity_staged !== null
              ? t(" · {0} staged, {1} short", { 0: row.quantity_staged, 1: Math.max(0, (row.quantity_required ?? row.planned_quantity) - row.quantity_staged) })
              : "", 2: row.confirmed_by_name
              ? t(" · {0} at {1}", { 0: row.confirmed_by_name, 1: formatClock(row.confirmed_at) })
              : "" })}
          </p>
          {row.note ? <p className="text-2 mt-1.5 text-sm">{row.note}</p> : null}
          {/* The cost of not having answered, in the plant's own numbers. */}
          {row.wait_events > 0 ? (
            <p className="mt-1.5 text-sm text-[var(--color-loss)]">
              {t("{0} material wait{1} today · {2}", { 0: row.wait_events, 1: row.wait_events > 1 ? "s" : "", 2: row.wait_cost_display })}
            </p>
          ) : null}
        </div>
        <Button
          size="sm"
          variant={row.status === "pending" ? "primary" : "subtle"}
          onClick={onConfirm}
        >
          {row.status === "pending" ? t("Confirm") : t("Change")}
        </Button>
      </CardContent>
    </Card>
  );
}

/**
 * The confirmation. One tap for the normal case, a number for the honest one.
 *
 * 'Short' requires a quantity because 'short' without one is a complaint
 * rather than a record — and it is the record that lets §5.2's recurring
 * pattern rule notice the same job short-staging three times in five shifts.
 */
function ConfirmSheet({
  row,
  onClose,
  onSaved,
}: {
  row: StagingRow;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const [short, setShort] = useState(row.status === "short");
  const [quantity, setQuantity] = useState(
    row.quantity_staged !== null && row.status === "short" ? String(row.quantity_staged) : "",
  );
  const [note, setNote] = useState(row.note ?? "");

  const save = useMutation({
    mutationFn: () =>
      api.confirmStaging({
        job_order_id: row.job_order_id,
        status: short ? "short" : "staged",
        quantity_staged: short ? Number(quantity || 0) : null,
        note: note.trim() || null,
      }),
    onSuccess: onSaved,
  });

  const blocked = short && quantity.trim() === "";

  return (
    <Sheet
      title={t("Job {0} — {1}", { 0: row.job_number, 1: row.part_number })}
      subtitle={t("{0} planned{1}", { 0: row.planned_quantity, 1: row.station_codes.length ? ` · ${row.station_codes.join(", ")}` : "" })}
      onClose={onClose}
    >
      <div className="space-y-4">
        {save.error ? <ErrorNote message={(save.error as Error).message} /> : null}

        <div className="grid grid-cols-2 gap-3">
          <Button
            variant={short ? "outline" : "go"}
            size="touch"
            onClick={() => setShort(false)}
          >
            {t("Full quantity staged")}
          </Button>
          <Button
            variant={short ? "stop" : "outline"}
            size="touch"
            onClick={() => setShort(true)}
          >
            {t("Short")}
          </Button>
        </div>

        {short ? (
          <div>
            <Label htmlFor="staged-qty">{t("How many are actually at the line?")}</Label>
            <Input
              id="staged-qty"
              type="number"
              inputMode="numeric"
              min={0}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              autoFocus
            />
          </div>
        ) : null}

        <div>
          <Label htmlFor="staging-note">{t("Note (optional)")}</Label>
          <Textarea
            id="staging-note"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={short ? t("Balance arriving 09:30 from the supplier.") : ""}
          />
        </div>

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>
            {t("Cancel")}
          </Button>
          <Button onClick={() => save.mutate()} disabled={blocked || save.isPending}>
            {save.isPending ? t("Saving…") : t("Confirm")}
          </Button>
        </div>
      </div>
    </Sheet>
  );
}
