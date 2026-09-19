"use client";

import { AlertTriangle, Camera, Clock, Flame, Star, TrendingUp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Money } from "@/components/ui/money";
import { cn } from "@/lib/utils";
import type {
  KaizenForecast,
  KaizenHistory,
  KaizenImpact,
  KaizenVerdict,
  VisionFinding,
} from "@/types/api";
import { useT } from "@/lib/i18n";
import { activeLocale } from "@/lib/format";

/**
 * The shared pieces of a Kaizen: the Business Impact Card, the Core History
 * Check panel, the forecast and the star ratings.
 *
 * One rule runs through all of them and is the reason the product is credible:
 * **money is shown in rupees, everything that cannot be costed is shown in
 * stars, and the two are never mixed into a single figure.** Hard and soft
 * savings likewise sit side by side and are never added into one headline.
 */

export const STAR_LABELS: Record<string, string> = {
  safety: "Safety",
  quality: "Quality",
  delivery: "Delivery",
  morale: "Morale / ergonomics",
  compliance: "Compliance / audit",
};

/** Filled pips plus the number — never colour alone. */
export function Stars({ value, label }: { value: number; label?: string }) {
  const t = useT();
  return (
    <span
      className="flex items-center gap-0.5"
      aria-label={t("{0} {1} of 5", { 0: label ?? "Rating", 1: value })}
    >
      {[1, 2, 3, 4, 5].map((n) => (
        <Star
          key={n}
          className={cn(
            "size-3.5",
            n <= value
              ? "fill-[var(--color-brand)] text-[var(--color-brand)]"
              : "text-[var(--color-ink-300)]",
          )}
        />
      ))}
    </span>
  );
}

export function Verdict({ verdict }: { verdict: KaizenVerdict | null }) {
  const t = useT();
  if (!verdict) return null;
  const variant = verdict === "HIGH" ? "warn" : verdict === "MEDIUM" ? "accent" : "neutral";
  return <Badge variant={variant}>{t("{0} priority", { 0: verdict })}</Badge>;
}

/**
 * Step 5A — what the plant already knows about this problem.
 *
 * Every rupee on this panel came out of Core's cost engine on events Core
 * itself recorded. None of it is estimated and none of it is the operator's
 * guess, which is exactly why it is persuasive. When there is nothing to show
 * it says so plainly rather than padding.
 */
