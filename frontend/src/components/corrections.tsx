"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import { formatClock, formatDate } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { ErrorNote, Skeleton } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import type {
  AuditEntityType, DowntimeCorrection, DowntimeEvent, QualityCorrection, ShiftEntryProduction, ShiftEntryQuality,
} from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * Correcting a recorded entry after the fact.
 *
 * The shift lock is what makes the numbers honest, so a correction is never a
 * quiet edit: it asks why, the server writes the before and after to the audit
 * log with that reason, and a supervisor can only correct a shift that is not
 * locked yet. Only fields that actually changed are sent.
 */

const MIN_WHY = 5;

/** ISO → the value a datetime-local input shows, in the tablet's clock. */
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(value: string): string {
  return new Date(value).toISOString();
}

const digits = (value: string) => value.replace(/\D/g, "");

function invalidateAfterCorrection(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["shift-entries"] });
  void queryClient.invalidateQueries({ queryKey: ["audit"] });
  void queryClient.invalidateQueries({ queryKey: ["kpis"] });
  void queryClient.invalidateQueries({ queryKey: ["grid"] });
}

function WhyField({ id, value, onChange }: { id: string; value: string; onChange: (value: string) => void }) {
  const t = useT();
  return (
    <div>
      <Label htmlFor={id}>{t("Why is this being corrected?")}</Label>
      <Textarea
        id={id}
        rows={2}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={t("Stop was logged against CNC-3, it was CNC-4")}
        aria-describedby={`${id}-hint`}
      />
      <p id={`${id}-hint`} className="text-3 mt-1 text-xs">
        {t("Required. Saved to the audit log with the old and new values.")}
      </p>
    </div>
  );
}

