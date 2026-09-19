"use client";

import { useState } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button, type ButtonProps } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import type { MasterResource } from "@/types/api";
import { useT, type Translator } from "@/lib/i18n";

/**
 * Building blocks shared by every Settings tab.
 *
 * Plant data is edited rarely and by few people, but when it is wrong every
 * costed minute downstream is wrong with it. So every form validates before it
 * sends, every failure is shown in words, and nothing is ever deleted — rows are
 * retired, which keeps last month's events pointing at something real.
 */

// ---------------------------------------------------------------------------
// Form fields described as data, so seven master tables share one form
// ---------------------------------------------------------------------------

export type FieldSpec = {
  key: string;
  label: string;
  kind: "text" | "number" | "select" | "checkbox" | "time" | "date" | "password";
  required?: boolean;
  hint?: string;
  placeholder?: string;
  options?: Array<{ value: string; label: string }>;
  /** Select values that the API expects as numbers. */
  numeric?: boolean;
  min?: number;
  max?: number;
  step?: number;
  integer?: boolean;
  /** Span both columns of the form grid. */
  wide?: boolean;
  /** Shown but not editable (a plant code, say). */
  readOnly?: boolean;
  /** A natural key: set when the row is created, read-only afterwards. */
  createOnly?: boolean;
  /** Leave the key out of the payload when blank, rather than sending null. */
  omitIfBlank?: boolean;
};

/** The edit form's view of a field list: natural keys become read-only. */
export function forEdit(fields: FieldSpec[]): FieldSpec[] {
  return fields.map((field) =>
    field.createOnly
      ? { ...field, readOnly: true, hint: field.hint ?? "Set when the row was created; cannot be changed." }
      : field,
  );
}

export type FormValues = Record<string, string | boolean>;

export function toValues(row: object, fields: FieldSpec[]): FormValues {
  const source = row as Record<string, unknown>;
  const values: FormValues = {};
  for (const field of fields) {
    const raw = source[field.key];
    if (field.kind === "checkbox") values[field.key] = Boolean(raw);
    else if (raw === null || raw === undefined) values[field.key] = "";
    else if (field.kind === "time") values[field.key] = String(raw).slice(0, 5);
    else values[field.key] = String(raw);
  }
  return values;
}

/** Turn text inputs back into the API's types. Returns per-field errors, or the payload. */
export function fromValues(
  values: FormValues,
  fields: FieldSpec[],
  /** The caller's translator. Validation text is read, so it is translated. */
  t: Translator = (source) => source,
): { payload: Record<string, unknown>; errors: Record<string, string> } {
  const payload: Record<string, unknown> = {};
  const errors: Record<string, string> = {};

  for (const field of fields) {
    if (field.readOnly) continue;
    const value = values[field.key];

    if (field.kind === "checkbox") {
      payload[field.key] = Boolean(value);
      continue;
    }

    const text = typeof value === "string" ? value.trim() : "";
    if (text === "") {
      if (field.required) errors[field.key] = t("{0} is required.", { 0: t(field.label) });
      else if (!field.omitIfBlank) payload[field.key] = null;
      continue;
    }

    if (field.kind === "number" || (field.kind === "select" && field.numeric)) {
      const n = Number(text);
      if (!Number.isFinite(n)) errors[field.key] = t("{0} must be a number.", { 0: t(field.label) });
      else if (field.integer && !Number.isInteger(n))
        errors[field.key] = t("{0} must be a whole number.", { 0: t(field.label) });
      else if (field.min !== undefined && n < field.min)
        errors[field.key] = t("{0} cannot be below {1}.", { 0: t(field.label), 1: field.min });
      else if (field.max !== undefined && n > field.max)
        errors[field.key] = t("{0} cannot be above {1}.", { 0: t(field.label), 1: field.max });
      else payload[field.key] = n;
      continue;
    }

    if (field.kind === "password" && field.min !== undefined && text.length < field.min) {
      errors[field.key] = t("{0} must be at least {1} characters.", {
        0: t(field.label),
        1: field.min,
      });
      continue;
    }

    payload[field.key] = text;
  }

  return { payload, errors };
}

