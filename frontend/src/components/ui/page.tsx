"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import { Money } from "@/components/ui/money";
import { useT } from "@/lib/i18n";

/**
 * The page furniture every module screen is built from.
 *
 * Two problems this exists to fix. Screens were *congested* — everything one
 * step from everything else, no rhythm to rest against — and at the same time
 * *empty*, a single narrow column on a 1440px display. Both come from every
 * page inventing its own spacing.
 *
 * So the rhythm is defined once, here:
 *
 *   page    →  a measured column, never edge-to-edge
 *   header  →  eyebrow · title · one sentence · actions
 *   section →  a labelled band with air above it
 *   grid    →  the one place column counts are decided
 *
 * The scale is deliberately generous. §7's brief is "thick fingers, poor light,
 * and a hurry", and an owner reading on a phone at 7 AM — density is not the
 * same thing as information, and a screen that looks busy reads as unfinished.
 */

/** The measured column. Wide enough for a 3-up grid, never the full 1440. */
export function Page({
  children,
  className,
  width = "default",
}: {
  children: React.ReactNode;
  className?: string;
  /** `narrow` for reading and single-task screens; `wide` for dense boards. */
  width?: "narrow" | "default" | "wide";
}) {
  const widths = {
    narrow: "max-w-3xl",
    default: "max-w-[80rem]",
    wide: "max-w-[96rem]",
  };
  return (
    <div className={cn("mx-auto w-full space-y-10", widths[width], className)}>{children}</div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  icon,
}: {
  eyebrow?: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <header className="rise flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        {eyebrow ? (
          <p className="label mb-2 flex items-center gap-1.5">
            {icon}
            {eyebrow}
          </p>
        ) : null}
        <h1 className="display text-[2rem] leading-[1.1] font-bold tracking-[-0.02em]">
          {title}
        </h1>
        {description ? (
          <p className="text-2 mt-2 max-w-2xl text-[0.9375rem] leading-relaxed">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

/**
 * A labelled band.
 *
 * The heading is small and quiet on purpose: it orients, it does not compete
 * with the numbers inside the band. Air above it is what separates one idea
 * from the next, and it is the single biggest reason these screens stopped
 * reading as congested.
 */
export function Section({
  title,
  description,
  actions,
  children,
  className,
  delay = 0,
}: {
  title?: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  return (
    <section
      className={cn("rise min-w-0 space-y-4", className)}
      style={delay ? { animationDelay: `${delay}ms` } : undefined}
    >
      {title || actions ? (
        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
          <div className="min-w-0">
            {title ? (
              <h2 className="text-[1.0625rem] leading-tight font-bold tracking-[-0.01em]">
                {title}
              </h2>
            ) : null}
            {description ? (
              <p className="text-3 mt-1 max-w-xl text-[0.8125rem] leading-relaxed">
                {description}
              </p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

/** Column counts live here, so no screen hand-rolls a breakpoint. */
export function Grid({
  cols = 3,
  children,
  className,
}: {
  cols?: 2 | 3 | 4;
  children: React.ReactNode;
  className?: string;
}) {
  const layouts = {
    2: "sm:grid-cols-2",
    3: "sm:grid-cols-2 lg:grid-cols-3",
    4: "sm:grid-cols-2 xl:grid-cols-4",
  };
  return <div className={cn("grid gap-4", layouts[cols], className)}>{children}</div>;
}

/**
 * Change against the previous period.
 *
 * Polarity is explicit rather than inferred from the sign, because on half of
 * these numbers down is the good direction: PPM falling is a win, output
 * falling is not. Green-up / red-down applied blindly is how a dashboard tells
 * a plant its best week was its worst.
 *
 * Direction is carried by an arrow and a word as well as by colour — roughly
 * one man in twelve on this floor has a red-green deficiency.
 */
export function Delta({
  value,
  previous,
  /** "up" when a rise is good (output); "down" when a fall is good (PPM, loss). */
  good = "up",
  suffix = "%",
  className,
}: {
  value: number;
  previous: number;
  good?: "up" | "down";
  suffix?: string;
  className?: string;
}) {
  const t = useT();
  if (!Number.isFinite(value) || !Number.isFinite(previous) || previous === 0) return null;

  const change = ((value - previous) / Math.abs(previous)) * 100;
  // Under a point either way is noise, not a trend.
  if (Math.abs(change) < 1) {
    return (
      <span className={cn("text-3 inline-flex items-center gap-1 text-xs font-medium", className)}>
        <Minus className="size-3" />
        {t("flat")}
      </span>
    );
  }

  const rising = change > 0;
  const welcome = good === "up" ? rising : !rising;
  const Arrow = rising ? ArrowUpRight : ArrowDownRight;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 text-xs font-semibold",
        welcome ? "text-[var(--color-recovered)]" : "text-[var(--color-loss)]",
        className,
      )}
    >
      <Arrow className="size-3.5" />
      <span className="tnum">
        {Math.abs(Math.round(change))}
        {suffix}
      </span>
    </span>
  );
}

/**
 * One number, given room.
 *
 * A stat tile is the right form when the answer *is* the number — §7's owner
 * home is four of these and nothing else. It carries at most one supporting
 * line; anything more and it should have been a chart.
 */
export function Stat({
  label,
  value,
  display,
  tone = "plain",
  money,
  moneyTone = "neutral",
  unit,
  delta,
  hint,
  href,
  chip,
  children,
  delay = 0,
}: {
  label: string;
  /** A plain figure. Use `money`/`display` instead for rupees. */
  value?: React.ReactNode;
  display?: string;
  money?: boolean;
  moneyTone?: "neutral" | "loss" | "recovered" | "structural" | "accent";
  tone?: "plain" | "feature";
  unit?: string;
  delta?: React.ReactNode;
  hint?: React.ReactNode;
  href?: string;
  chip?: React.ReactNode;
  children?: React.ReactNode;
  delay?: number;
}) {
  const body = (
    <>
      <div className="mb-3 flex items-start justify-between gap-2">
        <span className="label">{label}</span>
        {chip}
      </div>

      <div className="flex flex-1 flex-wrap items-baseline gap-x-2 gap-y-1">
        {money && display ? (
          <Money value={display} tone={moneyTone} size="lg" />
        ) : (
          <span className="figure text-[2rem] leading-none font-bold tracking-[-0.02em]">
            {value}
          </span>
        )}
        {unit ? <span className="text-3 text-sm font-medium">{unit}</span> : null}
        {delta}
      </div>

      {children ? <div className="mt-3">{children}</div> : null}

      {hint ? (
        <span
          className={cn(
            "text-3 mt-4 flex items-center gap-1 text-xs leading-relaxed",
            href && "transition-colors group-hover:text-[var(--text)]",
          )}
        >
          {hint}
          {href ? (
            <ArrowRight className="size-3 shrink-0 transition-transform group-hover:translate-x-0.5" />
          ) : null}
        </span>
      ) : null}
    </>
  );

  const shell = cn(
    "rise elev-1 relative flex min-h-[8.5rem] flex-col overflow-hidden rounded-[var(--radius-card)] border p-5",
    tone === "feature" ? "rail border-rail" : "surface",
    href &&
      "group transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-[var(--shadow-3)]",
  );
  const style = delay ? { animationDelay: `${delay}ms` } : undefined;

  return href ? (
    <Link href={href} className={shell} style={style}>
      {body}
    </Link>
  ) : (
    <div className={shell} style={style}>
      {body}
    </div>
  );
}

/**
 * A panel: the neutral container a chart or a table sits in.
 *
 * Thinner than a Card on purpose — a chart brings its own visual weight, and
 * wrapping it in a heavy surface is what made these pages feel stacked.
 */
export function Panel({
  title,
  description,
  actions,
  children,
  className,
  tone = "plain",
  delay = 0,
}: {
  title?: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  tone?: "plain" | "feature";
  delay?: number;
}) {
  return (
    <div
      className={cn(
        "rise elev-1 min-w-0 rounded-[var(--radius-card)] border p-5",
        tone === "feature" ? "rail border-rail" : "surface",
        className,
      )}
      style={delay ? { animationDelay: `${delay}ms` } : undefined}
    >
      {title || actions ? (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
          <div className="min-w-0">
            {title ? <p className="label">{title}</p> : null}
            {description ? (
              <p className="text-3 mt-1.5 max-w-lg text-xs leading-relaxed">{description}</p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}
