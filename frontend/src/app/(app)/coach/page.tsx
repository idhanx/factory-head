"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, RefreshCw, Sparkles, Sunrise, UserRound } from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { SUPS, can } from "@/lib/roles";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import { useT } from "@/lib/i18n";
import { activeLocale } from "@/lib/format";

/**
 * The AI Shift Coach (§3 Layer 2, §14.6).
 *
 * Exactly three priorities, each naming a machine, citing the plant's own
 * history, and giving a concrete action, a person, a deadline and a rupee
 * value. Three is a product decision, not a display limit — a supervisor given
 * seven priorities has none.
 *
 * The tier is shown on purpose. A Coach that has seen ten shifts must not sound
 * like one that has seen a year, and §14.6 makes that a product behaviour
 * rather than a disclaimer: "Saying nothing beats guessing."
 */
const TIER_TONE: Record<number, "neutral" | "accent" | "recovered"> = {
  0: "neutral",
  1: "accent",
  2: "recovered",
  3: "recovered",
};

export default function Coach() {
  const t = useT();
  const queryClient = useQueryClient();
  const session = loadSession();
  const canGenerate = session ? can(session.roles, ...SUPS) : false;
  const briefing = useQuery({ queryKey: ["coach"], queryFn: api.coachToday });
  const regenerate = useMutation({
    mutationFn: api.generateCoach,
    onSuccess: (data) => queryClient.setQueryData(["coach"], data),
  });

  if (briefing.isLoading) return <Skeleton className="h-96" />;
  if (briefing.error) {
    return (
      <div className="mx-auto max-w-4xl space-y-4">
        <h1 className="display text-[1.75rem] font-bold">{t("Shift Coach")}</h1>
        <ErrorNote message={t("Could not load today's briefing: {0}", { 0: briefing.error.message })} />
        <Button size="lg" variant="outline" onClick={() => void briefing.refetch()}>
          {t("Try again")}
        </Button>
      </div>
    );
  }
  // The server answers 404 until the 5:45 AM run has happened; the client
  // turns that into null. Reading no longer generates one as a side effect.
  if (!briefing.data) {
    return (
      <div className="mx-auto max-w-4xl space-y-4">
        <h1 className="display text-[1.75rem] font-bold">{t("Shift Coach")}</h1>
        <Empty
          icon={<Sunrise className="size-6" />}
          title={t("No briefing yet")}
          hint={
            canGenerate
              ? t("Today's briefing is written at 5:45 AM. If it has not arrived, generate it now from the plant's closed shifts.")
              : t("Today's briefing is written at 5:45 AM. Check back after the morning run.")
          }
          action={
            canGenerate ? (
              <Button
                size="lg"
                className="mt-2"
                onClick={() => regenerate.mutate()}
                disabled={regenerate.isPending}
              >
                <Sparkles className={regenerate.isPending ? "size-4 animate-pulse" : "size-4"} />
                {regenerate.isPending ? t("Generating…") : t("Generate now")}
              </Button>
            ) : undefined
          }
        />
        {regenerate.error ? (
          <ErrorNote message={t("Could not generate the briefing: {0}", { 0: regenerate.error.message })} />
        ) : null}
      </div>
    );
  }
  const d = briefing.data;

  const total = d.priorities.reduce((sum, p) => sum + p.value_paise, 0);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5 flex items-center gap-1.5">
            <Sunrise className="size-3" />
            {t("5:45 AM briefing")}
          </p>
          <h1 className="display text-[1.75rem] font-bold">{t("Shift Coach")}</h1>
          <p className="text-2 mt-1 text-sm">
            {new Date(d.business_date).toLocaleDateString(activeLocale(), {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={TIER_TONE[d.tier] ?? "neutral"} className="px-2.5 py-1">
            {t("Tier {0}", { 0: d.tier })}
          </Badge>
          {canGenerate ? (
            <Button
              size="lg"
              variant="outline"
              onClick={() => regenerate.mutate()}
              disabled={regenerate.isPending}
            >
              <RefreshCw className={regenerate.isPending ? "size-3.5 animate-spin" : "size-3.5"} />
              {regenerate.isPending ? t("Regenerating…") : t("Regenerate")}
            </Button>
          ) : null}
        </div>
      </header>

      {regenerate.error ? (
        <ErrorNote message={t("Could not regenerate the briefing: {0}", { 0: regenerate.error.message })} />
      ) : null}

      {/* ---- what it is entitled to say, and why -------------------------- */}
      <div className="rise surface-2 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border px-4 py-3">
        <p className="text-sm font-medium">{d.tier_label}</p>
        {d.data_basis ? (
          <p className="text-3 text-xs">
            {t("{0} closed shifts over {1} days", { 0: String(d.data_basis.shifts_of_history), 1: String(d.data_basis.days_live) })}
          </p>
        ) : null}
        {total > 0 ? (
          <span className="ml-auto flex items-baseline gap-1.5">
            <span className="label">{t("Total prevention value")}</span>
            <Money
              value={`₹${(total / 100).toLocaleString("en-IN")}`}
              tone="recovered"
              size="sm"
            />
          </span>
        ) : null}
      </div>

      {d.priorities.length === 0 ? (
        <Empty
          icon={<Sparkles className="size-6" />}
          title={t("Nothing specific to flag")}
          hint={t("The Coach never gives generic advice. When it has nothing useful to say, it says less — that is deliberate.")}
        />
      ) : (
        <div className="space-y-3">
          {d.priorities.map((priority, index) => (
            <Card
              key={priority.rank}
              elevation={2}
              className="rise relative overflow-hidden"
              style={{ animationDelay: `${index * 90}ms` }}
            >
              <span
                aria-hidden
                className="absolute inset-y-0 left-0 w-[3px] bg-[var(--color-brand)]"
              />
              <CardContent className="pt-5 pl-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="brand grid size-6 place-items-center rounded-lg text-[0.6875rem] font-bold">
                        {priority.rank}
                      </span>
                      <span className="display text-sm font-bold">{priority.station}</span>
                    </div>
                    <h2 className="display text-[1.25rem] font-bold leading-tight">
                      {priority.headline}
                    </h2>
                  </div>
                  <div className="text-right">
                    <p className="label mb-0.5">{t("Prevention value")}</p>
                    <Money value={priority.value_display} tone="recovered" size="lg" />
                  </div>
                </div>

                {/* The evidence, always from the plant's own history. */}
                <p className="text-2 mt-3.5 text-sm leading-relaxed">{priority.evidence}</p>

                <div className="surface-2 mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border px-4 py-3">
                  <p className="min-w-0 flex-1 text-sm font-semibold">
                    <ArrowRight className="mr-1.5 inline size-3.5 text-[var(--color-accent)]" />
                    {priority.action}
                  </p>
                  <span className="text-3 flex items-center gap-1.5 text-xs">
                    <UserRound className="size-3" />
                    {priority.assignee}
                  </span>
                  <Badge variant="accent">{priority.deadline}</Badge>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {d.rendered_text ? (
        <Card className="rise" style={{ animationDelay: "320ms" }}>
          <CardContent className="pt-5">
            <p className="label mb-2.5">{t("As sent to the phone")}</p>
            <pre className="surface-2 whitespace-pre-wrap rounded-lg border p-4 text-sm leading-relaxed">
              {d.rendered_text}
            </pre>
            <p className="text-3 mt-3 text-xs leading-relaxed">
              {t("Generated by {0}. Every number comes from the calculation kernel; the model never computes one. An LLM that hallucinates a rupee figure into an owner's WhatsApp would destroy the credibility of every number in the product.", { 0: d.generated_by === "llm" ? "a language model — phrasing only" : "rules" })}
            </p>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
