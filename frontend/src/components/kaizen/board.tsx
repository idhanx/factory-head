"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Network, Trophy, Users, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import { Money } from "@/components/ui/money";
import { IdeaCard } from "./queue";
import type { KaizenBoardRow, KaizenDashboard, KaizenPattern } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * The management layer (Plugin Spec §12.2).
 *
 * Views are ordered the way the spec says to build them: *"Quick Wins is the
 * view that gets used in week 1. The rest is reporting, and reporting has no
 * value until there's volume."*
 */

const VIEWS = [
  { key: "quick_wins", label: "Quick wins", hint: "High value, low cost, no shutdown" },
  { key: "pending", label: "Waiting on a decision", hint: "Ranked by what they are worth" },
  { key: "delayed", label: "Delayed", hint: "Untouched for more than 14 days" },
  { key: "top_savings", label: "Top savings", hint: "Verified only — never estimates" },
  { key: "safety", label: "Safety", hint: "Safety never competes with ROI for attention" },
] as const;

type ViewKey = (typeof VIEWS)[number]["key"];

export function Board({
  data,
  loading,
  canDecide,
  onChanged,
}: {
  data: KaizenDashboard | undefined;
  loading: boolean;
  canDecide: boolean;
  onChanged: () => void;
}) {
  const t = useT();
  const [view, setView] = useState<ViewKey>("quick_wins");

  if (loading || !data) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-40" />
        ))}
      </div>
    );
  }

  const ideas = data[view];

  return (
    <div className="space-y-4">
      <Totals totals={data.totals} />

      {data.patterns.length > 0 ? (
        <div className="space-y-3">
          {data.patterns.map((pattern) => (
            <PatternAlert key={pattern.id} pattern={pattern} onChanged={onChanged} />
          ))}
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <section className="min-w-0">
          <div className="surface-2 mb-3 flex flex-wrap gap-0.5 rounded-xl border p-1">
            {VIEWS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setView(option.key)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-[0.8125rem] font-medium transition-colors",
                  view === option.key
                    ? "surface text-[var(--text)] shadow-[var(--shadow-1)]"
                    : "text-2 hover:text-[var(--text)]",
                )}
              >
                {t(option.label)}
                <span className="text-3 tnum ml-1.5 text-xs">{data[option.key].length}</span>
              </button>
            ))}
          </div>

          <p className="text-3 mb-3 text-xs">
            {t(VIEWS.find((option) => option.key === view)?.hint ?? "")}
          </p>

          {ideas.length === 0 ? (
            <Empty
              icon={<Zap className="size-6" />}
              title={t("Nothing here")}
              hint={t("This view fills as ideas come in and get decided.")}
            />
          ) : (
            <div className="space-y-3">
              {ideas.map((idea, index) => (
                <IdeaCard
                  key={idea.id}
                  idea={idea}
                  index={index}
                  canDecide={canDecide}
                  onChanged={onChanged}
                />
              ))}
            </div>
          )}
        </section>

        <div className="space-y-4">
          <Leaderboards boards={data.leaderboards} />
          <Participation rows={data.participation} />
        </div>
      </div>
    </div>
  );
}

