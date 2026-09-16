"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "@/lib/api";
import { Money } from "@/components/ui/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import { AuthedImage } from "@/components/authed-image";
import { cn } from "@/lib/utils";
import type { Action, ActionState } from "@/types/api";

/** The only moves a person makes by hand, in the words used on the floor. */
const TRANSITIONS: Partial<Record<ActionState, { label: string; hint: string }>> = {
  root_cause: { label: "Find root cause", hint: "The team is working out why it happened." },
  planned: { label: "Countermeasure planned", hint: "The fix is agreed, not yet done." },
  in_progress: { label: "Start work", hint: "The fix is being carried out." },
  open: { label: "Reopen", hint: "Put it back on the board as open." },
  cancelled: { label: "Cancel action", hint: "Not worth pursuing. It leaves the board." },
};

/**
 * Screen 10 — RCA and the closure prompt.
 *
 * Two spec rules drive the whole layout:
 *
 *  §5.5 "Reason codes are not root causes." The reason ('Machine Breakdown')
 *  was logged in 20 seconds during the shift and says WHAT. The root cause
 *  ('bearing seized — wrong grease grade at last PM') says WHY, and is
 *  established here, at Action level, never on the shopfloor mid-shift.
 *
 *  §3 Layer 4 — closure asks four questions, once, and they become Factory
 *  Memory. Sixty seconds from the person who actually fixed it.
 */
