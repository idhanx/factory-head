"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ClipboardCheck } from "lucide-react";
import { api } from "@/lib/api";
import { enqueue } from "@/lib/offline/outbox";
import { openSyncSheet, useSync } from "@/lib/offline/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import { Delta, Grid, Page, PageHeader, Panel, Section, Stat } from "@/components/ui/page";
import { RankedBars, TrendLine } from "@/components/charts";
import type { QualityKpis, QualityPayload } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * Screen 5 — Quality.
 *
 * Two jobs on one screen, and they belong together.
 *
 * The first is §7's: the inspector records a batch in about 60 seconds, under
 * their own login, independently of the supervisor's count so the two
 * reconcile. That form stays first on a tablet and never moves more than a
 * thumb's reach.
 *
 * The second is §8's quality page — *"PPM, first-pass yield, top 3 defects with
 * trend, rejects by machine"*, chosen because they point the inspector at the
 * machine causing tomorrow's customer complaint. Every one of those numbers was
 * already being computed and none of it was on screen: an inspector could type
 * into this page for a month without ever learning which part was failing.
 */

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 28, label: "28 days" },
  { days: 90, label: "90 days" },
] as const;

export default function Quality() {
  const t = useT();
  const [days, setDays] = useState<number>(28);
  const kpis = useQuery({
    queryKey: ["kpis", "quality", days],
    queryFn: () => api.roleKpis("quality", days),
  });

  return (
    <Page width="wide">
      <PageHeader
        eyebrow={t("Screen 5 · Inspector")}
        icon={<ClipboardCheck className="size-3" />}
        title={t("Quality")}
        description={t("Record a batch at completion, and see what this plant is actually rejecting — which defect, on which machine, and what it costs.")}
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

      <Headline kpis={kpis.data} loading={kpis.isLoading} days={days} />

      {/* The form leads on a tablet and moves to the rail on a desktop, so the
          inspector's 60 seconds are never behind a scroll. */}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,25rem)]">
        <div className="order-2 min-w-0 space-y-6 xl:order-1">
          <Trend kpis={kpis.data} loading={kpis.isLoading} days={days} />
          <div className="grid items-start gap-6 lg:grid-cols-2">
            <Defects kpis={kpis.data} loading={kpis.isLoading} />
            <Machines kpis={kpis.data} loading={kpis.isLoading} />
          </div>
        </div>
        <div className="order-1 min-w-0 xl:order-2">
          <EntryCard />
        </div>
      </div>
    </Page>
  );
}

/* ---------------------------------------------------------------------- */
/* The four numbers §8 gives this role                                      */
/* ---------------------------------------------------------------------- */

function Headline({
  kpis,
  loading,
  days,
}: {
  kpis: QualityKpis | undefined;
  loading: boolean;
  days: number;
}) {
  const t = useT();
  if (loading) {
    return (
      <Grid cols={4}>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-34" />
        ))}
      </Grid>
    );
  }
  if (!kpis) return null;

  const yieldPct = kpis.first_pass_yield * 100;

  return (
    <Grid cols={4}>
      <Stat
        label={t("Parts per million")}
        value={Math.round(kpis.ppm).toLocaleString("en-IN")}
        tone="feature"
        // Falling PPM is the win, so the polarity is inverted here.
        delta={<Delta value={kpis.ppm} previous={kpis.previous_ppm} good="down" />}
        hint={t("Rejected per million inspected, last {0} days", { 0: days })}
        delay={0}
      />
      <Stat
        label={t("First-pass yield")}
        value={yieldPct.toFixed(1)}
        unit="%"
        hint={t("Right first time — before any rework")}
        delay={60}
      />
      <Stat
        label={t("Inspected")}
        value={kpis.inspected.toLocaleString("en-IN")}
        hint={t("{0} rejected", { 0: kpis.rejected.toLocaleString("en-IN") })}
        delay={120}
      />
      <Stat
        label={t("Scrap cost")}
        money
        display={kpis.scrap_cost_display}
        moneyTone="loss"
        hint={t("Material, plus the processing already spent on it")}
        delay={180}
      />
    </Grid>
  );
}

function Trend({
  kpis,
  loading,
  days,
}: {
  kpis: QualityKpis | undefined;
  loading: boolean;
  days: number;
}) {
  const t = useT();
  return (
    <Section
      title={t("PPM over time")}
      description={t("Rejections per million parts inspected, by day. A rising line is tomorrow's customer complaint.")}
      delay={220}
    >
      <Panel>
        {loading ? (
          <Skeleton className="h-52" />
        ) : (
          <TrendLine
            data={(kpis?.trend ?? []) as unknown as Array<Record<string, number | string>>}
            dataKey="ppm"
            label="PPM"
            tone="loss"
            height={220}
            format={(n) => Math.round(n).toLocaleString("en-IN")}
          />
        )}
        <p className="text-3 mt-3 text-xs">
          {t("Last {0} days. Each point is every batch that day, across all parts.", { 0: days })}
        </p>
      </Panel>
    </Section>
  );
}

