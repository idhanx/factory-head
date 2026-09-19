"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { ArrowUpRight, Check, Loader2, ShieldAlert, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { Money } from "@/components/ui/money";
import { ForecastPanel, HistoryPanel, ImpactCard, VisionFindings, Verdict } from "./pieces";
import type { KaizenIdea } from "@/types/api";
import { useT } from "@/lib/i18n";
import { activeLocale } from "@/lib/format";

/**
 * The approver's view of one Kaizen (Plugin Spec §6.3).
 *
 * *"Managers do not approve on ROI alone. A ₹4,000 saving that removes an audit
 * non-conformity gets approved instantly; a ₹90,000 saving that needs a two-day
 * shutdown sits for six months."* So the decision screen carries the
 * Core-verified history, the two-column money, the stars, the forecast and the
 * ticking cost of not deciding — not a single score.
 */

const STATUS_BADGE: Record<
  KaizenIdea["status"],
  { label: string; variant: "neutral" | "brand" | "recovered" | "warn" | "accent" }
> = {
  draft: { label: "Draft", variant: "neutral" },
  submitted: { label: "Submitted", variant: "neutral" },
  scored: { label: "Waiting on you", variant: "brand" },
  approved: { label: "Approved", variant: "recovered" },
  accepted: { label: "In progress", variant: "accent" },
  rejected: { label: "Declined", variant: "neutral" },
  implemented: { label: "Done", variant: "recovered" },
  verified: { label: "Verified", variant: "recovered" },
  duplicate: { label: "Already covered", variant: "neutral" },
};

/** Submitted → Approved → In progress → Done → Verified. The operator sees this
 *  and nothing else: no Actions Board, no RCA template, no PDCA grid. */
const JOURNEY: Array<{ key: string; label: string }> = [
  { key: "submitted", label: "Submitted" },
  { key: "approved", label: "Approved" },
  { key: "accepted", label: "In progress" },
  { key: "implemented", label: "Done" },
  { key: "verified", label: "Verified" },
];

