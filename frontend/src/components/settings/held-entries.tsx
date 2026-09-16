"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatClock, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import type { HoldStatus, SyncHold, SyncOpKind } from "@/types/api";
import { DataTable, ListState, Notice, RowButton, Toolbar } from "./shared";

/**
 * Entries that reached the server after their shift was locked.
 *
 * A tablet that was offline at closing time still has real stops and counts
 * on it. The lock is what makes the numbers trustworthy, so those entries are
 * not slipped in silently: they wait here. Apply writes one into the locked
 * shift as a logged correction; Discard drops it. Both need a note, because
 * both change what the morning meeting will see.
 */

const KIND_LABEL: Record<SyncOpKind, string> = {
  downtime_start: "Stop",
  downtime_close: "Restart",
  production: "Count",
  quality: "Quality batch",
};

const STATUSES: Array<{ key: HoldStatus; label: string }> = [
  { key: "held", label: "Waiting for review" },
  { key: "applied", label: "Applied" },
  { key: "discarded", label: "Discarded" },
];

function when(iso: string | null) {
  return iso ? `${formatDate(iso)}, ${formatClock(iso)}` : "—";
}

export function HeldEntriesTab() {
  const [status, setStatus] = useState<HoldStatus>("held");
  const [deciding, setDeciding] = useState<{ hold: SyncHold; verb: "apply" | "discard" } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const holds = useQuery({ queryKey: ["holds", status], queryFn: () => api.holds(status) });
  // Names for the ids inside a payload; a missing list just shows fewer words.
  const reasons = useQuery({ queryKey: ["reasons"], queryFn: api.reasonCodes });
  const parts = useQuery({ queryKey: ["parts"], queryFn: api.parts });

  const summary = (hold: SyncHold): string => {
    const p = hold.payload;
    const reason = reasons.data?.find((r) => r.id === p.reason_code_id)?.label;
    switch (hold.kind) {
      case "downtime_start":
        return [reason ?? (p.reason_code_id ? "Reason set" : "No reason"), p.note ? `“${String(p.note)}”` : null]
          .filter(Boolean)
          .join(" · ");
      case "downtime_close":
        return reason ? `Back to running · reason ${reason}` : "Back to running";
      case "production":
        return `${Number(p.count ?? 0)} parts`;
      case "quality": {
        const part = parts.data?.find((x) => x.id === p.part_id)?.part_number;
        const counts = `${Number(p.good_count ?? 0)} good, ${Number(p.reject_count ?? 0)} rejected`;
        return part ? `${part}: ${counts}` : counts;
      }
      default:
        return "—";
    }
  };

  const rows = holds.data ?? [];

  return (
    <div>
      <Toolbar
        title="Held entries"
        hint="Stops, counts and quality batches that reached the server after their shift was locked — usually a tablet that was offline at closing time. Apply one to write it into that shift as a logged correction, or discard it."
      >
        <div className="surface-2 flex gap-0.5 rounded-xl border p-1" role="group" aria-label="Show entries">
          {STATUSES.map((s) => (
            <button
              key={s.key}
              type="button"
              aria-pressed={status === s.key}
              onClick={() => setStatus(s.key)}
              className={cn(
                "min-h-11 rounded-lg px-3.5 text-[0.8125rem] font-medium whitespace-nowrap transition-colors",
                status === s.key ? "brand font-semibold" : "text-2 hover:text-[var(--text)]",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
      </Toolbar>
      {notice ? <Notice message={notice} onDismiss={() => setNotice(null)} /> : null}

      <ListState
        loading={holds.isLoading}
        error={holds.error}
        empty={rows.length === 0}
        emptyTitle={status === "held" ? "Nothing waiting for review" : `No ${status} entries`}
        emptyHint={
          status === "held"
            ? "Every entry reached the server before its shift was locked."
            : "Entries you apply or discard are listed here with the note given."
        }
        onRetry={() => void holds.refetch()}
      >
        <DataTable
          rows={rows}
          columns={[
            { header: "Kind", cell: (h) => <span className="font-semibold">{KIND_LABEL[h.kind] ?? h.kind}</span> },
            { header: "Station", cell: (h) => h.station_code ?? <span className="text-3">—</span> },
            {
              header: "Shift date",
              cell: (h) => <span className="tnum whitespace-nowrap">{h.business_date ?? "—"}</span>,
            },
            { header: "Happened", cell: (h) => <span className="tnum whitespace-nowrap">{when(h.occurred_at)}</span> },
            {
              header: "Submitted by",
              cell: (h) => (
                <span>
                  {h.submitted_by_name ?? "—"}
                  <span className="text-3 block text-xs">received {when(h.received_at)}</span>
                </span>
              ),
            },
            {
              header: "Entry",
              className: "min-w-56",
              cell: (h) => (
                <span>
                  {summary(h)}
                  {h.reason ? <span className="text-3 block text-xs">{h.reason}</span> : null}
                </span>
              ),
            },
            ...(status === "held"
              ? []
              : [
                  {
                    header: "Decision",
                    className: "min-w-56",
                    cell: (h: SyncHold) => (
                      <span>
                        <Badge variant={h.status === "applied" ? "recovered" : "neutral"}>
                          {h.status === "applied" ? "Applied" : "Discarded"}
                        </Badge>
                        {h.resolution_note ? <span className="mt-1 block">{h.resolution_note}</span> : null}
                        <span className="text-3 block text-xs">
                          {h.resolved_by_name ?? "—"} · {when(h.resolved_at)}
                        </span>
                      </span>
                    ),
                  },
                ]),
          ]}
          actions={
            status === "held"
              ? (h) => (
                  <>
                    <RowButton onClick={() => setDeciding({ hold: h, verb: "apply" })} aria-label={`Apply held ${KIND_LABEL[h.kind]}`}>
                      Apply
                    </RowButton>
                    <RowButton
                      onClick={() => setDeciding({ hold: h, verb: "discard" })}
                      className="text-[var(--color-loss)]"
                      aria-label={`Discard held ${KIND_LABEL[h.kind]}`}
                    >
                      Discard
                    </RowButton>
                  </>
                )
              : undefined
          }
        />
      </ListState>

      {deciding ? (
        <DecideSheet
          hold={deciding.hold}
          verb={deciding.verb}
          summary={summary(deciding.hold)}
          onClose={() => setDeciding(null)}
          onDone={(message) => {
            setDeciding(null);
            setNotice(message);
          }}
        />
      ) : null}
    </div>
  );
}

function DecideSheet({
  hold,
  verb,
  summary,
  onClose,
  onDone,
}: {
  hold: SyncHold;
  verb: "apply" | "discard";
  summary: string;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const decide = useMutation({
    mutationFn: () => (verb === "apply" ? api.applyHold(hold.id, note.trim()) : api.discardHold(hold.id, note.trim())),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["holds"] });
      if (verb === "apply") {
        // It is now part of a shift: the history, KPIs and reports read it.
        void queryClient.invalidateQueries({ queryKey: ["shift-entries"] });
        void queryClient.invalidateQueries({ queryKey: ["kpis"] });
      }
      onDone(`${KIND_LABEL[hold.kind]} ${verb === "apply" ? "applied to its shift" : "discarded"}.`);
    },
  });

  return (
    <Sheet
      title={verb === "apply" ? "Apply to the locked shift" : "Discard this entry"}
      subtitle={`${KIND_LABEL[hold.kind]}${hold.station_code ? ` · ${hold.station_code}` : ""} · ${summary}`}
      onClose={onClose}
    >
      <form
        className="space-y-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (note.trim().length < 3) return setInvalid("Write a short note (at least 3 characters) saying why.");
          setInvalid(null);
          decide.mutate();
        }}
      >
        <p className="text-2 text-sm leading-relaxed">
          {verb === "apply"
            ? `It is written into the ${hold.business_date ?? "locked"} shift as a correction, costed with that shift's rates, and recorded in the audit log with your note.`
            : "It is dropped and never counted. The entry and your note stay on record here."}
        </p>
        <div>
          <Label htmlFor="hold-note">Note</Label>
          <Textarea
            id="hold-note"
            rows={3}
            value={note}
            autoFocus
            onChange={(e) => setNote(e.target.value)}
            aria-invalid={Boolean(invalid)}
            placeholder={
              verb === "apply"
                ? "Tablet in bay 2 lost Wi-Fi before close; stop confirmed with the supervisor"
                : "Duplicate of the stop entered on the office PC"
            }
          />
        </div>
        {invalid ? <ErrorNote message={invalid} /> : null}
        {decide.error ? <ErrorNote message={decide.error.message} /> : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="touch" variant={verb === "discard" ? "stop" : "primary"} disabled={decide.isPending}>
            {decide.isPending ? "Saving…" : verb === "apply" ? "Apply entry" : "Discard entry"}
          </Button>
          <Button type="button" size="touch" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
