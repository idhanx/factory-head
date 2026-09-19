"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("shimmer surface-2 rounded-lg", className)} />;
}

export function Separator({ className }: { className?: string }) {
  return <div className={cn("hairline my-4", className)} />;
}

/** An empty state that says what to do next, not just that there is nothing here. */
export function Empty({
  title,
  hint,
  icon,
  action,
}: {
  title: string;
  hint?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
}) {
  const t = useT();
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-[var(--radius-card)] border border-dashed px-6 py-16 text-center">
      {icon ? (
        <div className="surface-2 text-3 grid size-12 place-items-center rounded-full">
          {icon}
        </div>
      ) : null}
      <p className="font-semibold">{t(title)}</p>
      {hint ? <p className="text-2 max-w-md text-sm leading-relaxed">{t(hint)}</p> : null}
      {action}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  // Messages reach here from module-level code (`lib/api.ts`) that cannot call
  // a hook, so the translation happens at the point of display instead. Text
  // the server wrote has no dictionary entry and passes through as sent.
  const t = useT();
  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-lg border border-[color-mix(in_oklab,var(--color-stopped)_35%,transparent)] bg-[var(--color-stopped-dim)] px-4 py-3 text-sm text-[var(--color-loss)]"
    >
      <span aria-hidden className="mt-0.5 shrink-0 font-bold">!</span>
      <span>{t(message)}</span>
    </div>
  );
}

/** Page title block. Consistent rhythm across every screen. */
export function PageHeader({
  title,
  subtitle,
  actions,
  eyebrow,
}: {
  title: string;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  eyebrow?: string;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow ? <p className="label mb-1.5">{eyebrow}</p> : null}
        <h1 className="display text-[1.75rem] font-bold">{title}</h1>
        {subtitle ? <div className="text-2 mt-1 text-sm">{subtitle}</div> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}
