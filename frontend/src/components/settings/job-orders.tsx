"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Repeat } from "lucide-react";
import { api } from "@/lib/api";
import { Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import {
  JOB_STATUS_LABEL, NewJobOrderSheet, RepeatJobOrderSheet, isAssignable,
} from "@/components/job-orders";
import type { JobOrder, JobStatus } from "@/types/api";
import { DataTable, ListState, Notice, RecordForm, RowButton, Toolbar, toValues, type FieldSpec } from "./shared";

/**
 * Job orders, for the office. The floor creates them from Shift Start; this is
 * where a finished or abandoned job is closed off so it stops appearing in the
 * station pickers.
 */

/** What the office may set. `running` comes from the floor and is shown, not offered. */
const STATUSES: Array<Exclude<JobStatus, "running">> = ["planned", "paused", "completed", "cancelled"];

const EDIT_FIELDS: FieldSpec[] = [
  { key: "planned_quantity", label: "Planned quantity", kind: "number", required: true, integer: true, min: 1 },
  { key: "due_date", label: "Due date", kind: "date" },
  { key: "notes", label: "Notes", kind: "text", wide: true },
];

export function JobOrdersTab() {
  const queryClient = useQueryClient();
  const [showClosed, setShowClosed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [repeating, setRepeating] = useState<JobOrder | null>(null);
  const [editing, setEditing] = useState<JobOrder | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Under the shared "jobs" key, so a job created on Shift Start shows up here too.
  const jobs = useQuery({
    queryKey: ["jobs", showClosed ? "all" : "open"],
    queryFn: () => api.jobOrderList(showClosed),
  });

  const update = useMutation({
    mutationFn: ({ job, payload }: { job: JobOrder; payload: Parameters<typeof api.updateJobOrder>[1] }) =>
      api.updateJobOrder(job.id, payload),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setEditing(null);
      setNotice(`Job #${saved.job_number} saved.`);
    },
    // The edit sheet shows its own error; a status change from the table needs one here.
    onError: (e: Error, { job }) => {
      if (!editing) setNotice(`Could not update job #${job.job_number}: ${e.message}`);
    },
  });

  const rows = (jobs.data ?? []).filter((job) => showClosed || isAssignable(job));

  return (
    <div>
      <Toolbar
        title="Job orders"
        hint="Mark a job completed or cancelled and it leaves the station pickers. Nothing recorded against it changes."
        showInactive={showClosed}
        onShowInactive={setShowClosed}
        inactiveLabel="Show completed and cancelled"
        addLabel="New job order"
        onAdd={() => setCreating(true)}
      />
      {notice ? <Notice message={notice} onDismiss={() => setNotice(null)} /> : null}

      <ListState
        loading={jobs.isLoading}
        error={jobs.error}
        empty={rows.length === 0}
        emptyTitle="No open job orders"
        emptyHint="Create one here or from the Shift screen."
        onRetry={() => void jobs.refetch()}
      >
        <DataTable
          rows={rows}
          inactive={(job) => !isAssignable(job)}
          columns={[
            { header: "Job", cell: (j) => <span className="font-semibold">#{j.job_number}</span> },
            { header: "Part", cell: (j) => j.part_number ?? <span className="text-3">—</span> },
            { header: "Customer", cell: (j) => j.customer_name ?? <span className="text-3">—</span> },
            { header: "Qty", cell: (j) => <span className="tnum">{j.planned_quantity.toLocaleString("en-IN")}</span> },
            { header: "Due", cell: (j) => <span className="tnum whitespace-nowrap">{j.due_date ?? "—"}</span> },
            {
              header: "Status",
              cell: (j) => (
                <Select
                  aria-label={`Status of job ${j.job_number}`}
                  value={j.status}
                  disabled={update.isPending}
                  onChange={(e) =>
                    update.mutate({
                      job: j,
                      payload: { status: e.target.value as Exclude<JobStatus, "running"> },
                    })
                  }
                  className="min-w-36"
                >
                  {STATUSES.map((s) => (
                    <option key={s} value={s}>{JOB_STATUS_LABEL[s]}</option>
                  ))}
                  {!(STATUSES as string[]).includes(j.status) ? (
                    <option value={j.status} disabled>
                      {JOB_STATUS_LABEL[j.status] ?? j.status}
                    </option>
                  ) : null}
                </Select>
              ),
            },
          ]}
          actions={(j) => (
            <>
              <RowButton
                onClick={() => {
                  update.reset();
                  setEditing(j);
                }}
                aria-label={`Edit job ${j.job_number}`}
              >
                Edit
              </RowButton>
              <RowButton onClick={() => setRepeating(j)} aria-label={`Repeat job ${j.job_number}`}>
                <Repeat className="size-3.5" />
                Repeat
              </RowButton>
            </>
          )}
        />
      </ListState>

      {creating ? (
        <NewJobOrderSheet
          onClose={() => setCreating(false)}
          onCreated={(job) => setNotice(`Job #${job.job_number} created.`)}
        />
      ) : null}
      {repeating ? (
        <RepeatJobOrderSheet
          job={repeating}
          onClose={() => setRepeating(null)}
          onCreated={(job) => setNotice(`Job #${job.job_number} created from #${repeating.job_number}.`)}
        />
      ) : null}
      {editing ? (
        <Sheet title={`Edit job #${editing.job_number}`} onClose={() => setEditing(null)}>
          <RecordForm
            idPrefix="job-edit"
            fields={EDIT_FIELDS}
            initial={toValues(editing, EDIT_FIELDS)}
            submitLabel="Save changes"
            busy={update.isPending}
            serverError={update.error?.message}
            onSubmit={(payload) => update.mutate({ job: editing, payload })}
            onCancel={() => setEditing(null)}
          />
        </Sheet>
      ) : null}
    </div>
  );
}
