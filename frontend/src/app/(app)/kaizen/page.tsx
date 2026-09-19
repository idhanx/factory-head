"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Lightbulb } from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { can } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, Skeleton } from "@/components/ui/misc";
import { Money } from "@/components/ui/money";
import { Board } from "@/components/kaizen/board";
import { IdeaCard } from "@/components/kaizen/queue";
import { SubmitFlow } from "@/components/kaizen/submit-flow";
import type { KaizenStanding } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * Kaizen AI — Layer 5, Plugin Edition.
 *
 *   "An operator talks to their phone for 30 seconds and takes one photo. The
 *    system already knows which machine they are standing at, which part is
 *    running and what that machine costs per hour — so it asks four questions
 *    instead of ten. Then it does the thing only a plugin can do: it looks up
 *    what the plant has already recorded about this exact problem."
 *
 * Three screens, because there are three jobs: an operator submitting, an
 * approver deciding, and the Lean engineer who actually logs in daily. The
 * operator's screen opens first and is the only one most people ever see.
 */

const TABS = [
  { key: "submit", label: "Report something" },
  { key: "mine", label: "My Kaizens" },
  { key: "board", label: "The board" },
] as const;

type Tab = (typeof TABS)[number]["key"];

export default function Kaizen() {
  const t = useT();
  const queryClient = useQueryClient();
  const session = loadSession();
  const canDecide = session ? can(session.roles, "owner", "supervisor", "consultant") : false;

  const [tab, setTab] = useState<Tab>("submit");

  const standing = useQuery({ queryKey: ["kaizen-me"], queryFn: api.myKaizenStanding });
  const mine = useQuery({
    queryKey: ["kaizen-mine"],
    queryFn: api.myKaizens,
    enabled: tab === "mine",
  });
  const dashboard = useQuery({
    queryKey: ["kaizen-dashboard"],
    queryFn: api.kaizenDashboard,
    enabled: tab === "board",
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["kaizen-me"] });
    void queryClient.invalidateQueries({ queryKey: ["kaizen-mine"] });
    void queryClient.invalidateQueries({ queryKey: ["kaizen-dashboard"] });
  };

  return (
    <div className="space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5 flex items-center gap-1.5">
            <Lightbulb className="size-3" />
            {t("Layer 5 · Kaizen AI")}
          </p>
          <h1 className="display text-[1.75rem] font-bold">{t("Improvement ideas")}</h1>
          <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
            {t("Say what is wrong and take one photo. The system knows your machine, looks up what this problem has already cost the plant, prices the fix against the same numbers everything else uses, and answers you straight away.")}
          </p>
        </div>
      </header>

      <div className="surface-2 flex w-fit gap-0.5 rounded-xl border p-1">
        {TABS.map((option) => (
          <button
            key={option.key}
            type="button"
            onClick={() => setTab(option.key)}
            className={cn(
              "rounded-lg px-3.5 py-1.5 text-[0.8125rem] font-medium transition-colors",
              tab === option.key
                ? "surface text-[var(--text)] shadow-[var(--shadow-1)]"
                : "text-2 hover:text-[var(--text)]",
            )}
          >
            {t(option.label)}
          </button>
        ))}
      </div>

      {tab === "submit" ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,30rem)_minmax(0,1fr)]">
          <SubmitFlow defaultName={session?.full_name ?? ""} onSubmitted={refresh} />
          <div className="space-y-4">
            <Standing standing={standing.data} loading={standing.isLoading} />
          </div>
        </div>
      ) : null}

      {tab === "mine" ? (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
          <section className="min-w-0">
            {mine.isLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-44" />
                ))}
              </div>
            ) : (mine.data ?? []).length === 0 ? (
              <Empty
                icon={<Lightbulb className="size-6" />}
                title={t("Nothing yet")}
                hint={t("Anything you report shows up here, and you get a message every time it moves.")}
              />
            ) : (
              <div className="space-y-3">
                {(mine.data ?? []).map((idea, index) => (
                  <IdeaCard
                    key={idea.id}
                    idea={idea}
                    index={index}
                    canDecide={false}
                    onChanged={refresh}
                  />
                ))}
              </div>
            )}
          </section>
          <Standing standing={standing.data} loading={standing.isLoading} />
        </div>
      ) : null}

      {tab === "board" ? (
        <Board
          data={dashboard.data}
          loading={dashboard.isLoading}
          canDecide={canDecide}
          onChanged={refresh}
        />
      ) : null}
    </div>
  );
}

/**
 * The operator's own standing: level, points, and this month's counter.
 *
 * The month counter is a floor, not a leaderboard — one 30-second observation
 * clears it. The two are deliberately separate systems: quantity clears the
 * floor, only verified outcomes earn points.
 */
function Standing({
  standing,
  loading,
}: {
  standing: KaizenStanding | undefined;
  loading: boolean;
}) {
  const t = useT();
  if (loading) return <Skeleton className="h-52" />;
  if (!standing) return null;

  const span = (standing.level_ceiling ?? standing.points) - standing.level_floor;
  const within = standing.points - standing.level_floor;
  const progress = span > 0 ? Math.min(100, Math.round((within / span) * 100)) : 100;
  const done = standing.this_month >= standing.monthly_minimum;

  return (
    <Card className="rise" style={{ animationDelay: "80ms" }}>
      <CardContent className="pt-5">
        <p className="label mb-1">{t("Your level")}</p>
        <p className="text-[1.375rem] leading-tight font-bold">{standing.level_label}</p>
        <p className="text-3 tnum text-xs">{t("{0} points", { 0: standing.points })}</p>

        {standing.next_level_label ? (
          <div className="mt-3">
            <div className="surface-inset h-2 overflow-hidden rounded-full">
              <div
                className="h-full rounded-full bg-[var(--color-brand)]"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="text-3 mt-1.5 text-xs">
              {t("{0} more to {1}", { 0: standing.points_to_next, 1: standing.next_level_label })}
            </p>
          </div>
        ) : null}

        <div className="mt-4 border-t pt-3">
          <p className="label mb-1">{t("This month")}</p>
          <p className={cn("text-sm font-semibold", done && "text-[var(--color-recovered)]")}>
            {t("{0} of {1}{2}", { 0: standing.this_month, 1: standing.monthly_minimum, 2: done ? " ✓" : "" })}
          </p>
          {!done ? (
            <p className="text-3 mt-0.5 text-xs">
              {t("{0} day{1} left. One 30-second observation clears it.", { 0: standing.days_left_in_month, 1: standing.days_left_in_month === 1 ? "" : "s" })}
            </p>
          ) : null}
        </div>

        {standing.verified_saving_paise > 0 ? (
          <div className="mt-4 border-t pt-3">
            <p className="label mb-0.5">{t("Verified savings from your ideas")}</p>
            <Money value={standing.verified_saving_display} tone="recovered" size="lg" />
            <p className="text-3 mt-0.5 text-xs">
              {t("Measured after the fix held — not what was promised.")}
            </p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
