"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight, GraduationCap, Plus, RefreshCw, UserX } from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { SUPS, can } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import type { SkillMatrix, TrainingNeed } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * Skill Matrix AI (§3 Layer 5).
 *
 *   "Tracks which operator is trained on which machine/operation, and connects
 *    that matrix directly to downtime data — 'No Operator' events point at
 *    exactly where a skill gap is costing money, and the plugin ranks training
 *    needs by the rupee loss they would remove."
 *
 * Two lists, never merged, because they are measured in different units:
 *
 *   * **Already costing money** — 'No Operator' stops, a measured ₹ per year.
 *   * **One absence away** — stations only one person can run. Nothing lost
 *     yet, so no annual figure is claimed; instead the cost of one idle shift,
 *     from the same kernel formula as every downtime event.
 */

const LEVELS = [
  { level: 0, label: "Not trained" },
  { level: 1, label: "In training" },
  { level: 2, label: "Competent" },
  { level: 3, label: "Can train others" },
] as const;

export default function Skills() {
  const t = useT();
  const session = loadSession();
  const canEdit = session ? can(session.roles, "owner", "supervisor", "consultant") : false;
  const canRefresh = session ? can(session.roles, ...SUPS) : false;
  const queryClient = useQueryClient();

  const matrix = useQuery({ queryKey: ["skills-matrix"], queryFn: api.skillMatrix });
  const needs = useQuery({ queryKey: ["training-needs"], queryFn: api.trainingNeeds });

  // Reading the needs no longer regenerates them; this is the deliberate refresh.
  const refresh = useMutation({
    mutationFn: api.refreshTrainingNeeds,
    onSuccess: (data: TrainingNeed[]) => queryClient.setQueryData(["training-needs"], data),
  });

  const gaps = (needs.data ?? []).filter((n) => n.kind === "gap");
  const coverage = (needs.data ?? []).filter((n) => n.kind === "coverage");
  const atRisk = matrix.data?.stations.filter((s) => s.at_risk).length ?? 0;
  const measured = gaps.reduce((sum, n) => sum + n.annual_loss_paise, 0);

  return (
    <div className="space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5 flex items-center gap-1.5">
            <GraduationCap className="size-3" />
            {t("Layer 5 · Skill Matrix AI")}
          </p>
          <h1 className="display text-[1.75rem] font-bold">{t("Skill matrix")}</h1>
          <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
            {t("Who can run what — connected to the downtime it causes when nobody can. Built from the operator list and stops the plant already records; no new register.")}
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-6">
          {canRefresh ? (
            <Button size="lg" variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              <RefreshCw className={cn("size-4", refresh.isPending && "animate-spin")} />
              {refresh.isPending ? t("Refreshing…") : t("Refresh training needs")}
            </Button>
          ) : null}
          <div>
            <p className="label mb-1">{t("Stations at risk")}</p>
            <span className="figure text-[1.75rem] font-bold leading-none">{atRisk}</span>
          </div>
          <div>
            <p className="label mb-1">{t("Lost to no operator, per year")}</p>
            <Money
              value={`₹${Math.round(measured / 100).toLocaleString("en-IN")}`}
              tone="loss"
              size="lg"
            />
          </div>
        </div>
      </header>

      {refresh.error ? (
        <ErrorNote message={t("Could not refresh training needs: {0}", { 0: refresh.error.message })} />
      ) : null}
      {refresh.isSuccess ? (
        <p role="status" className="text-3 text-xs">{t("Training needs recalculated from the latest stops and skills.")}</p>
      ) : null}
      {needs.error ? (
        <ErrorNote message={t("Could not load training needs: {0}", { 0: needs.error.message })} />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <NeedList
          title={t("Already costing money")}
          icon={<UserX className="size-3.5" />}
          hint={t("Stations that stopped for 'No Operator' at least twice in 90 days. Ranked by the measured loss.")}
          needs={gaps}
          loading={needs.isLoading}
          failed={Boolean(needs.error)}
          canEdit={canEdit}
          empty={t("No station has lost time to a missing operator more than once.")}
        />
        <NeedList
          title={t("One absence away")}
          icon={<AlertTriangle className="size-3.5" />}
          hint={t("Stations fewer than two people can run. Nothing lost yet — shown as the cost of one idle shift. Bottlenecks first: they stop the line.")}
          needs={coverage}
          loading={needs.isLoading}
          failed={Boolean(needs.error)}
          canEdit={canEdit}
          empty={t("Every station has at least two competent operators.")}
        />
      </div>

      <Card className="rise" style={{ animationDelay: "120ms" }}>
        <CardHeader className="flex-wrap justify-between gap-3">
          <CardTitle>{t("Operators × stations")}</CardTitle>
          <Legend />
        </CardHeader>
        <CardContent>
          {matrix.isLoading ? (
            <Skeleton className="h-96" />
          ) : matrix.error ? (
            <ErrorNote message={t("Could not load the skill matrix: {0}", { 0: matrix.error.message })} />
          ) : !matrix.data || matrix.data.operators.length === 0 ? (
            <Empty
              icon={<GraduationCap className="size-6" />}
              title={t("No operators yet")}
              hint={t("Operators come from template T5 during onboarding — names only, no logins.")}
            />
          ) : (
            <MatrixTable data={matrix.data} canEdit={canEdit} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function NeedList({
  title,
  icon,
  hint,
  needs,
  loading,
  failed,
  canEdit,
  empty,
}: {
  title: string;
  icon: React.ReactNode;
  hint: string;
  needs: TrainingNeed[];
  loading: boolean;
  failed: boolean;
  canEdit: boolean;
  empty: string;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const raise = useMutation({
    mutationFn: (id: string) => api.raiseTrainingAction(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["training-needs"] });
      void queryClient.invalidateQueries({ queryKey: ["actions"] });
    },
    // Feedback: `raise.error` renders under the list.
  });

  return (
    <Card className="rise">
      <CardHeader className="flex-col items-start gap-1">
        <CardTitle className="flex items-center gap-1.5">
          {icon}
          {title}
        </CardTitle>
        <p className="text-3 text-xs leading-relaxed normal-case tracking-normal">{hint}</p>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-40" />
        ) : failed ? (
          <p className="text-3 py-8 text-center text-sm">{t("Not loaded — see the error above.")}</p>
        ) : needs.length === 0 ? (
          <p className="text-3 py-8 text-center text-sm">{empty}</p>
        ) : (
          <ul className="divide-y">
            {needs.map((need) => (
              <li key={need.id} className="py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-bold">{need.station_code}</span>
                      {need.is_bottleneck ? <Badge variant="stopped">{t("Bottleneck")}</Badge> : null}
                      {need.kind === "gap" ? (
                        <span className="text-3 text-xs">
                          {t("{0} stops · {1} min", { 0: String(need.evidence.events ?? ""), 1: Math.round(Number(need.evidence.minutes ?? 0)) })}
                        </span>
                      ) : (
                        <span className="text-3 text-xs">
                          {t("{0} certified", { 0: String(need.evidence.certified_operators ?? 0) })}
                        </span>
                      )}
                    </div>
                    <p className="text-2 mt-1 text-xs leading-relaxed">{need.recommendation}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    {need.kind === "gap" ? (
                      <>
                        <Money value={need.annual_loss_display} tone="loss" size="md" />
                        <p className="text-3 text-[0.6875rem]">{t("per year, measured")}</p>
                      </>
                    ) : (
                      <>
                        <Money value={need.exposure_per_day_display} tone="neutral" size="md" />
                        <p className="text-3 text-[0.6875rem]">{t("per idle shift")}</p>
                      </>
                    )}
                  </div>
                </div>
                <div className="mt-2">
                  {need.has_action ? (
                    <Link
                      href="/actions"
                      className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
                    >
                      {t("Tracked as an Action")}<ArrowUpRight className="size-3" />
                    </Link>
                  ) : canEdit ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={raise.isPending}
                      onClick={() => raise.mutate(need.id)}
                    >
                      <Plus className="size-3.5" />
                      {t("Raise training action")}
                    </Button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
        {raise.error ? (
          <div className="mt-2">
            <ErrorNote message={(raise.error as Error).message} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function MatrixTable({ data, canEdit }: { data: SkillMatrix; canEdit: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const setSkill = useMutation({
    mutationFn: (payload: { operator_id: string; station_id: string; level: number }) =>
      api.setSkill(payload),
    // Optimistic: a supervisor clicking through a wall chart should not wait a
    // round-trip per cell. The server response is still the source of truth.
    onMutate: async (payload) => {
      await queryClient.cancelQueries({ queryKey: ["skills-matrix"] });
      const previous = queryClient.getQueryData<SkillMatrix>(["skills-matrix"]);
      queryClient.setQueryData<SkillMatrix>(["skills-matrix"], (current) =>
        current
          ? {
              ...current,
              operators: current.operators.map((op) =>
                op.id === payload.operator_id
                  ? { ...op, skills: { ...op.skills, [payload.station_id]: payload.level } }
                  : op,
              ),
            }
          : current,
      );
      return { previous };
    },
    onError: (e: Error, _payload, context) => {
      if (context?.previous) queryClient.setQueryData(["skills-matrix"], context.previous);
      setError(e.message);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills-matrix"] });
      void queryClient.invalidateQueries({ queryKey: ["training-needs"] });
    },
  });

  return (
    <div>
      {error ? (
        <div className="mb-3">
          <ErrorNote message={error} />
        </div>
      ) : null}
      {canEdit ? (
        <p className="text-3 mb-3 text-xs">{t("Click a cell to step through the levels.")}</p>
      ) : null}

      {/* A table wider than a phone scrolls inside its own container; the page
          body never scrolls sideways. */}
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-[3px] text-sm">
          <thead>
            <tr>
              <th className="label sticky left-0 z-10 bg-[var(--surface)] pr-3 text-left align-bottom">
                {t("Operator")}
              </th>
              {data.stations.map((station) => (
                <th key={station.id} className="px-0.5 pb-1 align-bottom">
                  <span
                    className={cn(
                      "block text-[0.6875rem] font-semibold whitespace-nowrap [writing-mode:vertical-rl] rotate-180",
                      station.at_risk ? "text-[var(--color-loss)]" : "text-2",
                    )}
                  >
                    {station.code}
                    {station.is_bottleneck ? " ★" : ""}
                  </span>
                </th>
              ))}
              <th className="label pl-2 text-right align-bottom">{t("Certified")}</th>
            </tr>
          </thead>
          <tbody>
            {data.operators.map((operator) => (
              <tr key={operator.id}>
                <th
                  scope="row"
                  className="sticky left-0 z-10 bg-[var(--surface)] pr-3 text-left font-medium whitespace-nowrap"
                >
                  {operator.name}
                </th>
                {data.stations.map((station) => {
                  const level = operator.skills[station.id] ?? 0;
                  const label = t(LEVELS[level]?.label ?? "Not trained");
                  return (
                    <td key={station.id} className="p-0">
                      <button
                        type="button"
                        disabled={!canEdit || setSkill.isPending}
                        title={t("{0} on {1}: {2}", { 0: operator.name, 1: station.code, 2: label })}
                        aria-label={t("{0} on {1}: {2}", { 0: operator.name, 1: station.code, 2: label })}
                        onClick={() =>
                          setSkill.mutate({
                            operator_id: operator.id,
                            station_id: station.id,
                            level: (level + 1) % 4,
                          })
                        }
                        className={cn(
                          "tnum grid size-9 place-items-center rounded-lg text-xs font-bold transition-transform",
                          canEdit && "hover:scale-110 active:scale-95",
                          level === 0 && "text-3 border border-dashed",
                        )}
                        style={
                          level > 0
                            ? { background: `var(--skill-${level})`, color: `var(--skill-${level}-text)` }
                            : undefined
                        }
                      >
                        {level === 0 ? "·" : level}
                      </button>
                    </td>
                  );
                })}
                <td className="tnum text-2 pl-2 text-right text-xs font-semibold">
                  {operator.certified_count}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th className="label sticky left-0 z-10 bg-[var(--surface)] pt-2 pr-3 text-left">
                {t("Competent")}
              </th>
              {data.stations.map((station) => (
                <td key={station.id} className="pt-2 text-center">
                  <span
                    className={cn(
                      "tnum text-xs font-bold",
                      station.at_risk ? "text-[var(--color-loss)]" : "text-2",
                    )}
                    title={
                      station.at_risk
                        ? t("Fewer than {0} people can run {1}", { 0: data.min_coverage, 1: station.code })
                        : undefined
                    }
                  >
                    {station.coverage}
                    {station.at_risk ? "!" : ""}
                  </span>
                </td>
              ))}
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="text-3 mt-3 text-xs">
        {t("★ bottleneck · a red count with ! means fewer than {0} people can run the station alone.", { 0: data.min_coverage })}
      </p>
    </div>
  );
}

function Legend() {
  const t = useT();
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      {LEVELS.map(({ level, label }) => (
        <span key={level} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className={cn(
              "tnum grid size-5 place-items-center rounded-md text-[0.625rem] font-bold",
              level === 0 && "text-3 border border-dashed",
            )}
            style={
              level > 0
                ? { background: `var(--skill-${level})`, color: `var(--skill-${level}-text)` }
                : undefined
            }
          >
            {level === 0 ? "·" : level}
          </span>
          <span className="text-2 text-xs">{t(label)}</span>
        </span>
      ))}
    </div>
  );
}
