"use client";

import * as React from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A bottom sheet on a phone, a centred dialog on anything wider.
 *
 * The same shape the machine grid uses for stops and counts, lifted out so the
 * forms that edit plant data feel like the screens that capture it. Escape and
 * a tap outside both cancel; nothing is saved until the form's own button.
 */
export function Sheet({
  title,
  subtitle,
  onClose,
  children,
  className,
}: {
  title: string;
  subtitle?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-[oklch(15%_0.01_60/0.55)] backdrop-blur-sm sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className={cn(
          "rise surface elev-4 max-h-[92dvh] w-full max-w-2xl overflow-y-auto rounded-t-2xl border p-5 sm:rounded-2xl",
          className,
        )}
      >
        <div className="mb-5 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="display text-xl font-bold">{title}</h2>
            {subtitle ? <div className="text-2 mt-0.5 text-sm">{subtitle}</div> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-3 grid size-11 shrink-0 place-items-center rounded-lg border transition-colors hover:surface-2 hover:text-[var(--text)]"
          >
            <X className="size-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