function SheetButtons({ busy, onClose }: { busy: boolean; onClose: () => void }) {
  const t = useT();
  return (
    <div className="flex flex-wrap gap-2">
      <Button type="submit" size="touch" disabled={busy}>
        {busy ? t("Saving…") : t("Save correction")}
      </Button>
      <Button type="button" size="touch" variant="ghost" onClick={onClose}>
        {t("Cancel")}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------

export function DowntimeCorrectionSheet({ event, onClose }: { event: DowntimeEvent; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const reasons = useQuery({ queryKey: ["reasons"], queryFn: api.reasonCodes });
  const [reasonId, setReasonId] = useState(event.reason_code_id ?? "");
  const [startedAt, setStartedAt] = useState(toLocalInput(event.started_at));
  const [endedAt, setEndedAt] = useState(toLocalInput(event.ended_at));
  const [note, setNote] = useState(event.note ?? "");
  const [why, setWhy] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (payload: DowntimeCorrection) => api.correctDowntime(event.id, payload),
    onSuccess: () => {
      invalidateAfterCorrection(queryClient);
      onClose();
    },
  });

  return (
    <Sheet
      title={t("Correct stop · {0}", { 0: event.station_code ?? "station" })}
      subtitle={`${event.reason_label ?? t("No reason")} · ${formatClock(event.started_at)}–${formatClock(event.ended_at)}`}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const payload: DowntimeCorrection = { reason: why.trim() };
          if (reasonId && reasonId !== (event.reason_code_id ?? "")) payload.reason_code_id = reasonId;
          if (startedAt && startedAt !== toLocalInput(event.started_at)) payload.started_at = fromLocalInput(startedAt);
          if (endedAt && endedAt !== toLocalInput(event.ended_at)) payload.ended_at = fromLocalInput(endedAt);
          if (note.trim() !== (event.note ?? "")) payload.note = note.trim();

          if (Object.keys(payload).length === 1) return setInvalid(t("Nothing has been changed yet."));
          if (startedAt && endedAt && new Date(endedAt) <= new Date(startedAt)) {
            return setInvalid(t("The end time must be after the start time."));
          }
          if (why.trim().length < MIN_WHY) return setInvalid(t("Say why in at least {0} characters.", { 0: MIN_WHY }));
          setInvalid(null);
          save.mutate(payload);
        }}
      >
        <div>
          <Label htmlFor="fix-dt-reason">{t("Reason")}</Label>
          {reasons.error ? (
            <ErrorNote message={t("Could not load reason codes: {0}", { 0: reasons.error.message })} />
          ) : (
            <Select id="fix-dt-reason" value={reasonId} disabled={reasons.isLoading} onChange={(e) => setReasonId(e.target.value)}>
              <option value="">{reasons.isLoading ? t("Loading…") : t("No reason")}</option>
              {(reasons.data ?? []).map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label} ({r.recoverability === "R" ? "recoverable" : "structural"})
                </option>
              ))}
            </Select>
          )}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="fix-dt-start">{t("Stopped at")}</Label>
            <Input id="fix-dt-start" type="datetime-local" value={startedAt} onChange={(e) => setStartedAt(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="fix-dt-end">{t("Running again at")}</Label>
            <Input
              id="fix-dt-end"
              type="datetime-local"
              value={endedAt}
              disabled={!event.ended_at}
              onChange={(e) => setEndedAt(e.target.value)}
              aria-describedby={event.ended_at ? undefined : "fix-dt-end-hint"}
            />
            {!event.ended_at ? (
              <p id="fix-dt-end-hint" className="text-3 mt-1 text-xs">
                {t("Still open — close it from the grid.")}
              </p>
            ) : null}
          </div>
        </div>
        <div>
          <Label htmlFor="fix-dt-note">{t("Note (optional)")}</Label>
          <Textarea id="fix-dt-note" rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <WhyField id="fix-dt-why" value={why} onChange={setWhy} />
        <p className="text-3 text-xs">{t("The cost is recomputed with the rates frozen when the stop happened.")}</p>
        {invalid ? <ErrorNote message={invalid} /> : null}
        {save.error ? <ErrorNote message={save.error.message} /> : null}
        <SheetButtons busy={save.isPending} onClose={onClose} />
      </form>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------

export function ProductionCorrectionSheet({ entry, onClose }: { entry: ShiftEntryProduction; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [count, setCount] = useState(String(entry.count));
  const [why, setWhy] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (value: number) => api.correctProduction(entry.id, { count: value, reason: why.trim() }),
    onSuccess: () => {
      invalidateAfterCorrection(queryClient);
      onClose();
    },
  });

  return (
    <Sheet
      title={t("Correct count · {0}", { 0: entry.station_code ?? "station" })}
      subtitle={t("{0} parts entered at {1}", { 0: entry.count, 1: formatClock(entry.recorded_at) })}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const value = Number.parseInt(count, 10);
          if (!Number.isFinite(value) || value < 0) return setInvalid(t("Enter the count as a whole number, 0 or more."));
          if (value === entry.count) return setInvalid(t("That is the count already recorded."));
          if (why.trim().length < MIN_WHY) return setInvalid(t("Say why in at least {0} characters.", { 0: MIN_WHY }));
          setInvalid(null);
          save.mutate(value);
        }}
      >
        <div className="max-w-xs">
          <Label htmlFor="fix-prod-count">{t("Parts in this entry")}</Label>
          <Input
            id="fix-prod-count"
            inputMode="numeric"
            value={count}
            onChange={(e) => setCount(digits(e.target.value))}
            className="money h-16 text-center text-3xl font-bold"
          />
        </div>
        <WhyField id="fix-prod-why" value={why} onChange={setWhy} />
        {invalid ? <ErrorNote message={invalid} /> : null}
        {save.error ? <ErrorNote message={save.error.message} /> : null}
        <SheetButtons busy={save.isPending} onClose={onClose} />
      </form>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------

export function QualityCorrectionSheet({ entry, onClose }: { entry: ShiftEntryQuality; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const defectCodes = useQuery({ queryKey: ["defects"], queryFn: api.defectCodes });
  const [good, setGood] = useState(String(entry.good_count));
  const [reject, setReject] = useState(String(entry.reject_count));
  const [rework, setRework] = useState(String(entry.rework_count));
  const [reworkMinutes, setReworkMinutes] = useState("");
  const [disposition, setDisposition] = useState(entry.disposition === "use_as_is" ? "use_as_is" : "scrap");
  const original = Object.fromEntries((entry.defects ?? []).map((d) => [d.code, d.count]));
  const [defects, setDefects] = useState<Record<string, number>>(original);
  const [why, setWhy] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (payload: QualityCorrection) => api.correctQuality(entry.id, payload),
    onSuccess: () => {
      invalidateAfterCorrection(queryClient);
      onClose();
    },
  });

  // Codes on the entry that are no longer in the master list still need a row.
  const codes = [
    ...(defectCodes.data ?? []).map((d) => ({ code: d.code, label: d.label })),
    ...(entry.defects ?? [])
      .filter((d) => !(defectCodes.data ?? []).some((m) => m.code === d.code))
      .map((d) => ({ code: d.code, label: d.label ?? d.code })),
  ];

  const n = (value: string) => Number(value || 0);

  return (
    <Sheet
      title={t("Correct quality batch · {0}", { 0: entry.part_number ?? "part" })}
      subtitle={t("{0} good, {1} rejected · {2}", { 0: entry.good_count, 1: entry.reject_count, 2: formatClock(entry.recorded_at) })}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const payload: QualityCorrection = { reason: why.trim() };
          if (n(good) !== entry.good_count) payload.good_count = n(good);
          if (n(reject) !== entry.reject_count) payload.reject_count = n(reject);
          if (n(rework) !== entry.rework_count) payload.rework_count = n(rework);
          if (reworkMinutes) payload.rework_minutes = n(reworkMinutes);
          if (disposition !== (entry.disposition ?? "scrap")) payload.disposition = disposition as "scrap" | "use_as_is";
          const nextDefects = Object.entries(defects).filter(([, count]) => count > 0);
          const changedDefects =
            nextDefects.length !== Object.values(original).filter((c) => c > 0).length ||
            nextDefects.some(([code, count]) => original[code] !== count);
          if (changedDefects) payload.defects = nextDefects.map(([code, count]) => ({ code, count }));

          if (Object.keys(payload).length === 1) return setInvalid(t("Nothing has been changed yet."));
          if (n(rework) > n(good)) return setInvalid(t("Reworked parts are counted inside the good parts — reworked cannot be more than good."));
          const assigned = nextDefects.reduce((sum, [, count]) => sum + count, 0);
          if (assigned > n(reject) + n(rework)) {
            return setInvalid(t("{0} parts have a defect code, but only {1} were rejected or reworked.", { 0: assigned, 1: n(reject) + n(rework) }));
          }
          if (why.trim().length < MIN_WHY) return setInvalid(t("Say why in at least {0} characters.", { 0: MIN_WHY }));
          setInvalid(null);
          save.mutate(payload);
        }}
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <Label htmlFor="fix-q-good">{t("Good")}</Label>
            <Input id="fix-q-good" inputMode="numeric" value={good} onChange={(e) => setGood(digits(e.target.value))} className="money text-lg font-semibold" />
          </div>
          <div>
            <Label htmlFor="fix-q-reject">{t("Rejected")}</Label>
            <Input id="fix-q-reject" inputMode="numeric" value={reject} onChange={(e) => setReject(digits(e.target.value))} className="money text-lg font-semibold" />
          </div>
          <div>
            <Label htmlFor="fix-q-rework">{t("Of the good, reworked")}</Label>
            <Input id="fix-q-rework" inputMode="numeric" value={rework} onChange={(e) => setRework(digits(e.target.value))} className="money text-lg font-semibold" />
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="fix-q-disposition">{t("Rejected parts")}</Label>
            <Select id="fix-q-disposition" value={disposition} onChange={(e) => setDisposition(e.target.value)}>
              <option value="scrap">{t("Scrap")}</option>
              <option value="use_as_is">{t("Use as is")}</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="fix-q-rework-min">{t("Rework minutes (optional)")}</Label>
            <Input
              id="fix-q-rework-min"
              inputMode="numeric"
              value={reworkMinutes}
              placeholder={t("Leave blank to keep")}
              onChange={(e) => setReworkMinutes(digits(e.target.value))}
            />
          </div>
        </div>

        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">{t("Defect codes")}</legend>
          {defectCodes.isLoading ? (
            <Skeleton className="h-20" />
          ) : defectCodes.error ? (
            <ErrorNote message={t("Could not load defect codes: {0}", { 0: defectCodes.error.message })} />
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {codes.map((d) => (
                <div key={d.code} className="flex items-center gap-2 rounded-lg border p-2">
                  <label htmlFor={`fix-q-defect-${d.code}`} className="min-w-0 flex-1 truncate text-sm">
                    {d.label}
                  </label>
                  <Input
                    id={`fix-q-defect-${d.code}`}
                    inputMode="numeric"
                    value={defects[d.code] ? String(defects[d.code]) : ""}
                    onChange={(e) => setDefects((prev) => ({ ...prev, [d.code]: Number(digits(e.target.value) || 0) }))}
                    className="money h-11 w-16 text-center"
                  />
                </div>
              ))}
            </div>
          )}
        </fieldset>

        <WhyField id="fix-q-why" value={why} onChange={setWhy} />
        {invalid ? <ErrorNote message={invalid} /> : null}
        {save.error ? <ErrorNote message={save.error.message} /> : null}
        <SheetButtons busy={save.isPending} onClose={onClose} />
      </form>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------

