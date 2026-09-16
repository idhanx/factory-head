"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { enqueue } from "@/lib/offline/outbox";
import { openSyncSheet, useSync } from "@/lib/offline/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import type { QualityPayload } from "@/types/api";

/**
 * Screen 5 — Quality entry.
 *
 * Reached only by the inspector's own login, and that is the point (§2): the
 * good-parts count is entered independently of the supervisor's production
 * count, so the two reconcile. "Two people would have to collude to fake the
 * numbers."
 *
 * Inspected is good plus rejected. Rework is counted inside the good pile — how
 * many of the good parts needed a second pass first — because a reworked part
 * still ships, but it was not right first time and it cost time to make right.
 *
 * A batch is saved to the tablet first and sent from there, so an inspection
 * bay with no Wi-Fi loses nothing. The costs and the reconciliation are the
 * server's to work out; until the entry has reached it, the card says so
 * rather than showing a figure.
 */
export default function Quality() {
  const [partId, setPartId] = useState("");
  const [stationId, setStationId] = useState("");
  const [good, setGood] = useState("");
  const [reject, setReject] = useState("");
  const [rework, setRework] = useState("");
  const [reworkMinutes, setReworkMinutes] = useState("");
  const [disposition, setDisposition] = useState<QualityPayload["disposition"]>("scrap");
  const [defects, setDefects] = useState<Record<string, number>>({});
  const [last, setLast] = useState<{ ref: string; summary: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parts = useQuery({ queryKey: ["parts"], queryFn: api.parts });
  const stations = useQuery({ queryKey: ["stations"], queryFn: api.stations });
  const defectCodes = useQuery({ queryKey: ["defects"], queryFn: api.defectCodes });
  const shift = useQuery({ queryKey: ["shift"], queryFn: api.currentShift });
  const loadError = parts.error ?? stations.error ?? defectCodes.error;

  const submit = async () => {
    const payload: QualityPayload = {
      part_id: partId,
      station_id: stationId || null,
      good_count: Number(good || 0),
      reject_count: Number(reject || 0),
      rework_count: Number(rework || 0),
      rework_minutes: Number(rework || 0) > 0 && reworkMinutes ? Number(reworkMinutes) : null,
      disposition,
      defects: Object.entries(defects)
        .filter(([, count]) => count > 0)
        .map(([code, count]) => ({ code, count })),
    };
    const part = parts.data?.find((p) => p.id === partId);
    const station = stations.data?.find((st) => st.id === stationId);
    const summary = `${payload.good_count} good${payload.rework_count ? ` (${payload.rework_count} after rework)` : ""}, ${payload.reject_count} rejected`;

    setSaving(true);
    try {
      const op = await enqueue({
        kind: "quality",
        shift_session_id: shift.data?.id ?? null,
        // Blank optionals are left out rather than sent as null.
        payload: Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== null)),
        meta: {
          station_id: station?.id,
          station_code: station?.code,
          part_number: part?.part_number,
          summary,
        },
      });
      setLast({ ref: op.client_ref, summary: `${part?.part_number ?? "Batch"}: ${summary}` });
      setError(null);
      setGood("");
      setReject("");
      setRework("");
      setReworkMinutes("");
      setDisposition("scrap");
      setDefects({});
    } catch (e) {
      setError(`Could not save this batch on the tablet: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const rejectTotal = Number(reject || 0);
  const reworkTotal = Number(rework || 0);
  const goodTotal = Number(good || 0);
  const coded = rejectTotal + reworkTotal;
  const assigned = Object.values(defects).reduce((sum, n) => sum + n, 0);
  // The server refuses both of these (422); catching them here saves the
  // inspector a round trip and says which number to fix.
  const overAssigned = assigned > coded;
  const reworkTooHigh = reworkTotal > goodTotal;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="rise">
        <p className="label mb-1.5">Screen 5 · inspector</p>
        <h1 className="display text-[1.75rem] font-bold">Quality entry</h1>
        <p className="text-2 mt-1 max-w-xl text-sm leading-relaxed">
          Good and rejected parts at batch completion — about 60 seconds. Entered
          under your own login, independently of the supervisor&apos;s production
          count, so the two reconcile.
        </p>
      </header>

      {error ? <ErrorNote message={error} /> : null}
      {loadError ? <ErrorNote message={`Could not load the part and code lists: ${loadError.message}`} /> : null}

      {last ? <BatchStatus clientRef={last.ref} summary={last.summary} /> : null}

      <Card>
        <CardContent className="pt-5">
          <form
            className="space-y-5"
            onSubmit={(e) => {
              e.preventDefault();
              if (overAssigned || reworkTooHigh || saving) return;
              void submit();
            }}
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="part">Part number</Label>
                <select
                  id="part"
                  required
                  value={partId}
                  onChange={(e) => setPartId(e.target.value)}
                  className="h-11 w-full rounded-lg border surface px-2 text-sm"
                >
                  <option value="">Select…</option>
                  {(parts.data ?? []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.part_number} — {p.description}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <Label htmlFor="station">Station</Label>
                <select
                  id="station"
                  value={stationId}
                  onChange={(e) => setStationId(e.target.value)}
                  className="h-11 w-full rounded-lg border surface px-2 text-sm"
                >
                  <option value="">Not specified</option>
                  {(stations.data ?? []).map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.code}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="good">Good parts</Label>
                <Input
                  id="good"
                  required
                  inputMode="numeric"
                  value={good}
                  onChange={(e) => setGood(e.target.value.replace(/\D/g, ""))}
                  className="money h-20 text-center text-4xl font-bold"
                />
              </div>
              <div>
                <Label htmlFor="reject">Rejected</Label>
                <Input
                  id="reject"
                  inputMode="numeric"
                  value={reject}
                  onChange={(e) => setReject(e.target.value.replace(/\D/g, ""))}
                  className="money h-20 text-center text-4xl font-bold"
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="rework">Of the good parts, how many were reworked?</Label>
                <Input
                  id="rework"
                  inputMode="numeric"
                  value={rework}
                  placeholder="0"
                  onChange={(e) => setRework(e.target.value.replace(/\D/g, ""))}
                  aria-invalid={reworkTooHigh}
                  className="money text-lg font-semibold"
                />
                <p className="mt-1 text-xs text-2">
                  Good in the end, but needed a second pass first. Leave 0 if none.
                </p>
              </div>
            </div>

            {rejectTotal > 0 || reworkTotal > 0 ? (
              <div className="grid gap-4 sm:grid-cols-2">
                {rejectTotal > 0 ? (
                  <div>
                    <Label htmlFor="disposition">What happens to the rejected parts?</Label>
                    <Select
                      id="disposition"
                      value={disposition}
                      onChange={(e) => setDisposition(e.target.value as QualityPayload["disposition"])}
                    >
                      <option value="scrap">Scrap</option>
                      <option value="use_as_is">Use as is</option>
                    </Select>
                  </div>
                ) : null}
                {reworkTotal > 0 ? (
                  <div>
                    <Label htmlFor="rework-minutes">Minutes spent on rework (optional)</Label>
                    <Input
                      id="rework-minutes"
                      inputMode="numeric"
                      value={reworkMinutes}
                      onChange={(e) => setReworkMinutes(e.target.value.replace(/\D/g, ""))}
                      placeholder="45"
                    />
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* Kept on screen while any code is counted, so a count that is now
                too high can still be corrected rather than hiding. */}
            {coded > 0 || assigned > 0 ? (
              <div>
                <Label>Defect codes</Label>
                <p className="mb-2 text-xs text-2">
                  {assigned} of {coded} rejected or reworked parts assigned a code
                  {assigned < coded ? " — the rest stay uncoded" : ""}
                </p>
                <div className="grid gap-2 sm:grid-cols-3">
                  {(defectCodes.data ?? []).map((defect) => (
                    <div key={defect.id} className="flex items-center gap-2 rounded-lg border p-2">
                      <span className="min-w-0 flex-1 truncate text-sm">{defect.label}</span>
                      <Input
                        inputMode="numeric"
                        value={defects[defect.code] ?? ""}
                        onChange={(e) =>
                          setDefects((prev) => ({
                            ...prev,
                            [defect.code]: Number(e.target.value.replace(/\D/g, "") || 0),
                          }))
                        }
                        className="h-11 w-16 text-center money"
                        aria-label={`${defect.label} count`}
                      />
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {reworkTooHigh ? (
              <ErrorNote
                message={`${reworkTotal} reworked is more than the ${goodTotal} good parts. Reworked parts are counted inside the good count — lower the reworked number, or raise the good count.`}
              />
            ) : null}
            {overAssigned ? (
              <ErrorNote
                message={`${assigned} parts have a defect code, but only ${coded} were rejected or reworked. Lower the defect counts, or raise the rejected or reworked count.`}
              />
            ) : null}

            <Button type="submit" size="touch" disabled={saving || !partId || overAssigned || reworkTooHigh}>
              {saving ? "Saving…" : "Record batch"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Where the last batch stands. The cost and reconciliation are computed by the
 * server when the entry lands; this card never invents them on the tablet.
 */
function BatchStatus({ clientRef, summary }: { clientRef: string; summary: string }) {
  const op = useSync((st) => st.ops.find((o) => o.client_ref === clientRef));
  const result = useSync((st) => st.results[clientRef]);
  const online = useSync((st) => st.online);

  const view = op
    ? op.status === "rejected"
      ? { title: "Not accepted", badge: "stopped" as const, label: "Refused", note: op.message ?? "The server refused this batch." }
      : {
          title: "Saved on this tablet — will sync",
          badge: "warn" as const,
          label: online ? "Sending" : "Offline",
          note: "The scrap cost and the count check appear in reports once it reaches the server.",
        }
    : result?.status === "held"
      ? {
          title: "Held for owner review",
          badge: "warn" as const,
          label: "Held",
          note: result.message ?? "Its shift was already locked when it arrived; the owner decides whether it is added.",
        }
      : {
          title: "Recorded",
          badge: "recovered" as const,
          label: "Synced",
          note: "On the server. Scrap cost and the reconciliation with the supervisor's count are in the shift report.",
        };

  return (
    <Card
      className={
        view.badge === "stopped"
          ? "border-[var(--color-stopped)]"
          : view.badge === "warn"
            ? "border-[var(--color-warn)]"
            : "border-[var(--color-running)]"
      }
    >
      <CardHeader className="flex items-center justify-between">
        <CardTitle>{view.title}</CardTitle>
        <Badge variant={view.badge}>{view.label}</Badge>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm">{summary}</p>
        <p className={view.badge === "stopped" ? "text-sm text-[var(--color-loss)]" : "text-2 text-xs"}>{view.note}</p>
        {op?.status === "rejected" ? (
          <Button size="lg" variant="outline" onClick={() => openSyncSheet(true)}>
            See entries on this tablet
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