function Defects({ kpis, loading }: { kpis: QualityKpis | undefined; loading: boolean }) {
  const t = useT();
  const items = (kpis?.top_defects ?? []).map((defect) => ({
    label: defect.label,
    value: defect.count,
    display: defect.count.toLocaleString("en-IN"),
    meta: <Delta value={defect.ppm} previous={defect.previous_ppm} good="down" />,
  }));

  return (
    <Section
      title={t("What is failing")}
      description={t("Counted by defect code, worst first, with the change against the period before.")}
      delay={280}
    >
      <Panel>
        {loading ? (
          <Skeleton className="h-44" />
        ) : items.length === 0 ? (
          <Empty
            title={t("No rejections recorded")}
            hint={t("Defect codes appear here as batches are recorded against them.")}
          />
        ) : (
          // One hue: this compares magnitudes of a single measure across codes,
          // which is a sequential job. Giving each code its own colour would
          // imply the colours carry meaning they do not.
          <RankedBars items={items} tone="loss" />
        )}
      </Panel>
    </Section>
  );
}

function Machines({ kpis, loading }: { kpis: QualityKpis | undefined; loading: boolean }) {
  const t = useT();
  const items = (kpis?.by_machine ?? [])
    .filter((row) => row.rejected > 0)
    .map((row) => ({
      label: row.station,
      sublabel: `${Math.round(row.ppm).toLocaleString("en-IN")} PPM`,
      value: row.scrap_cost_paise,
      display: row.scrap_cost_display,
    }));

  return (
    <Section
      title={t("Where it is failing")}
      description={t("Scrap cost by machine — the one that ranks first is where a fix pays back most.")}
      delay={340}
    >
      <Panel>
        {loading ? (
          <Skeleton className="h-44" />
        ) : items.length === 0 ? (
          <Empty
            title={t("Nothing rejected on any machine")}
            hint={t("Batches recorded against a station are ranked here by what the scrap cost.")}
          />
        ) : (
          <RankedBars items={items} tone="loss" />
        )}
      </Panel>
    </Section>
  );
}

/* ---------------------------------------------------------------------- */
/* The 60-second entry — §7 Screen 5                                        */
/* ---------------------------------------------------------------------- */

/**
 * Saved to the tablet first and sent from there, so an inspection bay with no
 * Wi-Fi loses nothing. The cost and the reconciliation are the server's to work
 * out; until the entry has reached it, the card says so rather than showing a
 * figure it made up.
 */