/** The audit trail for one entry. Loads only when opened. Owners and consultants. */
export function AuditTrail({ entityType, entityId }: { entityType: AuditEntityType; entityId: string }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const trail = useQuery({
    queryKey: ["audit", entityType, entityId],
    queryFn: () => api.auditLog(entityType, entityId),
    enabled: open,
  });

  return (
    <div className="mt-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="text-2 flex min-h-11 items-center gap-1.5 text-xs font-medium hover:text-[var(--text)]"
      >
        <ChevronDown className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`} />
        {t("History")}
      </button>
      {open ? (
        trail.isLoading ? (
          <Skeleton className="h-16" />
        ) : trail.error ? (
          <ErrorNote message={t("Could not load the history: {0}", { 0: trail.error.message })} />
        ) : (trail.data ?? []).length === 0 ? (
          <p className="text-3 pb-2 text-xs">{t("No changes recorded — this is the entry as it was first made.")}</p>
        ) : (
          <ol className="surface-2 space-y-2 rounded-xl border p-3">
            {(trail.data ?? []).map((item) => {
              const keys = [...new Set([...Object.keys(item.before ?? {}), ...Object.keys(item.after ?? {})])].filter(
                (key) => JSON.stringify(item.before?.[key]) !== JSON.stringify(item.after?.[key]),
              );
              return (
                <li key={item.id} className="text-xs">
                  <p>
                    <span className="font-semibold capitalize">{item.action.replace(/_/g, " ")}</span>
                    <span className="text-2">
                      {" "}
                      · {item.actor_name ?? "system"} · {formatDate(item.occurred_at)}, {formatClock(item.occurred_at)}
                    </span>
                  </p>
                  {item.reason ? <p className="text-2 mt-0.5">“{item.reason}”</p> : null}
                  {keys.length ? (
                    <ul className="text-3 mt-1 space-y-0.5">
                      {keys.map((key) => (
                        <li key={key}>
                          {key.replace(/_/g, " ")}: {show(item.before?.[key])} → {show(item.after?.[key])}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ol>
        )
      ) : null}
    </div>
  );
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
