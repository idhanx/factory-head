import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badge = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[0.6875rem] font-semibold whitespace-nowrap",
  {
    variants: {
      variant: {
        neutral: "surface-2 text-2 border-[var(--border)]",
        running:
          "border-transparent bg-[var(--color-running-dim)] text-[var(--color-running)]",
        stopped:
          "border-transparent bg-[var(--color-stopped-dim)] text-[var(--color-stopped)]",
        // Recoverable reads hot: it is the money someone can still go and get.
        recoverable:
          "border-transparent bg-[var(--color-stopped-dim)] text-[var(--color-loss)]",
        // Structural stays muted on purpose — real money, but not actionable,
        // so it must never compete for attention with what can be fixed.
        structural: "surface-2 text-[var(--color-structural)] border-[var(--border)]",
        recovered:
          "border-transparent bg-[color-mix(in_oklab,var(--color-recovered)_14%,transparent)] text-[var(--color-recovered)]",
        warn: "border-transparent bg-[var(--color-warn-dim)] text-[var(--color-warn)]",
        accent: "border-transparent bg-[var(--color-accent-dim)] text-[var(--color-accent)]",
        // A solid orange chip — brand emphasis, not a verdict. Good and bad
        // news keep `recovered` and `warn`, which read as outcomes.
        brand: "brand border-transparent",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) {
  return <span className={cn(badge({ variant }), className)} {...props} />;
}

/** A pilot lamp with a halo. A flat dot reads as a bullet point. */
export function StatusLed({
  state,
  className,
}: {
  state: "running" | "stopped" | "idle";
  className?: string;
}) {
  const colour = {
    running: "var(--color-running)",
    stopped: "var(--color-stopped)",
    idle: "var(--color-idle)",
  }[state];

  return (
    <span
      className={cn("led", className)}
      style={{ background: colour, color: colour }}
      aria-label={state}
    />
  );
}
