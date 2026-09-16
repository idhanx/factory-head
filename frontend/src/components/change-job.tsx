"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, WifiOff } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useSync } from "@/lib/offline/status";
import type { LocalTile } from "@/lib/offline/grid";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label, Select } from "@/components/ui/input";
import { ErrorNote, Skeleton } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import { JOB_STATUS_LABEL, NewJobOrderSheet, isAssignable } from "@/components/job-orders";
import type { JobOrder } from "@/types/api";

/**
 * §14.3 — the mid-shift part change, from the tile.
 *
 * Job A pauses with its numbers intact; the gap until the first count on Job B
 * is logged as a Setup/Changeover stop, which the server closes by itself at
 * that first count. Online only: the server has to pause one job and start the
 * next in a single step, and a change queued for later would tag the counts
 * made in between to the wrong job.
 */
export function ChangeJobSheet({
  tile,
  onClose,
  onDone,
}: {
  tile: LocalTile;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const online = useSync((s) => s.online);
  const [jobId, setJobId] = useState("");
  const [operatorId, setOperatorId] = useState("");
  const [creating, setCreating] = useState(false);

  // The same key Shift Start uses, so a job created in either place shows in both.
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.jobOrders });
  const operators = useQuery({ queryKey: ["operators"], queryFn: api.operators });

  const change = useMutation({
    mutationFn: () => api.changeJob(tile.station_id, jobId, operatorId || undefined),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["grid"] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      onDone(result.message || `${tile.code} changed job.`);
    },
  });

  // One sheet at a time: two stacked dialogs both listen for Escape.
  if (creating) {
    return (
      <NewJobOrderSheet
        onClose={() => setCreating(false)}
        onCreated={(job) => {
          setJobId(job.id);
          setCreating(false);
        }}
      />
    );
  }

  const runnable = (jobs.data ?? []).filter(isAssignable);
  const activeOperators = (operators.data ?? []).filter((o) => o.is_active !== false);

  return (
    <Sheet
      title={`Change job on ${tile.code}`}
      subtitle={
        tile.job_number
          ? `Now running #${tile.job_number}${tile.part_number ? ` · ${tile.part_number}` : ""}. Its counts stay with it.`
          : "Nothing is assigned to this machine yet."
      }
      onClose={onClose}
    >
      <form
        className="space-y-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (jobId && online) change.mutate();
        }}
      >
        <fieldset>
          <legend className="sr-only">Next job</legend>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span aria-hidden className="text-sm font-medium">Next job</span>
            <Button type="button" size="lg" variant="outline" onClick={() => setCreating(true)}>
              <Plus className="size-4" />
              New job order
            </Button>
          </div>

          {jobs.isLoading ? (
            <Skeleton className="h-32" />
          ) : jobs.error ? (
            <div className="space-y-2">
              <ErrorNote message={`Could not load job orders: ${jobs.error.message}`} />
              <Button type="button" size="lg" variant="outline" onClick={() => void jobs.refetch()}>
                Try again
              </Button>
            </div>
          ) : runnable.length === 0 ? (
            <p className="text-3 rounded-xl border border-dashed px-3 py-6 text-center text-sm">
              No open job orders. Tap <strong>New job order</strong> to add one.
            </p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Job orders">
              {runnable.map((job: JobOrder) => {
                const current = job.job_number === tile.job_number;
                const selected = job.id === jobId;
                return (
                  <button
                    key={job.id}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={current}
                    onClick={() => setJobId(job.id)}
                    className={cn(
                      "touch rounded-xl border px-4 py-2 text-left transition-colors disabled:opacity-50",
                      selected
                        ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]"
                        : "surface hover:surface-2",
                    )}
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">#{job.job_number}</span>
                      {current ? <Badge variant="neutral">On this machine</Badge> : null}
                      {!current && job.status !== "planned" ? (
                        <Badge variant="neutral">{JOB_STATUS_LABEL[job.status] ?? job.status}</Badge>
                      ) : null}
                    </span>
                    <span className="text-2 block text-xs">
                      {[job.part_number, `${job.planned_quantity} pcs`, job.customer_name].filter(Boolean).join(" · ")}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </fieldset>

        <div className="max-w-sm">
          <Label htmlFor="change-job-operator">Operator (optional)</Label>
          {operators.error ? (
            <ErrorNote message={`Could not load operators: ${operators.error.message}`} />
          ) : (
            <Select
              id="change-job-operator"
              value={operatorId}
              disabled={operators.isLoading}
              onChange={(e) => setOperatorId(e.target.value)}
            >
              <option value="">{operators.isLoading ? "Loading…" : "Same as before"}</option>
              {activeOperators.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                  {o.employee_code ? ` (${o.employee_code})` : ""}
                </option>
              ))}
            </Select>
          )}
        </div>

        {!online ? (
          <p role="status" className="flex items-center gap-2 rounded-xl border border-dashed px-4 py-3 text-sm text-[var(--color-warn)]">
            <WifiOff className="size-4 shrink-0" />
            Needs a connection. Changing a job cannot be saved on the tablet for later — the counts
            in between would land on the wrong job.
          </p>
        ) : null}
        {change.error ? <ErrorNote message={change.error.message} /> : null}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="touch" disabled={!jobId || !online || change.isPending}>
            {change.isPending ? "Changing…" : "Change job"}
          </Button>
          <Button type="button" size="touch" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