export default function ActionDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);

  const detail = useQuery({
    queryKey: ["action", id],
    queryFn: () => api.action(id),
    // Not found is an answer, not a blip worth retrying.
    retry: (count, e) => !(e instanceof ApiError && e.status === 404) && count < 1,
  });
  const rca = useQuery({
    queryKey: ["rca", id],
    queryFn: () => api.rca(id),
    // Only actions that cleared the severity bar have an RCA; a 404 here is
    // the expected answer for the rest, not a failure.
    retry: false,
  });

  const action = detail.data;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["action", id] });
    void queryClient.invalidateQueries({ queryKey: ["actions"] });
    void queryClient.invalidateQueries({ queryKey: ["meeting"] });
  };

  const close = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.closeAction(id, payload),
    onSuccess: () => {
      setError(null);
      refresh();
    },
    // No onError: `close.error` renders inside the form, beside its button.
    // The 409s are the rules talking ("Record the root cause…") — shown as written.
  });

  const move = useMutation({
    mutationFn: (state: ActionState) => api.updateAction(id, { state }),
    onSuccess: () => {
      setError(null);
      setConfirmCancel(false);
      refresh();
    },
    onError: (e: Error) => {
      setConfirmCancel(false);
      setError(e.message);
    },
  });

  if (detail.isLoading) return <Skeleton className="h-96" />;
  if (detail.error) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="space-y-4">
        <Link href="/actions" className="text-sm text-2 hover:underline">
          ← Actions
        </Link>
        {notFound ? (
          <Empty
            title="Action not found"
            hint="It may belong to another plant, or the link is wrong."
          />
        ) : (
          <ErrorNote message={`Could not load this action: ${detail.error.message}`} />
        )}
      </div>
    );
  }
  if (!action) return null;

  const transitions = (action.allowed_transitions ?? []).filter((state) => TRANSITIONS[state]);
  // The server decides; the state list is only a fallback for an older API.
  const canClose =
    action.can_close ?? ["open", "root_cause", "planned", "in_progress", "reopened"].includes(action.state);

  return (
    <div className="space-y-6">
      <div>
        <Link href="/actions" className="text-sm text-2 hover:underline">
          ← Actions
        </Link>
      </div>

      <header className="rise surface elev-2 relative overflow-hidden rounded-[var(--radius-card)] border p-6">
        <span
          aria-hidden
          className="absolute inset-x-0 top-0 h-[3px]"
          style={{
            background: `linear-gradient(90deg, ${
              action.state === "verified"
                ? "var(--color-recovered)"
                : action.state === "monitoring"
                  ? "var(--color-accent)"
                  : action.state === "cancelled"
                    ? "var(--color-structural)"
                    : "var(--color-loss)"
            }, transparent)`,
          }}
        />
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="text-3 tnum text-xs font-semibold">{action.reference}</span>
              {/* The lifecycle IS the PDCA cycle (§5.4), so the phase leads. */}
              <Badge variant="accent">{action.pdca_phase}</Badge>
              <Badge variant="neutral">{action.state.replace(/_/g, " ")}</Badge>
              {action.routed_to_rca ? <Badge variant="warn">Routed to RCA</Badge> : null}
              {action.is_overdue ? <Badge variant="stopped">Overdue</Badge> : null}
            </div>
            <h1 className="display text-[1.75rem] font-bold leading-tight">{action.title}</h1>
            {action.description ? (
              <p className="text-2 mt-1.5 text-sm leading-relaxed">{action.description}</p>
            ) : null}
          </div>
          <div className="shrink-0 text-right">
            <p className="label mb-1">Attributed loss</p>
            <Money value={action.linked_cost_display} tone="loss" size="xl" />
            <p className="text-3 mt-1 text-xs">
              across {action.occurrence_count} event{action.occurrence_count === 1 ? "" : "s"}
            </p>
          </div>
        </div>
      </header>

      {error ? <ErrorNote message={error} /> : null}

      {transitions.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Move this action</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {transitions.map((state) => (
                <Button
                  key={state}
                  size="lg"
                  variant={state === "cancelled" ? "ghost" : "outline"}
                  className={cn(state === "cancelled" && "border text-[var(--color-loss)]")}
                  disabled={move.isPending}
                  title={TRANSITIONS[state]?.hint}
                  onClick={() => (state === "cancelled" ? setConfirmCancel(true) : move.mutate(state))}
                >
                  {TRANSITIONS[state]?.label}
                </Button>
              ))}
            </div>
            {confirmCancel ? (
              <div
                role="alertdialog"
                aria-label="Confirm cancel"
                className="surface-2 rise rounded-xl border p-4"
              >
                <p className="text-sm font-semibold">Cancel {action.reference}?</p>
                <p className="text-2 mt-1 text-xs leading-relaxed">
                  It leaves the board and no savings will ever be credited to it. The events it
                  was raised from stay logged and costed.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    size="lg"
                    variant="stop"
                    disabled={move.isPending}
                    onClick={() => move.mutate("cancelled")}
                  >
                    {move.isPending ? "Cancelling…" : "Yes, cancel it"}
                  </Button>
                  <Button size="lg" variant="outline" onClick={() => setConfirmCancel(false)}>
                    Keep it
                  </Button>
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {action.severity_reasons.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Why this became an action</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 text-sm">
              {action.severity_reasons.map((reason) => (
                <li key={reason} className="flex gap-2">
                  <span className="text-2">·</span>
                  {reason}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {action.routed_to_rca && rca.data ? <FiveWhys id={id} rca={rca.data} /> : null}

          {canClose ? (
            <ClosureForm
              actionId={id}
              busy={close.isPending}
              error={close.error ? close.error.message : null}
              needsRootCause={!action.root_cause}
              onSubmit={(payload) => close.mutate(payload)}
            />
          ) : (
            <ClosedSummary action={action} />
          )}
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Recovered profit</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div>
                <p className="text-xs text-2">Baseline (frozen at creation)</p>
                <Money
                  value={`₹${(action.baseline_weekly_loss_paise / 100).toLocaleString("en-IN")}`}
                  size="md"
                  className="font-semibold"
                />
                <span className="ml-1 text-xs text-2">/week</span>
              </div>
              <div>
                <p className="text-xs text-2">Credited so far</p>
                <Money value={action.recovered_profit_display} tone="recovered" size="lg" />
              </div>
              {/* Two sustained weeks is the bar (§5.6). Showing it as progress
                  makes the conservatism visible rather than merely asserted. */}
              <div>
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="label">Sustain</span>
                  <span className="text-3 tnum text-xs">
                    {Math.min(action.sustained_weeks, 2)} / 2 weeks
                  </span>
                </div>
                <div className="flex gap-1.5">
                  {[0, 1].map((week) => (
                    <div
                      key={week}
                      className={
                        action.sustained_weeks > week
                          ? "h-1.5 flex-1 rounded-full bg-[var(--color-recovered)]"
                          : "surface-inset h-1.5 flex-1 rounded-full"
                      }
                    />
                  ))}
                </div>
              </div>
              <p className="text-3 text-xs leading-relaxed">
                {action.sustained_weeks >= 2
                  ? `Verified — the fix has held for ${action.sustained_weeks} weeks, so the savings are credited.`
                  : "Nothing is credited until the fix has held for two consecutive weeks. A fix that lasted one week is not a fix."}
              </p>
            </CardContent>
          </Card>

          {(action.photos ?? []).length ? (
            <Card>
              <CardHeader><CardTitle>Photos</CardTitle></CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-2">
                  {action.photos.map((photo, index) => (
                    <AuthedImage
                      key={photo.id}
                      url={photo.url}
                      alt={`Photo ${index + 1} for ${action.reference}`}
                      className="aspect-square w-full"
                      link
                    />
                  ))}
                </div>
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader><CardTitle>Details</CardTitle></CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Row label="Station" value={action.station_code ?? "—"} />
              <Row label="Reason" value={action.reason_label ?? "—"} />
              <Row label="Owner" value={action.owner_name ?? "unassigned"} />
              <Row label="Due" value={action.due_date ?? "—"} />
              <Row label="Trigger" value={action.trigger.replace(/_/g, " ")} />
              {action.closed_at ? (
                <Row
                  label="Closed"
                  value={new Date(action.closed_at).toLocaleDateString("en-IN", {
                    day: "numeric",
                    month: "short",
                    year: "numeric",
                  })}
                />
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-2">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  );
}

/** §5.5 — pre-filled with the event history, so the team starts from evidence. */
function FiveWhys({ id, rca }: { id: string; rca: NonNullable<Awaited<ReturnType<typeof api.rca>>> }) {
  const queryClient = useQueryClient();
  const [whys, setWhys] = useState(
    rca.five_whys?.length ? rca.five_whys : Array.from({ length: 5 }, () => ({ why: "", because: "" })),
  );
  const [conclusion, setConclusion] = useState(rca.conclusion ?? "");

  const save = useMutation({
    mutationFn: () => api.updateRca(id, { five_whys: whys, conclusion: conclusion || null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["rca", id] });
      void queryClient.invalidateQueries({ queryKey: ["action", id] });
      void queryClient.invalidateQueries({ queryKey: ["actions"] });
    },
  });

  const context = rca.prefilled_context as Record<string, unknown> | null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Root cause — 5 Why</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {context ? (
          <div className="rounded-lg surface-2 p-3 text-sm">
            <p className="label mb-1">
              What we already know
            </p>
            <p>
              {String(context.occurrences)} occurrence(s) of {String(context.reason)} on{" "}
              {String(context.station)}
              {Array.isArray(context.event_dates) && context.event_dates.length
                ? ` — ${(context.event_dates as string[]).join(", ")}`
                : ""}
              .
            </p>
            <p className="mt-1 text-2">
              A blank form at 8 AM gets closed again. This one starts from the evidence.
            </p>
          </div>
        ) : null}

        <div className="space-y-2">
          {whys.map((row, index) => (
            <div key={index} className="grid gap-2 sm:grid-cols-[1fr_1fr]">
              <Input
                placeholder={index === 0 ? "Why did it happen?" : `Why ${index + 1}?`}
                value={row.why}
                onChange={(e) => {
                  const next = [...whys];
                  next[index] = { ...next[index], why: e.target.value };
                  setWhys(next);
                }}
              />
              <Input
                placeholder="Because…"
                value={row.because}
                onChange={(e) => {
                  const next = [...whys];
                  next[index] = { ...next[index], because: e.target.value };
                  setWhys(next);
                }}
              />
            </div>
          ))}
        </div>

        <div>
          <Label htmlFor="conclusion">Root cause</Label>
          <Textarea
            id="conclusion"
            rows={2}
            value={conclusion}
            onChange={(e) => setConclusion(e.target.value)}
            placeholder="Bearing seized — wrong grease grade used at the last PM"
          />
          <p className="mt-1 text-xs text-2">
            The reason code said what happened. This says why.
          </p>
        </div>

        {save.error ? <ErrorNote message={save.error.message} /> : null}
        {save.isSuccess ? (
          <p role="status" className="text-xs text-[var(--color-running)]">Saved.</p>
        ) : null}

        <Button size="lg" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save root cause"}
        </Button>
      </CardContent>
    </Card>
  );
}

/**
 * Closure — and the four Factory Memory questions.
 *
 * These are asked exactly once, at the only moment the person who fixed it is
 * both available and still remembers. Fifty closures later the plant owns a
 * private repair playbook that survives any resignation.
 */
const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];
const PHOTO_MAX_BYTES = 10 * 1024 * 1024;

function ClosureForm({
  actionId,
  busy,
  error,
  needsRootCause,
  onSubmit,
}: {
  actionId: string;
  busy: boolean;
  error: string | null;
  needsRootCause: boolean;
  onSubmit: (payload: Record<string, unknown>) => void;
}) {
  const queryClient = useQueryClient();
  const [rootCause, setRootCause] = useState("");
  const [countermeasure, setCountermeasure] = useState("");
  const [whatFixed, setWhatFixed] = useState("");
  const [replaced, setReplaced] = useState("");
  const [checkFirst, setCheckFirst] = useState("");
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [photoInvalid, setPhotoInvalid] = useState<string | null>(null);

  // Uploaded on pick, not on close: a large photo over plant Wi-Fi takes a
  // while, and the person should see it arrived before they tap Close.
  const photo = useMutation({
    mutationFn: (file: File) => api.uploadAttachment(file, "action", actionId),
    onSuccess: (attachment) => {
      setPhotoUrl(attachment.url);
      void queryClient.invalidateQueries({ queryKey: ["action", actionId] });
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Close this action</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit({
              countermeasure,
              root_cause: rootCause || null,
              memory: {
                what_fixed_it: whatFixed,
                what_was_replaced: replaced || null,
                check_first_next_time: checkFirst || null,
                ...(photoUrl ? { photo_url: photoUrl } : {}),
              },
            });
          }}
        >
          {needsRootCause ? (
            <div>
              <Label htmlFor="root">Root cause</Label>
              <Input
                id="root"
                value={rootCause}
                onChange={(e) => setRootCause(e.target.value)}
                placeholder="Wrong grease grade used at the last PM"
              />
            </div>
          ) : null}

          <div>
            <Label htmlFor="counter">Countermeasure</Label>
            <Input
              id="counter"
              required
              value={countermeasure}
              onChange={(e) => setCountermeasure(e.target.value)}
              placeholder="Changed PM spec to the correct grease grade and retrained the team"
            />
          </div>

          <div className="surface-2 rounded-xl border p-4">
            <p className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
              <span className="brand grid size-5 place-items-center rounded-md text-[0.625rem] font-bold">
                4
              </span>
              Factory Memory — 60 seconds
            </p>
            <p className="text-3 mb-4 text-xs leading-relaxed">
              Four questions, asked once. The next person who hits this problem — in two
              years, after you have left — gets your answer in thirty seconds.
            </p>

            <div className="space-y-3">
              <div>
                <Label htmlFor="fixed">1. What fixed it?</Label>
                <Input
                  id="fixed"
                  required
                  value={whatFixed}
                  onChange={(e) => setWhatFixed(e.target.value)}
                  placeholder="Replaced the carbide insert grade on Station 3"
                />
              </div>
              <div>
                <Label htmlFor="replaced">2. What did you replace or change?</Label>
                <Input
                  id="replaced"
                  value={replaced}
                  onChange={(e) => setReplaced(e.target.value)}
                  placeholder="Insert grade P25 → P15"
                />
              </div>
              <div>
                <Label htmlFor="check">3. What should someone check first next time?</Label>
                <Input
                  id="check"
                  value={checkFirst}
                  onChange={(e) => setCheckFirst(e.target.value)}
                  placeholder="Check insert wear at 240 parts, not 310"
                />
              </div>
              <div>
                <Label htmlFor="closure-photo">4. Attach a photo (optional)</Label>
                <input
                  id="closure-photo"
                  type="file"
                  accept="image/*"
                  capture="environment"
                  disabled={photo.isPending}
                  aria-describedby="closure-photo-hint"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (!file) return;
                    photo.reset();
                    if (!PHOTO_TYPES.includes(file.type)) {
                      return setPhotoInvalid("Use a JPEG, PNG or WebP photo.");
                    }
                    if (file.size > PHOTO_MAX_BYTES) {
                      return setPhotoInvalid("That photo is over 10 MB. Take it again at a lower size.");
                    }
                    setPhotoInvalid(null);
                    photo.mutate(file);
                  }}
                  className="text-2 block w-full text-sm file:mr-3 file:h-11 file:cursor-pointer file:rounded-full file:border file:border-[var(--border)] file:bg-[var(--surface-2)] file:px-4 file:text-sm file:font-medium file:text-[var(--text)]"
                />
                <p id="closure-photo-hint" className="text-3 mt-1 text-xs">
                  The fixed part, the worn one, or the setting that was changed. Opens the camera on a tablet.
                </p>
                {photo.isPending ? <p role="status" className="text-2 mt-2 text-xs">Uploading photo…</p> : null}
                {photoInvalid ? <div className="mt-2"><ErrorNote message={photoInvalid} /></div> : null}
                {photo.error ? (
                  <div className="mt-2">
                    <ErrorNote message={`Could not upload the photo: ${photo.error.message}`} />
                  </div>
                ) : null}
                {photoUrl ? (
                  <div className="mt-3 flex items-end gap-3">
                    <AuthedImage url={photoUrl} alt="Uploaded closure photo" className="size-28" />
                    <p role="status" className="text-2 text-xs">
                      Uploaded. It goes into Factory Memory with your answers. Pick another to replace it.
                    </p>
                  </div>
                ) : null}
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-dashed p-3.5 text-xs leading-relaxed text-2">
            Closing moves this to <strong>Monitoring</strong>, not Done. The system watches
            the linked reason code for the next few weeks — if the loss actually stopped, the
            savings are credited; if the events come back, the action reopens.
          </div>

          {/* Repeated beside the button: the person who just typed four answers
              is looking here, not at the top of the page. */}
          {error ? <ErrorNote message={error} /> : null}

          <Button type="submit" size="touch" disabled={busy || photo.isPending}>
            {busy ? "Closing…" : photo.isPending ? "Waiting for the photo…" : "Close and start monitoring"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function ClosedSummary({ action }: { action: Action }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {action.state === "verified"
            ? "Verified"
            : action.state === "cancelled"
              ? "Cancelled"
              : "Monitoring"}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <p className="text-xs text-2">Root cause</p>
          <p>{action.root_cause ?? "—"}</p>
        </div>
        <div>
          <p className="text-xs text-2">Countermeasure</p>
          <p>{action.countermeasure ?? "—"}</p>
        </div>
        <p className="text-xs text-2">
          {action.state === "verified"
            ? "The loss rate stayed down for two consecutive weeks, so the savings are credited to this action."
            : action.state === "cancelled"
              ? "Cancelled — no savings are credited. The events behind it stay logged and costed."
              : "Watching the linked reason code. Savings are credited only once the fix has held."}
        </p>
      </CardContent>
    </Card>
  );
}
