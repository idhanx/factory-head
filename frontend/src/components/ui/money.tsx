"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { formatInr } from "@/lib/format";

/**
 * The one money component.
 *
 * Money is the product (§7: "the owner's home is money, not machines"), so it
 * gets a weight, a tabular face and a tracking treatment nothing else in the
 * system gets.
 *
 * `tone` carries the distinction the spec refuses to blur: recoverable losses
 * are actionable and read hot; structural losses are real, reported, and
 * deliberately quiet. They are never summed together.
 */
export function Money({
  value,
  tone = "neutral",
  size = "md",
  className,
  live,
}: {
  value: string;
  tone?: "neutral" | "loss" | "recovered" | "structural" | "accent";
  size?: "xs" | "sm" | "md" | "lg" | "xl" | "hero";
  className?: string;
  /** Adds the slow breath used on an open stop's running cost. */
  live?: boolean;
}) {
  const tones = {
    neutral: "",
    loss: "text-[var(--color-loss)]",
    recovered: "text-[var(--color-recovered)]",
    structural: "text-[var(--color-structural)]",
    accent: "text-[var(--color-accent)]",
  };
  const sizes = {
    xs: "text-xs font-semibold",
    sm: "text-sm font-semibold",
    md: "text-base font-semibold",
    lg: "text-2xl font-bold",
    xl: "text-[2.5rem] leading-[1.05] font-bold",
    hero: "text-[clamp(2.5rem,5vw,4rem)] leading-[0.98] font-bold",
  };

  // Large static figures get proportional digits in the UI sans. Anything that
  // moves — a live ticker — keeps the monospace tabular face so the number does
  // not jitter as its digits change width.
  const large = size === "xl" || size === "hero" || size === "lg";
  const face = large && !live ? "figure" : "money";

  return (
    <span className={cn(face, tones[tone], sizes[size], live && "ticker-live", className)}>
      {value}
    </span>
  );
}

/**
 * A money figure that counts up to its value on first paint.
 *
 * Used only where a number is the point of the screen — the owner's four
 * numbers. It is decoration with a job: it draws the eye to the figure the
 * owner opened the app to see. Respects `prefers-reduced-motion`, and settles
 * on the exact server-provided value rather than a rounded approximation.
 */
export function MoneyCounter({
  paise,
  display,
  tone = "neutral",
  size = "xl",
  className,
  durationMs = 900,
}: {
  paise: number;
  display: string;
  tone?: "neutral" | "loss" | "recovered" | "structural" | "accent";
  size?: "sm" | "md" | "lg" | "xl" | "hero";
  className?: string;
  durationMs?: number;
}) {
  const [shown, setShown] = useState(paise);
  const settled = useRef(false);

  useEffect(() => {
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduced || settled.current || paise === 0) {
      setShown(paise);
      settled.current = true;
      return;
    }

    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      // Ease-out cubic: fast at first, then settles — reads as the number
      // arriving rather than spinning.
      setShown(Math.round(paise * (1 - Math.pow(1 - t, 3))));
      if (t < 1) frame = requestAnimationFrame(tick);
      else settled.current = true;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [paise, durationMs]);

  // Always land on the server's own formatted string: the compact forms
  // (₹1.02L, ₹28.4L) come from the kernel and the client must not re-derive them.
  const text = settled.current || shown === paise ? display : formatInr(shown);

  // While counting, digits change every frame — tabular keeps the figure from
  // jittering. Once settled it hands back to the proportional display face.
  const running = !settled.current && shown !== paise;
  return (
    <Money
      value={text}
      tone={tone}
      size={size}
      className={cn(running && "money", className)}
    />
  );
}
