"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Lock, LockOpen } from "lucide-react";
import { api } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";

/**
 * Shift history — the last sixty shifts, newest first.
 *
 * The way in to what was recorded on a past shift and, where the rules allow,
 * to correcting it: a supervisor while the shift is still open, the owner at
 * any time, always with a reason on the audit trail.
 */
export default function ShiftHistory() {
  const history = useQuery({ queryKey: ["shift-history"], queryFn: () => api.shiftHistory() });
  // Sessions carry the shift's id, not its name.
  const shifts = useQuery({ queryKey: ["shifts"], queryFn: api.shifts });
  const nameOf = (id: string) => shifts.data?.find((s) => s.id === id)?.name ?? "Shift";

  const rows = history.data ?? [];

  return (
    <div className="space-y-6">
      <header className="rise">
        <p className="label mb-1.5">Shop floor</p>
        <h1 className="display text-[1.75rem] font-bold">Shift history</h1>
        <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
          Every stop, count and quality batch, shift by shift. Open one to check what was
          recorded or to correct a mistake — each correction asks why and is kept on record.
        </p>
      </header>

      {history.isLoading ? (
        <Skeleton className="h-80" />
      ) : history.error ? (
        <div className="space-y-3">
          <ErrorNote message={`Could not load shift history: ${history.error.message}`} />
          <Button size="lg" variant="outline" onClick={() => void history.refetch()}>
            Try again
          </Button>
        </div>
      ) : rows.length === 0 ? (
        <Empty title="No shifts yet" hint="Shifts appear here once the first one is started on the Shift screen." />
      ) : (
        <ul className="surface elev-1 divide-y rounded-[var(--radius-card)] border">
          {rows.map((session) => {
            const locked = Boolean(session.locked_at);
            return (
              <li key={session.id}>
                <Link
                  href={`/shifts/${session.id}`}
                  className="flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 transition-colors hover:surface-2"
                >
                  <span className="tnum w-28 shrink-0 font-semibold">
                    {new Date(session.business_date).toLocaleDateString("en-IN", {
                      weekday: "short",
                      day: "numeric",
                      month: "short",
                    })}
                  </span>
                  <span className="min-w-24 flex-1">
                    <span className="block font-medium">{nameOf(session.shift_id)}</span>
                    <span className="text-3 block text-xs">
                      {formatClock(session.started_at)}–{formatClock(session.ended_at)}
                    </span>
                  </span>
                  <Badge variant={session.status === "open" ? "running" : "neutral"} className="capitalize">
                    {session.status.replace(/_/g, " ")}
                  </Badge>
                  <span className="text-2 flex w-28 items-center gap-1.5 text-xs">
                    {locked ? <Lock className="size-3.5" /> : <LockOpen className="size-3.5" />}
                    {locked ? "Locked" : "Not locked"}
                  </span>
                  <ChevronRight className="text-3 size-4" aria-hidden />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