function Totals({ totals }: { totals: KaizenDashboard["totals"] }) {
  const t = useT();
  return (
    <Card className="rise">
      <CardContent className="grid gap-4 pt-5 sm:grid-cols-3">
        <div>
          <p className="label mb-0.5">{t("Verified savings")}</p>
          <Money value={totals.verified_display} tone="recovered" size="lg" />
          <p className="text-3 text-xs">{t("measured by Core, not claimed")}</p>
        </div>
        <div>
          <p className="label mb-0.5">{t("Approved · hard")}</p>
          <Money value={totals.approved_hard_display} size="lg" />
          <p className="text-3 text-xs">
            {t("+ {0} capacity, reported separately", { 0: totals.approved_soft_display })}
          </p>
        </div>
        <div>
          <p className="label mb-0.5">{t("Submitted")}</p>
          <p className="figure text-2xl font-bold">{totals.total}</p>
          <p className="text-3 text-xs">
            {t("{0}% quick observations", { 0: totals.quick_share_percent })}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * §10 — one root cause behind many Kaizens and many Core events.
 *
 * *"You are currently approving these one by one."* A plant-wide project is a
 * single Core Action with everything linked to it, not seventeen separate ones.
 */
function PatternAlert({
  pattern,
  onChanged,
}: {
  pattern: KaizenPattern;
  onChanged: () => void;
}) {
  const t = useT();
  const create = useMutation({
    mutationFn: () => api.createKaizenProject(pattern.id, {}),
    onSuccess: onChanged,
  });
  const dismiss = useMutation({
    mutationFn: () => api.dismissKaizenPattern(pattern.id),
    onSuccess: onChanged,
  });

  return (
    <Card className="rise border-[var(--color-warn)]">
      <CardContent className="pt-5">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge variant="warn">
            <Network className="size-3" />
            {t("Pattern detected")}
          </Badge>
          {pattern.departments.map((department) => (
            <Badge key={department} variant="neutral">
              {department}
            </Badge>
          ))}
        </div>

        <h3 className="text-[1.0625rem] font-bold">{pattern.label}</h3>
        <p className="text-2 mt-1 text-sm leading-relaxed">{pattern.recommendation}</p>

        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span>
            <span className="tnum font-bold">{pattern.kaizen_count}</span>
            <span className="text-2"> {t("Kaizens")}</span>
          </span>
          <span>
            <span className="tnum font-bold">{pattern.event_count}</span>
            <span className="text-2"> {t("recorded Core events")}</span>
          </span>
          {pattern.lost_minutes > 0 ? (
            <span>
              <span className="tnum font-bold">
                {Math.round(pattern.lost_minutes / 60).toLocaleString("en-IN")}
              </span>
              <span className="text-2"> {t("hrs lost")}</span>
            </span>
          ) : null}
          <span>
            {/* Recorded, not estimated: only Core events contribute here. */}
            <Money value={pattern.verified_cost_display} tone="loss" size="sm" />
            <span className="text-2 text-xs"> {t("recorded")}</span>
          </span>
        </div>

        {create.error ? (
          <div className="mt-2">
            <ErrorNote message={(create.error as Error).message} />
          </div>
        ) : null}

        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" disabled={create.isPending} onClick={() => create.mutate()}>
            {create.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {t("Create one plant-wide project")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => dismiss.mutate()}>
            {t("Not a pattern")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Two rankings, always side by side.
 *
 * *"If a quality engineer competes with a machine operator on the same ranking,
 * the engineer wins every month... The operator sees a leaderboard they can
 * never top and stops submitting."*
 */
function Leaderboards({ boards }: { boards: Record<string, KaizenBoardRow[]> }) {
  const t = useT();
  const groups: Array<{ key: string; label: string }> = [
    { key: "shopfloor", label: "Shopfloor" },
    { key: "support", label: "Support & staff" },
  ];

  return (
    <Card className="rise" style={{ animationDelay: "80ms" }}>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Trophy className="size-3.5" />
          {t("Contributors")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {groups.map((group) => {
          const rows = boards[group.key] ?? [];
          return (
            <div key={group.key}>
              <p className="label mb-1.5">{group.label}</p>
              {rows.length === 0 ? (
                <p className="text-3 py-2 text-sm">{t("Nobody yet.")}</p>
              ) : (
                <ol className="divide-y">
                  {rows.slice(0, 5).map((row, index) => (
                    <li key={row.name} className="flex items-center gap-3 py-2">
                      <span
                        className={cn(
                          "tnum grid size-6 shrink-0 place-items-center rounded-full text-[0.6875rem] font-bold",
                          index === 0 ? "brand" : "surface-2 text-2",
                        )}
                      >
                        {index + 1}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold">
                          {row.name}
                        </span>
                        <span className="text-3 block text-xs">{row.level}</span>
                      </span>
                      <span className="tnum text-xs font-semibold">{t("{0} pts", { 0: row.points })}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

/**
 * Participation % per department — never raw count.
 *
 * *"Otherwise a 60-person machine shop always beats a 4-person stores team, and
 * stores stops trying."* It is a leading indicator and never the headline.
 */
function Participation({ rows }: { rows: KaizenDashboard["participation"] }) {
  const t = useT();
  if (rows.length === 0) return null;
  return (
    <Card className="rise" style={{ animationDelay: "120ms" }}>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Users className="size-3.5" />
          {t("Participation this month")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2.5">
          {rows.map((row) => (
            <li key={row.department}>
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium capitalize">{row.department}</span>
                <span className="tnum text-3 text-xs">
                  {t("{0} of {1}", { 0: row.submitted, 1: row.headcount })} ·{" "}
                  <span className="font-semibold text-[var(--text)]">{row.percent}%</span>
                </span>
              </div>
              <div className="surface-inset h-1.5 overflow-hidden rounded-full">
                <div
                  className="h-full rounded-full bg-[var(--color-brand)]"
                  style={{ width: `${Math.min(100, row.percent)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
        <p className="text-3 mt-3 text-xs leading-relaxed">
          {t("Participation is the leading indicator; verified savings is the lagging one. A plant high on one and low on the other has a compliance culture, not an improvement culture.")}
        </p>
      </CardContent>
    </Card>
  );
}
