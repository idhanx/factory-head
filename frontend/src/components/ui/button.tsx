"use client";

import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const button = cva(
  [
    "relative inline-flex items-center justify-center gap-2 rounded-full font-medium select-none",
    "transition-[transform,box-shadow,background-color,color] duration-150",
    "active:translate-y-px",
    "disabled:pointer-events-none disabled:opacity-45",
    // A subtle top highlight is what separates a button that reads as a
    // physical control from one that reads as a coloured rectangle.
    "before:pointer-events-none before:absolute before:inset-0 before:rounded-[inherit]",
    "before:shadow-[inset_0_1px_0_oklch(100%_0_0/0.18)]",
  ],
  {
    variants: {
      variant: {
        // The orange carries its own dark-brown ink. It is a light fill, so the
        // white text every other filled variant uses would fail contrast on it.
        primary:
          "bg-[var(--color-brand)] text-[var(--color-brand-ink)] shadow-[var(--shadow-2)] hover:brightness-105 hover:shadow-[var(--shadow-3)] before:shadow-none",
        feature:
          "rail border border-rail shadow-[var(--shadow-1)] hover:bg-[var(--rail-2)] before:shadow-none",
        stop:
          "bg-[var(--color-stopped)] text-white shadow-[var(--shadow-2)] hover:brightness-110 hover:shadow-[0_8px_24px_color-mix(in_oklab,var(--color-stopped)_35%,transparent)]",
        go:
          "bg-[var(--color-running)] text-white shadow-[var(--shadow-2)] hover:brightness-110 hover:shadow-[0_8px_24px_color-mix(in_oklab,var(--color-running)_35%,transparent)]",
        outline:
          "surface border text-[var(--text)] shadow-[var(--shadow-1)] hover:surface-2 hover:border-[var(--border-strong)]",
        subtle: "surface-2 text-[var(--text)] hover:surface-3 before:shadow-none",
        ghost: "text-2 hover:surface-2 hover:text-[var(--text)] before:shadow-none",
      },
      size: {
        sm: "h-8 px-3.5 text-[0.8125rem]",
        md: "h-10 px-5 text-sm",
        lg: "h-12 px-6 text-[0.9375rem]",
        // Capture screens only — gloves, oily hands, a running machine.
        touch: "touch px-6 text-base font-semibold",
        xl: "touch-lg px-8 text-lg font-semibold",
        icon: "size-10",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp ref={ref} className={cn(button({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = "Button";