function EntryCard() {
  const t = useT();
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

  const rejectTotal = Number(reject || 0);
  const reworkTotal = Number(rework || 0);
  const goodTotal = Number(good || 0);
  const coded = rejectTotal + reworkTotal;
  const assigned = Object.values(defects).reduce((sum, n) => sum + n, 0);
  // The server refuses both of these (422); catching them here saves the
  // inspector a round trip and says which number to fix.
  const overAssigned = assigned > coded;
  const reworkTooHigh = reworkTotal > goodTotal;

  const submit = async () => {
    const payload: QualityPayload = {
      part_id: partId,
      station_id: stationId || null,
      good_count: goodTotal,
      reject_count: rejectTotal,
      rework_count: reworkTotal,
      rework_minutes: reworkTotal > 0 && reworkMinutes ? Number(reworkMinutes) : null,
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
        payload: Object.fromEntries(
          Object.entries(payload).filter(([, value]) => value !== null),
        ),
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
      setError(t("Could not save this batch on the tablet: {0}", { 0: (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4 xl:sticky xl:top-24">
      <Card className="rise">
        <CardHeader>
          <CardTitle>{t("Record a batch")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-3 mb-4 text-xs leading-relaxed">
            {t("Entered under your own login, independently of the supervisor's production count, so the two reconcile.")}
          </p>

          {loadError ? (
            <div className="mb-4">
              <ErrorNote message={t("Could not load the part and code lists: {0}", { 0: loadError.message })} />
            </div>
          ) : null}

          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (overAssigned || reworkTooHigh || saving) return;
              void submit();
            }}
          >
            <div>
              <Label htmlFor="part">{t("Part number")}</Label>
              <Select
                id="part"
                required
                value={partId}
                onChange={(e) => setPartId(e.target.value)}
              >
                <option value="">{t("Select…")}</option>
                {(parts.data ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.part_number} — {p.description}
                  </option>
                ))}
              </Select>
            </div>

            <div>
              <Label htmlFor="station">{t("Station")}</Label>
              <Select
                id="station"
                value={stationId}
                onChange={(e) => setStationId(e.target.value)}
              >
                <option value="">{t("Not specified")}</option>
                {(stations.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.code}
                  </option>
                ))}
              </Select>
            </div>

            {/* Big, centred and numeric: thick fingers, poor light, a hurry. */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="good">{t("Good")}</Label>
                <Input
                  id="good"
                  required
                  inputMode="numeric"
                  value={good}
                  onChange={(e) => setGood(e.target.value.replace(/\D/g, ""))}
                  className="money h-18 text-center text-3xl font-bold"
                />
              </div>
              <div>
                <Label htmlFor="reject">{t("Rejected")}</Label>
                <Input
                  id="reject"
                  inputMode="numeric"
                  value={reject}
                  onChange={(e) => setReject(e.target.value.replace(/\D/g, ""))}
                  className="money h-18 text-center text-3xl font-bold"
                />
              </div>
            </div>

            <div>
              <Label htmlFor="rework">{t("Of the good parts, how many were reworked?")}</Label>
              <Input
                id="rework"
                inputMode="numeric"
                value={rework}
                placeholder="0"
                onChange={(e) => setRework(e.target.value.replace(/\D/g, ""))}
                aria-invalid={reworkTooHigh}
                className="money font-semibold"
              />
              <p className="text-3 mt-1 text-xs">
                {t("Good in the end, but needed a second pass. Leave 0 if none.")}
              </p>
            </div>

            {rejectTotal > 0 ? (
              <div>
                <Label htmlFor="disposition">{t("What happens to the rejected parts?")}</Label>
                <Select
                  id="disposition"
                  value={disposition}
                  onChange={(e) =>
                    setDisposition(e.target.value as QualityPayload["disposition"])
                  }
                >
                  <option value="scrap">{t("Scrap")}</option>
                  <option value="use_as_is">{t("Use as is")}</option>
                </Select>
              </div>
            ) : null}

            {reworkTotal > 0 ? (
              <div>
                <Label htmlFor="rework-minutes">{t("Minutes spent on rework (optional)")}</Label>
                <Input
                  id="rework-minutes"
                  inputMode="numeric"
                  value={reworkMinutes}
                  onChange={(e) => setReworkMinutes(e.target.value.replace(/\D/g, ""))}
                  placeholder="45"
                />
              </div>
            ) : null}

            {/* Kept on screen while any code is counted, so a count that is now
                too high can still be corrected rather than hiding. */}
            {coded > 0 || assigned > 0 ? (
              <div>
                <Label>{t("Defect codes")}</Label>
                <p className="text-3 mb-2 text-xs">
                  {t("{0} of {1} assigned{2}", { 0: assigned, 1: coded, 2: assigned < coded ? t("— the rest stay uncoded") : "" })}
                </p>
                <div className="space-y-2">
                  {(defectCodes.data ?? []).map((defect) => (
                    <div
                      key={defect.id}
                      className="flex items-center gap-2 rounded-lg border px-2.5 py-1.5"
                    >
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
                        className="money h-10 w-14 text-center"
                        aria-label={t("{0} count", { 0: defect.label })}
                      />
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {reworkTooHigh ? (
              <ErrorNote
                message={t("{0} reworked is more than the {1} good parts. Reworked parts are counted inside the good count — lower the reworked number, or raise the good count.", { 0: reworkTotal, 1: goodTotal })}
              />
            ) : null}
            {overAssigned ? (
              <ErrorNote
                message={t("{0} parts have a defect code, but only {1} were rejected or reworked. Lower the defect counts, or raise the rejected or reworked count.", { 0: assigned, 1: coded })}
              />
            ) : null}
            {error ? <ErrorNote message={error} /> : null}

            <Button
              type="submit"
              size="touch"
              className="w-full"
              disabled={saving || !partId || overAssigned || reworkTooHigh}
            >
              {saving ? t("Saving…") : t("Record batch")}
            </Button>
          </form>
        </CardContent>
      </Card>

      {last ? <BatchStatus clientRef={last.ref} summary={last.summary} /> : null}
    </div>
  );
}

/**
 * Where the last batch stands. The cost and reconciliation are computed by the
 * server when the entry lands; this card never invents them on the tablet.
 */
function BatchStatus({ clientRef, summary }: { clientRef: string; summary: string }) {
  const t = useT();
  const op = useSync((st) => st.ops.find((o) => o.client_ref === clientRef));
  const result = useSync((st) => st.results[clientRef]);
  const online = useSync((st) => st.online);

  const view = op
    ? op.status === "rejected"
      ? {
          title: "Not accepted",
          badge: "stopped" as const,
          label: "Refused",
          note: op.message ?? "The server refused this batch.",
        }
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
          note:
            result.message ??
            "Its shift was already locked when it arrived; the owner decides whether it is added.",
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
        <CardTitle>{t(view.title)}</CardTitle>
        <Badge variant={view.badge}>{t(view.label)}</Badge>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm">{summary}</p>
        <p className={view.badge === "stopped" ? "text-sm text-[var(--color-loss)]" : "text-2 text-xs"}>
          {view.note}
        </p>
        {op?.status === "rejected" ? (
          <Button size="lg" variant="outline" onClick={() => openSyncSheet(true)}>
            {t("See entries on this tablet")}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}
