"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Lock, LockOpen, ShieldAlert, Timer } from "lucide-react";
import { api } from "@/lib/api";
import { formatMinutes, activeLocale } from "@/lib/format";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Textarea } from "@/components/ui/input";
import { ErrorNote, Skeleton } from "@/components/ui/misc";
import { RankedBars } from "@/components/charts";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

/**
 * Screen 7 — Morning Meeting Mode.
 *
 *   "The meeting screen shows yesterday's locked numbers — nobody argues about
 *    them because nobody can edit them."  (§6, 8:00 AM)
 *
 * Two behaviours the spec is precise about, and both are load-bearing:
 *
 *  1. Not every issue goes to RCA. Only those that cleared the severity bar.
 *     Small losses stay visible and costed, and generate no work (§5.3) —
 *     otherwise nobody does five 5-Whys before 8 AM and RCA dies.
 *  2. Today's Plan is gated: it stays shut until yesterday's escalated Actions
 *     are each marked Done or Delayed-with-reason.
 *
 * Everything is scaled up: this is projected on a wall, not held at arm's
 * length, and §7 asks for it to be readable from five metres.
 */
export default function Meeting() {
  const t = useT();
  const queryClient = useQueryClient();
  const pack = useQuery({ queryKey: ["meeting"], queryFn: () => api.meetingPack() });
  const [delaying, setDelaying] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const mark = useMutation({
    mutationFn: ({ id, status, why }: { id: string; status: "done" | "delayed"; why?: string }) =>
      api.updateAction(id, { meeting_status: status, delay_reason: why ?? null }),
    onSuccess: () => {
      setDelaying(null);
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["meeting"] });
    },
  });

  if (pack.isLoading) return <Skeleton className="h-[32rem]" />;
  if (pack.error) {
    return (
      <div className="space-y-3">
        <ErrorNote message={t("Could not load the meeting pack: {0}", { 0: pack.error.message })} />
        <Button size="lg" variant="outline" onClick={() => void pack.refetch()}>
          {t("Try again")}
        </Button>
      </div>
    );
  }
  if (!pack.data) return null;
  const d = pack.data;

  return (
    <div className="meeting-scale space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5">{t("Morning meeting")}</p>
          <h1 className="display text-[2rem] font-bold">
            {new Date(d.business_date).toLocaleDateString(activeLocale(), {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </h1>
        </div>
        <Badge
          variant={d.locked ? "recovered" : "warn"}
          className="gap-1.5 px-3 py-1.5 text-sm"
        >
          {d.locked ? <Lock className="size-3.5" /> : <LockOpen className="size-3.5" />}
          {d.locked ? t("Numbers locked") : t("Shift not yet closed")}
        </Badge>
      </header>

      {/* ---- the three headline numbers ---------------------------------- */}
      <div className="grid gap-4 sm:grid-cols-3">
        <HeadlineCard
          label={t("Recoverable")}
          hint={t("Money the plant can get back")}
          accent="var(--color-loss)"
          delay={0}
        >
          <Money value={d.losses.recoverable_total_display} tone="loss" size="hero" />
        </HeadlineCard>

        <HeadlineCard
          label={t("Structural")}
          hint={t("Real, and not recoverable — never added above")}
          accent="var(--color-structural)"
          delay={70}
        >
          <Money value={d.losses.structural_total_display} tone="structural" size="hero" />
        </HeadlineCard>

        <HeadlineCard
          label={t("Downtime")}
          hint={t("Across every machine, all shifts")}
          accent="var(--color-accent)"
          delay={140}
        >
          <span className="figure text-[clamp(2.5rem,5vw,4rem)] font-bold leading-[0.98]">
            {Math.round(d.losses.total_minutes)}
            <span className="text-2 ml-1.5 text-xl font-semibold">{t("min")}</span>
          </span>
        </HeadlineCard>
      </div>

      {/* ---- where it went ----------------------------------------------- */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="rise" style={{ animationDelay: "210ms" }}>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5">
              <Timer className="size-3.5" />
              {t("Top downtime losses")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {d.losses.recoverable.length === 0 ? (
              <p className="text-3 py-10 text-center text-sm">{t("Nothing recoverable logged.")}</p>
            ) : (
              <>
                <RankedBars
                  items={d.losses.recoverable.slice(0, 5).map((line) => ({
                    label: line.reason,
                    sublabel: `${line.station} · ${formatMinutes(line.minutes)} · ${line.event_count}×`,
                    value: line.cost,
                    display: line.cost_display,
                  }))}
                />
                {d.losses.structural.length ? (
                  <div className="mt-5 border-t pt-4">
                    <p className="label mb-2.5">{t("Structural — logged, not actionable")}</p>
                    <ul className="space-y-1.5">
                      {d.losses.structural.slice(0, 3).map((line) => (
                        <li
                          key={`${line.station}-${line.reason}`}
                          className="flex items-center justify-between gap-3 text-sm"
                        >
                          <span className="text-2 truncate">
                            {line.reason}
                            <span className="text-3"> · {line.station}</span>
                          </span>
                          <Money value={line.cost_display} tone="structural" size="sm" />
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </>
            )}
          </CardContent>
        </Card>

        <Card className="rise" style={{ animationDelay: "280ms" }}>
          <CardHeader>
            <CardTitle className="flex items-center gap-1.5">
              <ShieldAlert className="size-3.5" />
              {t("Top quality losses by ₹")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {d.top_quality.length === 0 ? (
              <p className="text-3 py-10 text-center text-sm">{t("No rejections recorded.")}</p>
            ) : (
              <RankedBars
                items={d.top_quality.map((defect) => ({
                  label: defect.label,
                  sublabel: `${defect.count} parts`,
                  value: defect.cost,
                  display: defect.cost_display,
                }))}
              />
            )}
          </CardContent>
        </Card>
      </div>

      {/* ---- the gate ----------------------------------------------------- */}
      <Card
        className={cn("rise", !d.actions_gate_cleared && "border-[var(--color-warn)]")}
        style={{ animationDelay: "350ms" }}
      >
        <CardHeader className="justify-between">
          <CardTitle className="flex items-center gap-1.5">
            {d.actions_gate_cleared ? (
              <CheckCircle2 className="size-3.5" />
            ) : (
              <ShieldAlert className="size-3.5" />
            )}
            {d.actions_gate_cleared ? t("Today's plan") : t("Close yesterday first")}
          </CardTitle>
          <Badge
            variant={d.actions_gate_cleared ? "recovered" : "warn"}
            className="ml-auto"
          >
            {d.actions_gate_cleared
              ? t("Gate cleared")
              : t("{0} unaddressed", { 0: d.blocking_actions.length })}
          </Badge>
        </CardHeader>
        <CardContent>
          {!d.actions_gate_cleared ? (
            <p className="text-2 mb-4 text-sm leading-relaxed">
              {t("Each of these was escalated to root-cause work because it cleared the severity bar. Mark it")}{" "}<strong>{t("Done")}</strong>{t(", or")}{" "}<strong>{t("Delayed")}</strong>{" "}
              {t("with a reason. Silence is not an option — that is the discipline.")}
            </p>
          ) : null}

          {mark.error ? (
            <div className="mb-3">
              <ErrorNote message={t("Could not record that: {0}", { 0: mark.error.message })} />
            </div>
          ) : null}

          <ul className="divide-y">
            {d.open_actions.map((action) => (
              <li key={action.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/actions/${action.id}`}
                      className="font-semibold hover:text-[var(--color-accent)]"
                    >
                      {action.title}
                    </Link>
                    {action.routed_to_rca ? <Badge variant="warn">RCA</Badge> : null}
                    {/* Today's mark only: a "delayed" from last week's meeting must
                        not make an action look handled this morning. */}
                    {action.meeting_status_today ? (
                      <Badge
                        variant={action.meeting_status_today === "done" ? "recovered" : "neutral"}
                      >
                        {action.meeting_status_today === "done" ? t("Done today") : t("Delayed today")}
                      </Badge>
                    ) : null}
                  </div>
                  <p className="text-3 mt-0.5 text-xs">
                    {t("{0} · {1} · due {2}", { 0: action.reference, 1: action.owner ?? "unassigned", 2: action.due_date ?? "—" })}
                  </p>
                </div>
                <Money value={action.cost_display} tone="loss" size="sm" />
                {action.routed_to_rca && !action.meeting_status_today ? (
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      className="h-11 px-4"
                      variant="go"
                      disabled={mark.isPending}
                      onClick={() => mark.mutate({ id: action.id, status: "done" })}
                    >
                      {t("Done")}
                    </Button>
                    <Button size="sm" className="h-11 px-4" variant="outline" onClick={() => setDelaying(action.id)}>
                      {t("Delayed")}
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>

          {delaying ? (
            <div className="surface-2 mt-4 rounded-xl border p-4">
              <Label htmlFor="delay-reason" className="mb-2 font-semibold">{t("Why did it slip?")}</Label>
              <Textarea
                id="delay-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                placeholder={t("Spare not available until Thursday")}
                autoFocus
              />
              <div className="mt-3 flex gap-2">
                <Button
                  size="sm"
                  className="h-11 px-4"
                  disabled={!reason.trim() || mark.isPending}
                  onClick={() => mark.mutate({ id: delaying, status: "delayed", why: reason.trim() })}
                >
                  {t("Record")}
                </Button>
                <Button size="sm" className="h-11 px-4" variant="ghost" onClick={() => setDelaying(null)}>
                  {t("Cancel")}
                </Button>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

function HeadlineCard({
  label,
  hint,
  accent,
  delay,
  children,
}: {
  label: string;
  hint: string;
  accent: string;
  delay: number;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{ animationDelay: `${delay}ms` }}
      className="rise surface elev-2 relative overflow-hidden rounded-[var(--radius-card)] border p-6"
    >
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-[3px]"
        style={{ background: `linear-gradient(90deg, ${accent}, transparent)` }}
      />
      <p className="label mb-3">{label}</p>
      {children}
      <p className="text-3 mt-3 text-xs leading-relaxed">{hint}</p>
    </div>
  );
}
