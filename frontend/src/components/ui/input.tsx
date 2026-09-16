"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

const field = [
  "w-full rounded-lg border surface px-3.5 text-sm text-[var(--text)] outline-none",
  "shadow-[inset_0_1px_2px_oklch(20%_0.01_70/0.04)]",
  "placeholder:text-3 transition-[border-color,box-shadow] duration-150",
  "focus:border-[var(--color-accent)]",
  "focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--color-accent)_18%,transparent)]",
  "disabled:opacity-50",
].join(" ");

export const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn(field, "h-11", className)} {...props} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(field, "py-2.5 leading-relaxed", className)} {...props} />
));
Textarea.displayName = "Textarea";

export const Select = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, ...props }, ref) => (
  <select ref={ref} className={cn(field, "h-11 pr-8", className)} {...props} />
));
Select.displayName = "Select";

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("mb-1.5 block text-sm font-medium", className)} {...props} />;
}
