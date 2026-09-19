"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ClipboardCheck, Lock, LockOpen, Package, Pencil, Timer } from "lucide-react";
import { ApiError, api, loadSession } from "@/lib/api";
import { can, ADMINS } from "@/lib/roles";
import { formatClock, formatMinutes, activeLocale } from "@/lib/format";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import {
  AuditTrail, DowntimeCorrectionSheet, ProductionCorrectionSheet, QualityCorrectionSheet,
} from "@/components/corrections";
import type { DowntimeEvent, ShiftEntryProduction, ShiftEntryQuality } from "@/types/api";
import { useT } from "@/lib/i18n";

type Editing =
  | { kind: "downtime"; row: DowntimeEvent }
  | { kind: "production"; row: ShiftEntryProduction }
  | { kind: "quality"; row: ShiftEntryQuality };

/**
 * One shift, as recorded: stops, counts and quality batches.
 *
 * "Correct" appears only when the server says this login may correct this
 * shift (`can_correct`). Owners and consultants also get each entry's history
 * — what it was, what it became, who changed it and why.
 */
export default function ShiftEntriesPage({ params }: { params: Promise<{ id: string }> }) {
  const t = useT();
  const { id } = use(params);
  const session = loadSession();
  const seesAudit = session ? can(session.roles, ...ADMINS) : false;
  const [editing, setEditing] = useState<Editing | null>(null);

  const entries = useQuery({
    queryKey: ["shift-entries", id],
    queryFn: () => api.shiftEntries(id),
    retry: (count, e) => !(e instanceof ApiError && e.status === 404) && count < 1,
  });

  const back = (
    <Link href="/shifts" className="text-2 inline-flex min-h-11 items-center text-sm hover:underline">
      {t("← Shift history")}
    </Link>
  );

  if (entries.isLoading) return <Skeleton className="h-96" />;
  if (entries.error) {
    const notFound = entries.error instanceof ApiError && entries.error.status === 404;
    return (
      <div className="space-y-4">
        {back}
        {notFound ? (
          <Empty title={t("Shift not found")} hint={t("It may belong to another plant, or the link is wrong.")} />
        ) : (
          <div className="space-y-3">
            <ErrorNote message={t("Could not load this shift: {0}", { 0: entries.error.message })} />
            <Button size="lg" variant="outline" onClick={() => void entries.refetch()}>
              {t("Try again")}
            </Button>
          </div>
        )}
      </div>
    );
  }
  if (!entries.data) return null;
  const { shift, can_correct: canCorrect, downtime, production, quality } = entries.data;
  const locked = Boolean(shift.locked_at);

  const correct = (next: Editing) => (canCorrect ? (
    <Button size="sm" variant="outline" className="h-11 px-4" onClick={() => setEditing(next)}>
      <Pencil className="size-3.5" />
      {t("Correct")}
    </Button>
  ) : null);

  return (
    <div className="space-y-6">
      {back}

      <header className="rise surface elev-2 flex flex-wrap items-end justify-between gap-4 rounded-[var(--radius-card)] border p-5">
        <div>
          <p className="label mb-1.5">{t("Shift history")}</p>
          <h1 className="display text-[1.75rem] font-bold">
            {shift.shift_name} ·{" "}
            {new Date(shift.business_date).toLocaleDateString(activeLocale(), { weekday: "short", day: "numeric", month: "short" })}
          </h1>
          <p className="text-2 mt-1 text-sm">
            {t("{0}–{1} · {2} stop{3} · {4} count{5} · {6} quality batch{7}", { 0: formatClock(shift.started_at), 1: formatClock(shift.ended_at), 2: downtime.length, 3: downtime.length === 1 ? "" : "s", 4: production.length, 5: production.length === 1 ? "" : "s", 6: quality.length, 7: quality.length === 1 ? "" : "es" })}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={shift.status === "open" ? "running" : "neutral"} className="capitalize">
            {shift.status.replace(/_/g, " ")}
          </Badge>
          <Badge variant={locked ? "neutral" : "warn"} className="gap-1.5">
            {locked ? <Lock className="size-3" /> : <LockOpen className="size-3" />}
            {locked ? t("Locked {0}", { 0: formatClock(shift.locked_at) }) : t("Not locked")}
          </Badge>
        </div>
      </header>

      {!canCorrect ? (
        <p className="text-2 rounded-xl border border-dashed px-4 py-3 text-sm">
          {locked
            ? t("This shift is locked. Only the owner can correct it now.")
            : t("You can view this shift. Corrections are made by the supervisor or the owner.")}
        </p>
      ) : null}

      {/* ---- downtime ------------------------------------------------------ */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            <Timer className="size-3.5" />
            {t("Stops (")}{downtime.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {downtime.length === 0 ? (
            <p className="text-3 py-4 text-center text-sm">{t("No stops recorded on this shift.")}</p>
          ) : (
            <ul className="divide-y">
              {downtime.map((event) => (
                <li key={event.id} className="py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold">
                        {event.station_code ?? "—"}
                        <span className="text-2 font-normal"> · {event.reason_label ?? t("No reason")}</span>
                      </p>
                      <p className="text-3 text-xs">
                        {formatClock(event.started_at)}–{event.ended_at ? formatClock(event.ended_at) : t("still open")}
                        {event.duration_minutes !== null ? ` · ${formatMinutes(event.duration_minutes)}` : ""}
                        {event.source && event.source !== "manual" ? ` · ${event.source.replace(/_/g, " ")}` : ""}
                      </p>
                      {event.note ? <p className="text-2 mt-0.5 text-sm">“{event.note}”</p> : null}
                    </div>
                    <Money value={event.cost_display} tone="loss" size="sm" />
                    {correct({ kind: "downtime", row: event })}
                  </div>
                  {seesAudit ? <AuditTrail entityType="downtime_event" entityId={event.id} /> : null}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ---- production ---------------------------------------------------- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            <Package className="size-3.5" />
            {t("Production counts (")}{production.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {production.length === 0 ? (
            <p className="text-3 py-4 text-center text-sm">{t("No counts recorded on this shift.")}</p>
          ) : (
            <ul className="divide-y">
              {production.map((entry) => (
                <li key={entry.id} className="py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold">{entry.station_code ?? "—"}</p>
                      <p className="text-3 text-xs">
                        {formatClock(entry.recorded_at)}
                        {entry.source && entry.source !== "manual" ? ` · ${entry.source.replace(/_/g, " ")}` : ""}
                      </p>
                    </div>
                    <span className="money text-lg font-bold">{entry.count.toLocaleString("en-IN")}</span>
                    <span className="text-3 text-xs">{t("parts")}</span>
                    {correct({ kind: "production", row: entry })}
                  </div>
                  {seesAudit ? <AuditTrail entityType="production_count" entityId={entry.id} /> : null}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ---- quality ------------------------------------------------------- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            <ClipboardCheck className="size-3.5" />
            {t("Quality batches (")}{quality.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {quality.length === 0 ? (
            <p className="text-3 py-4 text-center text-sm">{t("No quality batches recorded on this shift.")}</p>
          ) : (
            <ul className="divide-y">
              {quality.map((entry) => (
                <li key={entry.id} className="py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-semibold">
                        {entry.part_number ?? "—"}
                        {entry.station_code ? <span className="text-2 font-normal"> · {entry.station_code}</span> : null}
                      </p>
                      <p className="text-2 text-sm">
                        {t("{0} good{1}, {2} rejected{3}", { 0: entry.good_count, 1: entry.rework_count ? t(" ({0} after rework)", { 0: entry.rework_count }) : "", 2: entry.reject_count, 3: entry.reject_count && entry.disposition ? ` · ${entry.disposition === "use_as_is" ? "used as is" : entry.disposition}` : "" })}
                      </p>
                      <p className="text-3 text-xs">
                        {formatClock(entry.recorded_at)}
                        {(entry.defects ?? []).length
                          ? ` · ${entry.defects.map((d) => `${d.label ?? d.code} ×${d.count}`).join(", ")}`
                          : ""}
                      </p>
                    </div>
                    <Money value={entry.cost_display} tone="loss" size="sm" />
                    {correct({ kind: "quality", row: entry })}
                  </div>
                  {seesAudit ? <AuditTrail entityType="quality_record" entityId={entry.id} /> : null}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {editing?.kind === "downtime" ? (
        <DowntimeCorrectionSheet event={editing.row} onClose={() => setEditing(null)} />
      ) : null}
      {editing?.kind === "production" ? (
        <ProductionCorrectionSheet entry={editing.row} onClose={() => setEditing(null)} />
      ) : null}
      {editing?.kind === "quality" ? (
        <QualityCorrectionSheet entry={editing.row} onClose={() => setEditing(null)} />
      ) : null}
    </div>
  );
}