export function FieldInput({
  field,
  idPrefix,
  value,
  error,
  onChange,
}: {
  field: FieldSpec;
  idPrefix: string;
  value: string | boolean;
  error?: string;
  onChange: (value: string | boolean) => void;
}) {
  const t = useT();
  const id = `${idPrefix}-${field.key}`;
  const hintId = field.hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  if (field.kind === "checkbox") {
    return (
      <div className={cn(field.wide && "sm:col-span-2")}>
        <label htmlFor={id} className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border px-3">
          <input
            id={id}
            type="checkbox"
            checked={Boolean(value)}
            disabled={field.readOnly}
            onChange={(e) => onChange(e.target.checked)}
            aria-describedby={describedBy}
            className="size-5"
          />
          <span className="text-sm font-medium">{t(field.label)}</span>
        </label>
        {field.hint ? <p id={hintId} className="text-3 mt-1 text-xs">{t(field.hint)}</p> : null}
      </div>
    );
  }

  return (
    <div className={cn(field.wide && "sm:col-span-2")}>
      <Label htmlFor={id}>
        {t(field.label)}
        {field.required ? null : <span className="text-3 font-normal"> {t("(optional)")}</span>}
      </Label>
      {field.kind === "select" ? (
        <Select
          id={id}
          value={String(value)}
          disabled={field.readOnly}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
        >
          {field.required ? null : <option value="">—</option>}
          {field.options?.map((option) => (
            <option key={option.value} value={option.value}>
              {t(option.label)}
            </option>
          ))}
        </Select>
      ) : (
        <Input
          id={id}
          type={field.kind === "number" ? "text" : field.kind}
          inputMode={field.kind === "number" ? "decimal" : undefined}
          value={String(value)}
          placeholder={field.placeholder ? t(field.placeholder) : undefined}
          readOnly={field.readOnly}
          disabled={field.readOnly}
          autoComplete={field.kind === "password" ? "new-password" : undefined}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          className={cn(error && "border-[var(--color-stopped)]")}
        />
      )}
      {field.hint ? <p id={hintId} className="text-3 mt-1 text-xs leading-relaxed">{t(field.hint)}</p> : null}
      {error ? (
        <p id={errorId} className="mt-1 text-xs font-medium text-[var(--color-loss)]">{error}</p>
      ) : null}
    </div>
  );
}