export function HistoryPanel({
  history,
  compact = false,
}: {
  history: KaizenHistory;
  compact?: boolean;
}) {
  const t = useT();
  if (!history.found) {
    return (
      <div className="surface-2 rounded-xl border px-3.5 py-3">
        <p className="label mb-1">{t("What your plant already knows")}</p>
        <p className="text-2 text-sm leading-relaxed">{history.message}</p>
      </div>
    );
  }

  return (
    <div className="surface-2 rounded-xl border px-3.5 py-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <p className="label">{t("What your plant already knows")}</p>
        {history.trend === "rising" ? (
          <Badge variant="warn">
            <TrendingUp className="size-3" />
            {t("Getting worse")}
          </Badge>
        ) : null}
        {history.rank && history.rank <= 10 ? (
          <Badge variant="accent">{t("#{0} recurring loss", { 0: history.rank })}</Badge>
        ) : null}
      </div>

      {history.subject ? (
        <p className="mb-2 text-sm font-semibold">{history.subject}</p>
      ) : null}

      {!compact ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <tbody>
              {history.tiers.map((tier) => (
                <tr key={tier.scope} className="border-t first:border-t-0">
                  <td className="text-2 py-1.5 pr-3">{tier.label}</td>
                  <td className="tnum text-3 py-1.5 pr-3 text-right whitespace-nowrap">
                    {t("{0} event{1}", { 0: tier.events, 1: tier.events === 1 ? "" : "s" })}
                  </td>
                  {tier.minutes > 0 ? (
                    <td className="tnum text-3 py-1.5 pr-3 text-right whitespace-nowrap">
                      {t("{0} hrs", { 0: Math.round(tier.minutes / 60) })}
                    </td>
                  ) : null}
                  <td className="py-1.5 text-right">
                    <Money value={tier.cost_display} tone="loss" size="xs" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <p className="text-2 mt-2 text-xs leading-relaxed">{history.message}</p>

      {history.last_occurred_on ? (
        <p className="text-3 mt-1 text-[0.6875rem]">
          {t("Last occurrence {0}{1}", { 0: new Date(history.last_occurred_on).toLocaleDateString(activeLocale(), {
            day: "numeric",
            month: "short",
          }), 1: history.annual_run_rate_paise > 0
            ? ` · run-rate if unfixed ${history.annual_run_rate_display}/yr`
            : "" })}
        </p>
      ) : null}

      {history.recurrence_of ? (
        <p className="mt-2 rounded-lg bg-[var(--color-stopped-dim)] px-2.5 py-2 text-xs leading-relaxed text-[var(--color-loss)]">
          <strong>{t("Recurrence.")}</strong> {history.recurrence_of.reference} {" "}{t("was closed for this and the problem has come back — that countermeasure did not hold.")}
        </p>
      ) : history.open_action ? (
        <p className="text-2 mt-2 rounded-lg px-2.5 py-2 text-xs leading-relaxed surface">
          <strong className="text-[var(--text)]">{history.open_action.reference}</strong> {t("is already open on this")}{history.open_action.owner_name ? t(", with {0}", { 0: history.open_action.owner_name }) : ""}{t(". Your report is logged against it — no duplicate action is created.")}
        </p>
      ) : null}

      {history.memory.length > 0 ? (
        <div className="mt-2">
          <p className="label mb-1">{t("We fixed something like this before")}</p>
          {history.memory.slice(0, 2).map((entry) => (
            <p key={entry.id} className="text-2 text-xs leading-relaxed">
              {entry.what_fixed_it}
              {entry.fixed_by_name ? ` — ${entry.fixed_by_name}` : ""}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The Business Impact Card.
 *
 * Hard and soft savings are two columns, never one. An honest ₹0 hard saving is
 * what makes the ₹4,00,000 on the next Kaizen believable.
 */
export function ImpactCard({ impact }: { impact: KaizenImpact }) {
  const t = useT();
  const stars = Object.entries(impact.stars).filter(([, value]) => (value ?? 0) > 0);
  const nothing = impact.cost_of_doing_nothing;

  return (
    <div className="surface-2 rounded-xl border p-3.5">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <p className="label mb-0.5">{t("Hard saving · cash")}</p>
          <Money
            value={impact.saving.hard_display}
            tone={impact.saving.hard_paise > 0 ? "recovered" : "structural"}
            size="lg"
          />
          <p className="text-3 text-[0.6875rem]">{t("counts in the plant total")}</p>
        </div>
        <div>
          <p className="label mb-0.5">{t("Soft saving · capacity")}</p>
          <Money
            value={impact.saving.soft_display}
            tone={impact.saving.soft_paise > 0 ? "accent" : "structural"}
            size="lg"
          />
          <p className="text-3 text-[0.6875rem]">{t("real hours, reported separately")}</p>
        </div>
      </div>

      {impact.saving.basis ? (
        <p className="text-2 mt-2.5 border-t pt-2.5 text-xs leading-relaxed whitespace-pre-line">
          {impact.saving.basis}
        </p>
      ) : null}

      {impact.saving.provisional && impact.saving.provisional_note ? (
        <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-[var(--color-warn-dim)] px-2.5 py-2 text-xs leading-relaxed text-[var(--color-warn)]">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{impact.saving.provisional_note}</span>
        </p>
      ) : null}

      {stars.length > 0 ? (
        <div className="mt-3 border-t pt-2.5">
          <p className="label mb-1.5">{t("Not costed — shown in stars, never in rupees")}</p>
          <dl className="grid gap-1 sm:grid-cols-2">
            {stars.map(([key, value]) => (
              <div key={key} className="flex items-center justify-between gap-3">
                <dt className="text-2 text-xs">{STAR_LABELS[key] ?? key}</dt>
                <dd>
                  <Stars value={value ?? 0} label={STAR_LABELS[key]} />
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      <div className="text-2 mt-3 grid gap-x-4 gap-y-1 border-t pt-2.5 text-xs sm:grid-cols-2">
        <span className="flex items-center justify-between gap-2">
          <span>{t("Investment")}</span>
          <span className="tnum font-semibold text-[var(--text)]">
            {impact.investment_display}
            {impact.investment_is_estimate && impact.investment_paise > 0 ? (
              <span className="text-3 font-normal"> {t("(operator estimate)")}</span>
            ) : null}
          </span>
        </span>
        {impact.payback_months !== null ? (
          <span className="flex items-center justify-between gap-2">
            <span>{t("Payback")}</span>
            <span className="tnum font-semibold text-[var(--text)]">
              {impact.payback_months < 1
                ? t("{0} days", { 0: Math.max(1, Math.round(impact.payback_months * 30)) })
                : t("{0} months", { 0: impact.payback_months })}
            </span>
          </span>
        ) : null}
        <span className="flex items-center justify-between gap-2">
          <span>{t("Effort")}</span>
          <Stars value={impact.effort_score} label={t("Ease")} />
        </span>
        {impact.roi_percent !== null ? (
          <span className="flex items-center justify-between gap-2">
            <span>{t("First-year return")}</span>
            <span className="tnum font-semibold text-[var(--text)]">
              {Math.round(impact.roi_percent)}%
            </span>
          </span>
        ) : null}
      </div>

      {nothing.per_month_paise > 0 ? (
        <div className="mt-3 rounded-lg bg-[var(--color-stopped-dim)] px-3 py-2.5">
          <p className="mb-0.5 flex items-center gap-1.5 text-[0.6875rem] font-bold tracking-wide text-[var(--color-loss)] uppercase">
            <Flame className="size-3.5" />
            {t("Cost of doing nothing")}
          </p>
          {/* Same number, opposite framing. "₹1,687 walks out of this factory
              every month you don't decide" is a decision; an annual saving is
              a nice-to-have. */}
          <p className="text-sm leading-relaxed text-[var(--color-loss)]">
            <strong>{nothing.per_month_display}</strong>{" "}
            {t("leaves this factory every month this waits.")}
            {nothing.days_waiting > 0 ? (
              <>
                {" "}
                <strong>{nothing.since_submitted_display}</strong>{" "}
                {nothing.days_waiting === 1
                  ? t("since it was submitted {0} day ago.", { 0: nothing.days_waiting })
                  : t("since it was submitted {0} days ago.", { 0: nothing.days_waiting })}
              </>
            ) : null}
          </p>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Step 9 — what will go wrong, shown to the approver at the moment of decision.
 *
 * Whether this came from the plant's own Action history or from a rule is
 * stated plainly: an approver told "11 days on average here" should know
 * whether that is their plant or a default.
 */
export function ForecastPanel({ forecast }: { forecast: KaizenForecast }) {
  const t = useT();
  return (
    <div className="surface-2 rounded-xl border px-3.5 py-3">
      <p className="label mb-2">{t("Implementation forecast")}</p>
      <div className="mb-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
        <span>
          <span className="text-2">{t("Likely to complete")}{" "}</span>
          <strong className="tnum">{forecast.probability}%</strong>
        </span>
        <span className="flex items-center gap-1.5">
          <Clock className="text-3 size-3.5" />
          <strong className="tnum">{forecast.expected_days}</strong>
          <span className="text-2">{t("days expected")}</span>
        </span>
      </div>
      <ul className="space-y-1">
        {forecast.obstacles.map((obstacle) => (
          <li key={obstacle.what} className="flex items-start gap-2 text-xs leading-relaxed">
            <span
              className={cn(
                "mt-1 size-1.5 shrink-0 rounded-full",
                obstacle.risk === "high"
                  ? "bg-[var(--color-stopped)]"
                  : obstacle.risk === "medium"
                    ? "bg-[var(--color-warn)]"
                    : "bg-[var(--color-running)]",
              )}
            />
            <span>
              <span className="font-medium">{obstacle.what}</span>
              {obstacle.detail ? <span className="text-2"> — {obstacle.detail}</span> : null}
            </span>
          </li>
        ))}
      </ul>
      {forecast.advice ? (
        <p className="text-2 mt-2 rounded-lg surface px-2.5 py-2 text-xs leading-relaxed">
          💡 {forecast.advice}
        </p>
      ) : null}
      <p className="text-3 mt-1.5 text-[0.6875rem]">
        {forecast.learned
          ? t("Based on how long actions actually take at this plant.")
          : t("Based on general rules — this sharpens as the plant closes more actions.")}
      </p>
    </div>
  );
}

/**
 * Step 2's findings, shown ONLY to the operator.
 *
 * Nothing here reaches a supervisor until the operator taps "Add to this
 * Kaizen". The moment operators believe the camera reports them to management,
 * they stop taking photos — and Step 2 dies with the habit.
 */
export function VisionFindings({
  findings,
  onToggle,
  readOnly = false,
}: {
  findings: VisionFinding[];
  onToggle?: (index: number) => void;
  readOnly?: boolean;
}) {
  const t = useT();
  if (findings.length === 0) return null;
  return (
    <div className="surface-2 rounded-xl border px-3.5 py-3">
      <p className="label mb-1 flex items-center gap-1.5">
        <Camera className="size-3" />
        {readOnly ? t("From the photo") : t("I looked at your photo. I can see:")}
      </p>
      {!readOnly ? (
        <p className="text-3 mb-2 text-xs leading-relaxed">
          {t("These are only shown to you. Anything you mark as not relevant is deleted and never reaches your supervisor.")}
        </p>
      ) : null}
      <ul className="space-y-1.5">
        {findings.map((finding, index) => (
          <li key={`${finding.category}-${index}`} className="flex items-start gap-2">
            {readOnly ? (
              <span className="text-2 text-xs leading-relaxed">• {finding.text}</span>
            ) : (
              <label className="flex cursor-pointer items-start gap-2 text-xs leading-relaxed">
                <input
                  type="checkbox"
                  checked={finding.accepted}
                  onChange={() => onToggle?.(index)}
                  className="mt-0.5 size-4 shrink-0 accent-[var(--color-brand)]"
                />
                <span>
                  {finding.text}
                  <span className="text-3 ml-1.5">
                    {finding.category.replace(/_/g, " ")}
                  </span>
                </span>
              </label>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