export function Progress({ status }: { status: KaizenIdea["status"] }) {
  const t = useT();
  const order = ["submitted", "scored", "approved", "accepted", "implemented", "verified"];
  const reached = order.indexOf(status);
  if (status === "rejected" || status === "duplicate" || reached < 0) return null;

  return (
    <ol className="flex flex-wrap items-center gap-1.5" aria-label={t("Progress")}>
      {JOURNEY.map((stage) => {
        const done = order.indexOf(stage.key) <= reached;
        return (
          <li key={stage.key} className="flex items-center gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-full",
                done ? "bg-[var(--color-recovered)]" : "surface-3",
              )}
            />
            <span className={cn("text-[0.6875rem]", done ? "text-2" : "text-3")}>
              {stage.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function IdeaCard({
  idea,
  index = 0,
  canDecide,
  onChanged,
  defaultOpen = false,
}: {
  idea: KaizenIdea;
  index?: number;
  canDecide: boolean;
  onChanged: () => void;
  defaultOpen?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(defaultOpen);
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const [realCost, setRealCost] = useState("");
  const [rate, setRate] = useState("");

  const waiting = idea.status === "scored" || idea.status === "submitted";
  const badge = STATUS_BADGE[idea.status];

  const decide = useMutation({
    mutationFn: (accept: boolean) =>
      api.decideKaizen(idea.id, {
        accept,
        note: note.trim() || undefined,
        // Replacing the operator's guess with the real figure recalculates
        // payback and ROI on the spot.
        investment_paise: realCost ? Math.round(Number(realCost) * 100) : undefined,
      }),
    onSuccess: () => {
      setDeclining(false);
      setNote("");
      onChanged();
    },
  });

  const supplyRate = useMutation({
    mutationFn: () =>
      api.supplyKaizenRate(idea.id, {
        key: idea.impact.saving.missing_rate_key ?? "",
        paise: Math.round(Number(rate) * 100),
      }),
    onSuccess: () => {
      setRate("");
      onChanged();
    },
  });

  return (
    <Card className="rise" style={{ animationDelay: `${Math.min(index * 45, 300)}ms` }}>
      <CardContent className="pt-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="text-3 tnum text-xs font-semibold">{idea.reference}</span>
              <Badge variant={badge.variant}>{badge.label}</Badge>
              {idea.track === "quick" ? <Badge variant="neutral">{t("Observation")}</Badge> : null}
              {idea.station_code ? <Badge variant="neutral">{idea.station_code}</Badge> : null}
              {idea.safety_flagged ? (
                <Badge variant="warn">
                  <ShieldAlert className="size-3" />
                  {t("Safety")}
                </Badge>
              ) : null}
              {idea.is_recurrence ? <Badge variant="warn">{t("Came back")}</Badge> : null}
              {waiting ? <Verdict verdict={idea.priority_verdict} /> : null}
            </div>
            <h3 className="text-[1.0625rem] leading-snug font-bold">{idea.title}</h3>
            <p className="text-3 mt-0.5 text-xs">
              {idea.credited_name}
              {idea.credited_name !== idea.submitted_by_name
                ? t(" · submitted by {0}", { 0: idea.submitted_by_name })
                : ""}
              {" · "}
              {new Date(idea.created_at).toLocaleDateString(activeLocale(), {
                day: "numeric",
                month: "short",
              })}
              {idea.category_label ? ` · ${idea.category_label}` : ""}
            </p>
          </div>

          <div className="text-right">
            <p className="label mb-0.5">{t("Hard saving / yr")}</p>
            <Money
              value={idea.impact.saving.hard_display}
              tone={idea.impact.saving.hard_paise > 0 ? "recovered" : "structural"}
              size="lg"
            />
            {idea.impact.saving.soft_paise > 0 ? (
              <p className="text-3 text-xs">
                {t("+ {0} capacity", { 0: idea.impact.saving.soft_display })}
              </p>
            ) : null}
          </div>
        </div>

        <p className="text-2 mt-3 text-sm leading-relaxed">{idea.description}</p>

        {idea.status !== "rejected" ? (
          <div className="mt-3">
            <Progress status={idea.status} />
          </div>
        ) : null}

        {!open ? (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="mt-3 text-sm font-medium text-[var(--color-accent)] hover:underline"
          >
            {t("See the evidence")}
          </button>
        ) : (
          <div className="mt-3 space-y-3">
            {idea.history ? <HistoryPanel history={idea.history} /> : null}
            <ImpactCard impact={idea.impact} />
            {idea.vision_findings.length > 0 ? (
              <VisionFindings findings={idea.vision_findings} readOnly />
            ) : null}
            {idea.forecast ? <ForecastPanel forecast={idea.forecast} /> : null}

            {idea.challenge?.offered ? (
              <div className="surface-2 rounded-xl border px-3.5 py-3">
                <p className="label mb-1">{t("An alternative was offered")}</p>
                <p className="text-sm leading-relaxed">{idea.challenge.alternative}</p>
                <p className="text-3 mt-1 text-xs">
                  {idea.challenge.chosen === "alternative"
                    ? t("The operator chose this.")
                    : idea.challenge.chosen
                      ? t("The operator kept their own idea.")
                      : t("Not yet decided.")}
                </p>
              </div>
            ) : null}

            {idea.similar && idea.similar.length > 0 ? (
              <div className="surface-2 rounded-xl border px-3.5 py-3">
                <p className="label mb-1">{t("Similar Kaizens in this plant")}</p>
                <ul className="space-y-1">
                  {idea.similar.map((match) => (
                    <li key={match.id} className="text-2 text-xs leading-relaxed">
                      <span className="tnum font-semibold">{match.reference}</span> —{" "}
                      {match.title} ({match.status})
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {idea.interview.length > 0 ? (
              <div className="surface-2 rounded-xl border px-3.5 py-3">
                <p className="label mb-1">{t("What the operator answered")}</p>
                <dl className="space-y-1">
                  {idea.interview.map((answer) => (
                    <div key={answer.key} className="flex justify-between gap-3 text-xs">
                      <dt className="text-2">{answer.question ?? answer.key}</dt>
                      <dd className="font-semibold whitespace-nowrap">
                        {String(answer.answer)}
                        {answer.unit ? ` ${answer.unit}` : ""}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            ) : null}

            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-3 text-xs hover:underline"
            >
              {t("Hide the evidence")}
            </button>
          </div>
        )}

        {idea.score_rationale && !open ? (
          <p className="text-2 mt-3 text-xs leading-relaxed whitespace-pre-line">
            {idea.score_rationale}
          </p>
        ) : null}

        {idea.decision_note ? (
          <p className="text-2 mt-3 text-xs leading-relaxed">
            <span className="font-semibold text-[var(--text)]">{t("Decision:")}</span>{" "}
            {idea.decision_note}
          </p>
        ) : null}

        {/* §7.3 — exactly one missing rate, asked of the approver, never of the
            operator, and never blocking the approval. The answer is written to
            Core's own cost master. */}
        {waiting && canDecide && idea.impact.saving.missing_rate_key ? (
          <div className="mt-3 rounded-xl border border-dashed px-3.5 py-3">
            <p className="label mb-1">{t("One number would finalise this")}</p>
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-40 flex-1">
                <Label htmlFor={`rate-${idea.id}`}>
                  {idea.impact.saving.missing_rate_label}
                </Label>
                <Input
                  id={`rate-${idea.id}`}
                  type="number"
                  inputMode="decimal"
                  value={rate}
                  onChange={(e) => setRate(e.target.value)}
                  placeholder="₹"
                />
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={!rate || supplyRate.isPending}
                onClick={() => supplyRate.mutate()}
              >
                {supplyRate.isPending ? <Loader2 className="size-3.5 animate-spin" /> : null}
                {t("Save for every future Kaizen")}
              </Button>
            </div>
            <p className="text-3 mt-1 text-xs">
              {t("You can approve without it — the saving simply stays provisional.")}
            </p>
          </div>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {idea.action_id ? (
            <Link
              href={`/actions/${idea.action_id}`}
              className="flex items-center gap-1 text-sm font-medium text-[var(--color-accent)] hover:underline"
            >
              {t("Tracked as an action")}<ArrowUpRight className="size-3.5" />
            </Link>
          ) : null}
          {idea.duplicate_action_id && !idea.action_id ? (
            <Link
              href={`/actions/${idea.duplicate_action_id}`}
              className="text-2 flex items-center gap-1 text-sm hover:underline"
            >
              {t("Already covered by an open action")}<ArrowUpRight className="size-3.5" />
            </Link>
          ) : null}

          {waiting && canDecide && !declining ? (
            <>
              <Button
                size="sm"
                variant="go"
                onClick={() => decide.mutate(true)}
                disabled={decide.isPending}
              >
                {decide.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Check className="size-3.5" />
                )}
                {t("Approve")}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setDeclining(true)}>
                <X className="size-3.5" />
                {t("Decline")}
              </Button>
              {idea.impact.investment_is_estimate && idea.impact.investment_paise > 0 ? (
                <span className="flex items-center gap-1.5">
                  <Label htmlFor={`cost-${idea.id}`} className="text-3 !mb-0 text-xs">
                    {t("Real cost ₹")}
                  </Label>
                  <Input
                    id={`cost-${idea.id}`}
                    type="number"
                    inputMode="numeric"
                    value={realCost}
                    onChange={(e) => setRealCost(e.target.value)}
                    className="h-8 w-24"
                    placeholder={String(Math.round(idea.impact.investment_paise / 100))}
                  />
                </span>
              ) : null}
            </>
          ) : null}
        </div>

        {decide.error ? (
          <div className="mt-2">
            <ErrorNote message={(decide.error as Error).message} />
          </div>
        ) : null}

        {declining ? (
          <div className="surface-2 mt-3 rounded-xl border p-3.5">
            {/* Required, never optional: an idea declined without a reason the
                operator can read is how a suggestion scheme dies in month two. */}
            <Label htmlFor={`note-${idea.id}`}>{t("Tell {0} why", { 0: idea.credited_name })}</Label>
            <Textarea
              id={`note-${idea.id}`}
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("Good thought, but the fixture change next month already moves this.")}
              autoFocus
            />
            <div className="mt-2.5 flex gap-2">
              <Button
                size="sm"
                disabled={!note.trim() || decide.isPending}
                onClick={() => decide.mutate(false)}
              >
                {t("Decline with reason")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDeclining(false)}>
                {t("Cancel")}
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