/** A two-column form over a field list. The caller owns the mutation. */
export function RecordForm({
  idPrefix,
  fields,
  initial,
  submitLabel,
  busy,
  serverError,
  onSubmit,
  onCancel,
  children,
}: {
  idPrefix: string;
  fields: FieldSpec[];
  initial: FormValues;
  submitLabel: string;
  busy: boolean;
  serverError?: string | null;
  onSubmit: (payload: Record<string, unknown>) => void;
  onCancel: () => void;
  children?: React.ReactNode;
}) {
  const t = useT();
  const [values, setValues] = useState<FormValues>(initial);
  const [errors, setErrors] = useState<Record<string, string>>({});

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const result = fromValues(values, fields, t);
        setErrors(result.errors);
        if (Object.keys(result.errors).length === 0) onSubmit(result.payload);
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {fields.map((field) => (
          <FieldInput
            key={field.key}
            field={field}
            idPrefix={idPrefix}
            value={values[field.key] ?? ""}
            error={errors[field.key]}
            onChange={(value) => setValues((prev) => ({ ...prev, [field.key]: value }))}
          />
        ))}
      </div>
      {children}
      {Object.keys(errors).length ? (
        <ErrorNote message={t("Some fields need fixing — see the notes under them.")} />
      ) : null}
      {serverError ? <ErrorNote message={serverError} /> : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="lg" disabled={busy}>
          {busy ? t("Saving…") : t(submitLabel)}
        </Button>
        <Button type="button" size="lg" variant="ghost" onClick={onCancel}>
          {t("Cancel")}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Table chrome
// ---------------------------------------------------------------------------

/** Row-level buttons: compact type, still a 44px target. */
export function RowButton({ className, ...props }: ButtonProps) {
  return <Button size="sm" variant="outline" className={cn("h-11 px-4", className)} {...props} />;
}

export function Toolbar({
  title,
  hint,
  showInactive,
  onShowInactive,
  inactiveLabel = "Show inactive",
  addLabel,
  onAdd,
  children,
}: {
  title: string;
  hint?: React.ReactNode;
  showInactive?: boolean;
  onShowInactive?: (value: boolean) => void;
  inactiveLabel?: string;
  addLabel?: string;
  onAdd?: () => void;
  children?: React.ReactNode;
}) {
  const t = useT();
  const toggleId = `show-inactive-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 className="display text-lg font-bold">{t(title)}</h2>
        {typeof hint === "string" ? (
          <p className="text-2 mt-0.5 max-w-2xl text-sm leading-relaxed">{t(hint)}</p>
        ) : hint ? (
          <p className="text-2 mt-0.5 max-w-2xl text-sm leading-relaxed">{hint}</p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {children}
        {onShowInactive ? (
          <label
            htmlFor={toggleId}
            className="text-2 flex min-h-11 cursor-pointer items-center gap-2.5 rounded-full border px-4 text-sm font-medium"
          >
            <input
              id={toggleId}
              type="checkbox"
              checked={Boolean(showInactive)}
              onChange={(e) => onShowInactive(e.target.checked)}
              className="size-4"
            />
            {t(inactiveLabel)}
          </label>
        ) : null}
        {onAdd ? (
          <Button size="lg" onClick={onAdd}>
            <Plus className="size-4" />
            {addLabel ? t(addLabel) : t("Add")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

export type Column<T> = {
  header: string;
  cell: (row: T) => React.ReactNode;
  className?: string;
};

export function DataTable<T extends { id: string }>({
  rows,
  columns,
  actions,
  inactive,
}: {
  rows: T[];
  columns: Column<T>[];
  actions?: (row: T) => React.ReactNode;
  inactive?: (row: T) => boolean;
}) {
  const t = useT();
  return (
    <div className="surface elev-1 overflow-x-auto rounded-[var(--radius-card)] border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b">
            {columns.map((col) => (
              <th key={col.header} className="label px-4 py-3 text-left whitespace-nowrap">
                {t(col.header)}
              </th>
            ))}
            {actions ? <th className="label px-4 py-3 text-right">
              <span className="sr-only">{t("Actions")}</span>
            </th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              className={cn("border-b last:border-0", inactive?.(row) && "surface-2 text-3")}
            >
              {columns.map((col) => (
                <td key={col.header} className={cn("px-4 py-2 align-middle", col.className)}>
                  {col.cell(row)}
                </td>
              ))}
              {actions ? (
                <td className="px-4 py-2 text-right">
                  <div className="flex justify-end gap-2">{actions(row)}</div>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ActiveBadge({ active }: { active: boolean }) {
  const t = useT();
  return active ? <Badge variant="recovered">{t("Active")}</Badge> : <Badge variant="neutral">{t("Inactive")}</Badge>;
}

/** Loading, failed and empty are three different answers; this keeps them apart. */
export function ListState({
  loading,
  error,
  empty,
  emptyTitle,
  emptyHint,
  onRetry,
  children,
}: {
  loading: boolean;
  error: Error | null;
  empty: boolean;
  emptyTitle: string;
  emptyHint?: string;
  onRetry?: () => void;
  children: React.ReactNode;
}) {
  const t = useT();
  if (loading) return <Skeleton className="h-64" />;
  if (error) {
    return (
      <div className="space-y-3">
        <ErrorNote message={t("Could not load this list: {0}", { 0: error.message })} />
        {onRetry ? (
          <Button size="lg" variant="outline" onClick={onRetry}>
            {t("Try again")}
          </Button>
        ) : null}
      </div>
    );
  }
  if (empty) return <Empty title={t(emptyTitle)} hint={emptyHint ? t(emptyHint) : undefined} />;
  return <>{children}</>;
}

export function Notice({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  const t = useT();
  return (
    <div
      role="status"
      className="surface-2 rise mb-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border px-4 py-2 text-sm"
    >
      <span>{t(message)}</span>
      <Button size="sm" variant="ghost" className="h-11" onClick={onDismiss}>
        {t("Dismiss")}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cache: a settings edit must reach the screens that read the same rows
// ---------------------------------------------------------------------------

const APP_KEYS: Record<MasterResource, string[][]> = {
  stations: [["stations"], ["grid"], ["skills-matrix"]],
  "reason-codes": [["reasons"]],
  "defect-codes": [["defects"]],
  parts: [["parts"], ["jobs"]],
  operators: [["operators"], ["skills-matrix"]],
  shifts: [["shifts"]],
  "alert-recipients": [],
};

export function invalidateMaster(queryClient: QueryClient, resource: MasterResource) {
  void queryClient.invalidateQueries({ queryKey: ["settings", resource] });
  for (const key of APP_KEYS[resource]) void queryClient.invalidateQueries({ queryKey: key });
}
