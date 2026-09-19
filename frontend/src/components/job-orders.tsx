"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import type { JobOrder } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * Creating today's jobs, from the Shift Start screen or Settings.
 *
 * The budget is two minutes for the whole morning's list, so the form asks for
 * the two things only the supervisor knows — which part, how many — and lets the
 * server number the job. Both sheets refresh the `jobs` cache on success, which
 * is what every station's job select reads, so the new job is pickable at once.
 */

export const JOB_STATUS_LABEL: Record<string, string> = {
  planned: "Planned",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  cancelled: "Cancelled",
};

/** Completed and cancelled jobs stay on record but cannot be put on a machine. */
export function isAssignable(job: JobOrder): boolean {
  return job.status !== "completed" && job.status !== "cancelled";
}

export function jobLabel(job: JobOrder): string {
  return [`#${job.job_number}`, job.part_number, `${job.planned_quantity} pcs`]
    .filter(Boolean)
    .join(" · ");
}

function positiveInt(value: string): number | null {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function NewJobOrderSheet({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated?: (job: JobOrder) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const parts = useQuery({ queryKey: ["parts"], queryFn: api.parts });
  const [partId, setPartId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [jobNumber, setJobNumber] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.createJobOrder({
        part_id: partId,
        planned_quantity: positiveInt(quantity)!,
        ...(jobNumber.trim() ? { job_number: jobNumber.trim() } : {}),
        ...(dueDate ? { due_date: dueDate } : {}),
      }),
    onSuccess: (job) => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      onCreated?.(job);
      onClose();
    },
  });

  const activeParts = (parts.data ?? []).filter((p) => p.is_active !== false);

  return (
    <Sheet title={t("New job order")} subtitle={t("Which part, and how many.")} onClose={onClose}>
      <form
        className="space-y-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (!partId) return setInvalid(t("Pick the part this job makes."));
          if (!positiveInt(quantity)) return setInvalid(t("Planned quantity must be a whole number above 0."));
          setInvalid(null);
          create.mutate();
        }}
      >
        <div>
          <Label htmlFor="job-part">{t("Part")}</Label>
          {parts.error ? (
            <ErrorNote message={t("Could not load parts: {0}", { 0: parts.error.message })} />
          ) : (
            <Select
              id="job-part"
              value={partId}
              onChange={(e) => setPartId(e.target.value)}
              disabled={parts.isLoading}
              autoFocus
            >
              <option value="">{parts.isLoading ? t("Loading parts…") : t("Select a part…")}</option>
              {activeParts.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.part_number}
                  {p.description ? ` — ${p.description}` : ""}
                  {p.customer_name ? ` (${p.customer_name})` : ""}
                </option>
              ))}
            </Select>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="job-qty">{t("Planned quantity")}</Label>
            <Input
              id="job-qty"
              inputMode="numeric"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value.replace(/\D/g, ""))}
              placeholder="500"
              className="money text-lg font-semibold"
            />
          </div>
          <div>
            <Label htmlFor="job-due">{t("Due date (optional)")}</Label>
            <Input id="job-due" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
          </div>
        </div>

        <div>
          <Label htmlFor="job-number">{t("Job number (optional)")}</Label>
          <Input
            id="job-number"
            value={jobNumber}
            onChange={(e) => setJobNumber(e.target.value)}
            placeholder={t("Leave blank to number it automatically")}
          />
        </div>

        {invalid ? <ErrorNote message={invalid} /> : null}
        {create.error ? <ErrorNote message={create.error.message} /> : null}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="touch" disabled={create.isPending}>
            {create.isPending ? t("Creating…") : t("Create job")}
          </Button>
          <Button type="button" size="touch" variant="ghost" onClick={onClose}>
            {t("Cancel")}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

/** Same part, same customer, a new number — yesterday's job again. */
export function RepeatJobOrderSheet({
  job,
  onClose,
  onCreated,
}: {
  job: JobOrder;
  onClose: () => void;
  onCreated?: (job: JobOrder) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [quantity, setQuantity] = useState(String(job.planned_quantity));
  const [dueDate, setDueDate] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const repeat = useMutation({
    mutationFn: () =>
      api.repeatJobOrder(job.id, {
        planned_quantity: positiveInt(quantity)!,
        ...(dueDate ? { due_date: dueDate } : {}),
      }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      onCreated?.(created);
      onClose();
    },
  });

  return (
    <Sheet
      title={t("Repeat #{0}", { 0: job.job_number })}
      subtitle={t("{0}{1} — gets a new job number.", { 0: job.part_number ?? "Same part", 1: job.customer_name ? ` · ${job.customer_name}` : "" })}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (!positiveInt(quantity)) return setInvalid(t("Planned quantity must be a whole number above 0."));
          setInvalid(null);
          repeat.mutate();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="repeat-qty">{t("Planned quantity")}</Label>
            <Input
              id="repeat-qty"
              inputMode="numeric"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value.replace(/\D/g, ""))}
              className="money text-lg font-semibold"
              autoFocus
            />
          </div>
          <div>
            <Label htmlFor="repeat-due">{t("Due date (optional)")}</Label>
            <Input id="repeat-due" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
          </div>
        </div>

        {invalid ? <ErrorNote message={invalid} /> : null}
        {repeat.error ? <ErrorNote message={repeat.error.message} /> : null}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="touch" disabled={repeat.isPending}>
            {repeat.isPending ? t("Creating…") : t("Create repeat job")}
          </Button>
          <Button type="button" size="touch" variant="ghost" onClick={onClose}>
            {t("Cancel")}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
