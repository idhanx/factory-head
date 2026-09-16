import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Three surfaces, because the layout language needs exactly three.
 *
 * `plain` is the working card — white, separated from the white canvas by its
 * border and shadow. `feature` is the pale peach panel: one or two per screen
 * at most, used where a figure or a chart is the point. `brand` is the orange,
 * reserved for the single thing on a screen worth acting on.
 *
 * `feature` and `brand` set their own text colour rather than inheriting the
 * page's, so their contents stay legible if the page palette changes again.
 */
export function Card({
  className,
  elevation = 2,
  tone = "plain",
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  elevation?: 1 | 2 | 3 | 4;
  tone?: "plain" | "feature" | "brand";
}) {
  return (
    <div
      className={cn(
        "rounded-[var(--radius-card)]",
        {
          plain: "surface border",
          feature: "rail border-rail border",
          brand: "brand border-transparent border",
        }[tone],
        { 1: "elev-1", 2: "elev-2", 3: "elev-3", 4: "elev-4" }[elevation],
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-center gap-3 px-5 pt-5 pb-3", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("label", className)} {...props} />;
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-5 pb-5", className)} {...props} />;
}

export function CardFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("surface-2 rounded-b-[var(--radius-card)] border-t px-5 py-3", className)}
      {...props}
    />
  );
}
