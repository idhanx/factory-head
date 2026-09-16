"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BellRing, MessageCircle, MessageSquareText } from "lucide-react";
import { api } from "@/lib/api";
import { formatClock, formatDate } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { ErrorNote, Skeleton } from "@/components/ui/misc";
import type { ChannelState, NotificationChannels, NotificationLog } from "@/types/api";
import { DataTable, ListState, Toolbar } from "./shared";

/**
 * Who was told what, and whether it arrived.
 *
 * An escalation that never reached the maintenance head is a stop that ran an
 * hour longer than it had to. This is where the owner checks that the
 * channels are set up and reads the log when someone says "I never got it".
 */

const CHANNELS: Array<{
  key: keyof NotificationChannels;
  label: string;
  provider: string;
  Icon: React.ComponentType<{ className?: string }>;
}> = [
  { key: "whatsapp", label: "WhatsApp", provider: "via WATI", Icon: MessageCircle },
  { key: "sms", label: "SMS", provider: "fallback, via MSG91", Icon: MessageSquareText },
  { key: "push", label: "Browser push", provider: "office PCs and phones", Icon: BellRing },
];

const KINDS: Array<{ value: string; label: string }> = [
  { value: "escalation", label: "Escalation" },
  { value: "shift_report", label: "Shift report" },
  { value: "daily_report", label: "Daily report" },
  { value: "coach_briefing", label: "Coach briefing" },
  { value: "rca_assignment", label: "RCA assignment" },
  { value: "reconciliation", label: "Count mismatch" },
];

const STATUSES: Array<{ value: string; label: string }> = [
  { value: "pending", label: "Pending" },
  { value: "sent", label: "Sent" },
  { value: "failed", label: "Failed" },
  { value: "skipped", label: "Skipped" },
];

const STATUS_BADGE: Record<string, "recovered" | "warn" | "stopped" | "neutral"> = {
  sent: "recovered",
  pending: "warn",
  failed: "stopped",
  skipped: "neutral",
};

function channelNote(state: ChannelState): { badge: "recovered" | "warn" | "neutral"; text: string } {
  if (state.enabled && state.configured) return { badge: "recovered", text: "Working" };
  if (!state.configured) return { badge: "warn", text: "No credentials yet" };
  return { badge: "neutral", text: "Switched off" };
}

export function NotificationsTab() {
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");

  const channels = useQuery({ queryKey: ["notifications", "channels"], queryFn: api.notificationChannels });
  const log = useQuery({
    queryKey: ["notifications", "log", kind, status],
    queryFn: () => api.notifications({ kind: kind || undefined, status: status || undefined, limit: 100 }),
  });

  const rows = log.data ?? [];
  const label = (list: Array<{ value: string; label: string }>, value: string) =>
    list.find((item) => item.value === value)?.label ?? value.replace(/_/g, " ");

  return (
    <div className="space-y-6">
      <Toolbar
        title="Notifications"
        hint="Escalations and reports go out on WhatsApp first, SMS if that fails, and browser push to anyone who turned it on with the bell in the header."
      />

      <section aria-label="Delivery channels">
        {channels.isLoading ? (
          <Skeleton className="h-28" />
        ) : channels.error ? (
          <div className="space-y-3">
            <ErrorNote message={`Could not load the channel status: ${channels.error.message}`} />
            <Button size="lg" variant="outline" onClick={() => void channels.refetch()}>
              Try again
            </Button>
          </div>
        ) : channels.data ? (
          <div className="grid gap-3 sm:grid-cols-3">
            {CHANNELS.map(({ key, label: name, provider, Icon }) => {
              const state = channels.data[key];
              const note = state ? channelNote(state) : { badge: "neutral" as const, text: "Unknown" };
              return (
                <Card key={key} elevation={1}>
                  <CardHeader className="justify-between">
                    <CardTitle className="flex items-center gap-1.5">
                      <Icon className="size-3.5" />
                      {name}
                    </CardTitle>
                    <Badge variant={note.badge}>{note.text}</Badge>
                  </CardHeader>
                  <CardContent>
                    <p className="text-3 text-xs">{provider}</p>
                    {state && !state.configured ? (
                      <p className="text-2 mt-2 text-xs leading-relaxed">
                        Messages are logged below as skipped until the provider&apos;s credentials are added on the
                        server.
                      </p>
                    ) : null}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        ) : null}
      </section>

      <section aria-labelledby="delivery-log">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <h3 id="delivery-log" className="display text-base font-bold">
            Delivery log
          </h3>
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <Label htmlFor="notif-kind">Kind</Label>
              <Select id="notif-kind" value={kind} onChange={(e) => setKind(e.target.value)} className="min-w-44">
                <option value="">All kinds</option>
                {KINDS.map((k) => (
                  <option key={k.value} value={k.value}>{k.label}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="notif-status">Status</Label>
              <Select id="notif-status" value={status} onChange={(e) => setStatus(e.target.value)} className="min-w-36">
                <option value="">All</option>
                {STATUSES.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </Select>
            </div>
          </div>
        </div>

        <ListState
          loading={log.isLoading}
          error={log.error}
          empty={rows.length === 0}
          emptyTitle={kind || status ? "Nothing matches these filters" : "Nothing sent yet"}
          emptyHint={kind || status ? "Clear a filter to see more." : "Escalations and reports appear here as they go out."}
          onRetry={() => void log.refetch()}
        >
          <DataTable<NotificationLog>
            rows={rows}
            columns={[
              {
                header: "When",
                cell: (n) => (
                  <span className="tnum whitespace-nowrap">
                    {formatDate(n.created_at)}, {formatClock(n.created_at)}
                    {n.sent_at ? <span className="text-3 block text-xs">sent {formatClock(n.sent_at)}</span> : null}
                  </span>
                ),
              },
              { header: "Kind", cell: (n) => <span className="whitespace-nowrap">{label(KINDS, n.kind)}</span> },
              { header: "Channel", cell: (n) => <span className="capitalize">{n.channel.replace(/_/g, " ")}</span> },
              {
                header: "To",
                cell: (n) => (
                  <span>
                    {n.recipient_name ?? "—"}
                    {n.recipient_masked ? <span className="text-3 tnum block text-xs">{n.recipient_masked}</span> : null}
                  </span>
                ),
              },
              {
                header: "Status",
                cell: (n) => (
                  <span className="whitespace-nowrap">
                    <Badge variant={STATUS_BADGE[n.status] ?? "neutral"}>{label(STATUSES, n.status)}</Badge>
                    {n.attempts > 1 ? <span className="text-3 ml-1.5 text-xs">{n.attempts} tries</span> : null}
                  </span>
                ),
              },
              {
                header: "Message",
                className: "min-w-64 max-w-md",
                cell: (n) => (
                  <span>
                    {n.last_error ? <span className="block text-xs text-[var(--color-loss)]">{n.last_error}</span> : null}
                    {n.body ? (
                      <details>
                        <summary className="text-2 flex min-h-11 cursor-pointer items-center text-xs">
                          <span className="line-clamp-1">{n.body}</span>
                        </summary>
                        <p className="text-2 pb-2 text-xs leading-relaxed whitespace-pre-wrap">{n.body}</p>
                      </details>
                    ) : (
                      <span className="text-3">—</span>
                    )}
                  </span>
                ),
              },
            ]}
          />
        </ListState>
      </section>
    </div>
  );
}
