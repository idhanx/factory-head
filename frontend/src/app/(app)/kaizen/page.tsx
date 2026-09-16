"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Check, Lightbulb, Send, Trophy, X } from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { can } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import type { KaizenIdea } from "@/types/api";

/**
 * Kaizen AI (§3 Layer 5).
 *
 *   "Operators submit improvement ideas; the system converts each into a
 *    structured cost-saving proposal, scores it, routes it into the Actions
 *    engine for implementation, and tracks the realised saving through the same
 *    Recovered-Profit accounting the core product already uses."
 *
 * The score comes back the moment the idea is submitted, with its reasoning in
 * plain words. That immediacy matters more than the number: a suggestion scheme
 * dies the first time an idea disappears into a box and nobody answers.
 */

const FILTERS = [
  { key: "open", label: "Awaiting decision" },
  { key: "accepted", label: "Accepted" },
  { key: "rejected", label: "Declined" },
  { key: "all", label: "All" },
] as const;

export default function Kaizen() {
  const queryClient = useQueryClient();
  const session = loadSession();
  const canDecide = session ? can(session.roles, "owner", "supervisor", "consultant") : false;

  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("open");
  const ideas = useQuery({ queryKey: ["kaizen"], queryFn: api.kaizenIdeas });
  const board = useQuery({ queryKey: ["kaizen-board"], queryFn: api.kaizenLeaderboard });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["kaizen"] });
    void queryClient.invalidateQueries({ queryKey: ["kaizen-board"] });
  };

  const all = ideas.data ?? [];
  const visible = all.filter((idea) =>
    filter === "all"
      ? true
      : filter === "open"
        ? idea.status === "scored" || idea.status === "submitted"
        : idea.status === filter,
  );
  const acceptedSaving = all
    .filter((i) => i.status === "accepted" || i.status === "implemented")
    .reduce((sum, i) => sum + i.estimated_annual_saving_paise, 0);

  return (
    <div className="space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5 flex items-center gap-1.5">
            <Lightbulb className="size-3" />
            Layer 5 · Kaizen AI
          </p>
          <h1 className="display text-[1.75rem] font-bold">Improvement ideas</h1>
          <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
            Anyone can suggest a fix. Each idea is priced against this plant&apos;s own
            losses and answered straight away — and accepted ideas become Actions, so
            their savings are measured by the same rules as everything else.
          </p>
        </div>
        <div>
          <p className="label mb-1">Accepted, estimated per year</p>
          <Money
            value={`₹${Math.round(acceptedSaving / 100).toLocaleString("en-IN")}`}
            tone="recovered"
            size="lg"
          />
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <div className="space-y-4">
          <SubmitCard defaultName={session?.full_name ?? ""} onSubmitted={refresh} />
          <Leaderboard rows={board.data ?? []} loading={board.isLoading} />
        </div>

        <section className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="surface-2 flex gap-0.5 rounded-xl border p-1">
              {FILTERS.map((f) => {
                const count =
                  f.key === "all"
                    ? all.length
                    : f.key === "open"
                      ? all.filter((i) => i.status === "scored" || i.status === "submitted").length
                      : all.filter((i) => i.status === f.key).length;
                return (
                  <button
                    key={f.key}
                    type="button"
                    onClick={() => setFilter(f.key)}
                    className={cn(
                      "rounded-lg px-3 py-1.5 text-[0.8125rem] font-medium transition-colors",
                      filter === f.key
                        ? "surface text-[var(--text)] shadow-[var(--shadow-1)]"
                        : "text-2 hover:text-[var(--text)]",
                    )}
                  >
                    {f.label}
                    <span className="text-3 tnum ml-1.5 text-xs">{count}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {ideas.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-44" />
              ))}
            </div>
          ) : visible.length === 0 ? (
            <Empty
              icon={<Lightbulb className="size-6" />}
              title={filter === "open" ? "Nothing waiting for a decision" : "No ideas here yet"}
              hint="Ideas are scored the moment they are submitted, so there is never a queue of unanswered suggestions."
            />
          ) : (
            <div className="space-y-3">
              {visible.map((idea, index) => (
                <IdeaCard
                  key={idea.id}
                  idea={idea}
                  index={index}
                  canDecide={canDecide}
                  onDecided={refresh}
                />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function SubmitCard({
  defaultName,
  onSubmitted,
}: {
  defaultName: string;
  onSubmitted: () => void;
}) {
  const stations = useQuery({ queryKey: ["stations"], queryFn: api.stations });
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [stationId, setStationId] = useState("");
  const [name, setName] = useState(defaultName);
  const [result, setResult] = useState<KaizenIdea | null>(null);

  const submit = useMutation({
    mutationFn: () =>
      api.submitKaizen({
        title: title.trim(),
        description: description.trim(),
        submitted_by_name: name.trim() || undefined,
        station_id: stationId || null,
      }),
    onSuccess: (idea) => {
      setResult(idea);
      setTitle("");
      setDescription("");
      setStationId("");
      onSubmitted();
    },
  });

  return (
    <Card className="rise">
      <CardHeader>
        <CardTitle>Suggest an improvement</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3.5"
          onSubmit={(e) => {
            e.preventDefault();
            submit.mutate();
          }}
        >
          <div>
            <Label htmlFor="kz-title">What would you change?</Label>
            <Input
              id="kz-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Keep spare inserts at the machine"
              required
              minLength={3}
            />
          </div>
          <div>
            <Label htmlFor="kz-desc">Why — what goes wrong today?</Label>
            <Textarea
              id="kz-desc"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="When the insert breaks we walk to stores for a new one…"
              required
              minLength={10}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="kz-station">Machine (sharpens the estimate)</Label>
              <Select
                id="kz-station"
                value={stationId}
                onChange={(e) => setStationId(e.target.value)}
              >
                <option value="">Not one machine</option>
                {(stations.data ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.code}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="kz-name">Your name</Label>
              <Input id="kz-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
          </div>

          {submit.error ? <ErrorNote message={(submit.error as Error).message} /> : null}

          <Button type="submit" className="w-full" size="lg" disabled={submit.isPending}>
            <Send className="size-4" />
            {submit.isPending ? "Scoring…" : "Submit idea"}
          </Button>
        </form>

        {result ? (
          <div className="rise surface-2 mt-4 rounded-xl border p-4" role="status">
            <p className="label mb-1">{result.reference} · scored</p>
            <div className="flex items-baseline justify-between gap-3">
              <p className="font-semibold">{result.title}</p>
              <Money
                value={result.estimated_annual_saving_display}
                tone={result.estimated_annual_saving_paise > 0 ? "recovered" : "structural"}
                size="md"
              />
            </div>
            <p className="text-2 mt-2 text-xs leading-relaxed">{result.score_rationale}</p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function IdeaCard({
  idea,
  index,
  canDecide,
  onDecided,
}: {
  idea: KaizenIdea;
  index: number;
  canDecide: boolean;
  onDecided: () => void;
}) {
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const open = idea.status === "scored" || idea.status === "submitted";

  const decide = useMutation({
    mutationFn: (accept: boolean) =>
      api.decideKaizen(idea.id, accept ? { accept: true } : { accept: false, note: note.trim() }),
    onSuccess: () => {
      setDeclining(false);
      onDecided();
    },
  });

  const statusBadge = {
    submitted: <Badge variant="neutral">Submitted</Badge>,
    scored: <Badge variant="brand">Awaiting decision</Badge>,
    accepted: <Badge variant="recovered">Accepted</Badge>,
    implemented: <Badge variant="recovered">Implemented</Badge>,
    rejected: <Badge variant="neutral">Declined</Badge>,
  }[idea.status];

  return (
    <Card className="rise" style={{ animationDelay: `${Math.min(index * 45, 300)}ms` }}>
      <CardContent className="pt-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className="text-3 tnum text-xs font-semibold">{idea.reference}</span>
              {statusBadge}
              {idea.station_code ? <Badge variant="neutral">{idea.station_code}</Badge> : null}
            </div>
            <h3 className="text-[1.0625rem] font-bold leading-snug">{idea.title}</h3>
            <p className="text-3 mt-0.5 text-xs">
              {idea.submitted_by_name} ·{" "}
              {new Date(idea.created_at).toLocaleDateString("en-IN", {
                day: "numeric",
                month: "short",
              })}
            </p>
          </div>
          <div className="text-right">
            <p className="label mb-0.5">Est. per year</p>
            <Money
              value={idea.estimated_annual_saving_display}
              tone={idea.estimated_annual_saving_paise > 0 ? "recovered" : "structural"}
              size="lg"
            />
            <ImpactScale value={idea.impact_score} />
          </div>
        </div>

        <p className="text-2 mt-3 text-sm leading-relaxed">{idea.description}</p>

        {idea.score_rationale ? (
          <div className="surface-2 mt-3 rounded-xl border px-3.5 py-2.5">
            <p className="label mb-0.5">How it was priced</p>
            <p className="text-2 text-xs leading-relaxed">{idea.score_rationale}</p>
          </div>
        ) : null}

        {idea.decision_note ? (
          <p className="text-2 mt-3 text-xs leading-relaxed">
            <span className="font-semibold text-[var(--text)]">Decision:</span> {idea.decision_note}
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {idea.action_id ? (
            <Link
              href={`/actions/${idea.action_id}`}
              className="flex items-center gap-1 text-sm font-medium text-[var(--color-accent)] hover:underline"
            >
              Tracked as an Action
              <ArrowUpRight className="size-3.5" />
            </Link>
          ) : null}

          {open && canDecide && !declining ? (
            <>
              <Button size="sm" variant="go" onClick={() => decide.mutate(true)} disabled={decide.isPending}>
                <Check className="size-3.5" />
                Accept into Actions
              </Button>
              <Button size="sm" variant="outline" onClick={() => setDeclining(true)}>
                <X className="size-3.5" />
                Decline
              </Button>
            </>
          ) : null}
        </div>

        {declining ? (
          <div className="surface-2 mt-3 rounded-xl border p-3.5">
            {/* Required, not optional: an idea declined without a reason the
                operator can read is how a suggestion scheme dies. */}
            <Label htmlFor={`note-${idea.id}`}>Tell {idea.submitted_by_name} why</Label>
            <Textarea
              id={`note-${idea.id}`}
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Good thought, but the fixture change is already planned for next month."
              autoFocus
            />
            {decide.error ? (
              <div className="mt-2">
                <ErrorNote message={(decide.error as Error).message} />
              </div>
            ) : null}
            <div className="mt-2.5 flex gap-2">
              <Button
                size="sm"
                disabled={!note.trim() || decide.isPending}
                onClick={() => decide.mutate(false)}
              >
                Decline with reason
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDeclining(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Impact 1–5, as filled pips with the number beside them — never colour alone. */
function ImpactScale({ value }: { value: number }) {
  return (
    <span className="mt-1.5 flex items-center justify-end gap-1" aria-label={`Impact ${value} of 5`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span
          key={n}
          className={cn(
            "h-1.5 w-3 rounded-full",
            n <= value ? "bg-[var(--color-brand)]" : "surface-inset",
          )}
        />
      ))}
      <span className="text-3 tnum ml-1 text-[0.6875rem]">{value}/5</span>
    </span>
  );
}

function Leaderboard({
  rows,
  loading,
}: {
  rows: Array<{
    name: string;
    ideas: number;
    accepted: number;
    estimated_annual_saving_paise: number;
    estimated_annual_saving_display: string;
  }>;
  loading: boolean;
}) {
  // Ideas first, then how many were accepted, then what they are worth — so a
  // tie is broken by substance, and the highlighted first place is earned
  // rather than whoever happens to sort first alphabetically.
  const sorted = useMemo(
    () =>
      [...rows].sort(
        (a, b) =>
          b.ideas - a.ideas ||
          b.accepted - a.accepted ||
          b.estimated_annual_saving_paise - a.estimated_annual_saving_paise,
      ),
    [rows],
  );

  return (
    <Card className="rise" style={{ animationDelay: "80ms" }}>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Trophy className="size-3.5" />
          Contributors
        </CardTitle>
      </CardHeader>
      <CardContent>
        {/* Recognition, not ranking people against each other on problems —
            §10's no-blame rule is about reports on what went wrong. Credit for
            an improvement someone proposed is the opposite of blame. */}
        {loading ? (
          <Skeleton className="h-32" />
        ) : sorted.length === 0 ? (
          <p className="text-3 py-6 text-center text-sm">No ideas submitted yet.</p>
        ) : (
          <ol className="divide-y">
            {sorted.slice(0, 8).map((row, index) => (
              <li key={row.name} className="flex items-center gap-3 py-2.5">
                <span
                  className={cn(
                    "tnum grid size-6 shrink-0 place-items-center rounded-full text-[0.6875rem] font-bold",
                    index === 0 ? "brand" : "surface-2 text-2",
                  )}
                >
                  {index + 1}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{row.name}</span>
                  <span className="text-3 block text-xs">
                    {row.ideas} idea{row.ideas === 1 ? "" : "s"} · {row.accepted} accepted
                  </span>
                </span>
                <span className="money text-xs font-semibold">{row.estimated_annual_saving_display}</span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
