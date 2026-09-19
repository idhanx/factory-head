"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Camera,
  Check,
  ChevronRight,
  Loader2,
  Mic,
  Send,
  Sparkles,
  Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { ForecastPanel, HistoryPanel, ImpactCard, VisionFindings } from "./pieces";
import type {
  KaizenAnswer,
  KaizenClassification,
  KaizenIdea,
  KaizenPreview,
  KaizenQuestion,
  KaizenTrack,
  VisionFinding,
} from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * The operator journey (Plugin Spec §5).
 *
 * The whole design is judged against one rule: **if an operator cannot submit
 * an idea in under three minutes, they will stop using it by week two.** So:
 *
 *  - Two tracks, not one. A Quick Observation is 30 seconds — photo and voice,
 *    no interview, no cost engine. It is ~80% of real volume, and running the
 *    full analysis on a ₹200 observation buries the four ideas a month that are
 *    worth ₹4 lakh.
 *  - Speak, don't type. Typing is the single biggest reason paper and app-based
 *    suggestion schemes die on a shop floor.
 *  - Tap, don't type, for numbers. Ranges resolve to a midpoint, and a wrong
 *    number silently corrupts the savings figure.
 *  - Never ask what Core already knows. The machine, the part, the routing and
 *    the bottleneck factor are inherited and confirmed with one tap.
 */

type Step = "home" | "speak" | "photo" | "confirm" | "interview" | "review" | "done";

const STEP_ORDER: Step[] = ["speak", "photo", "confirm", "interview", "review"];

/** Phase 1 of the voice roadmap: the device transcribes, the model only tidies. */
type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
};

function speechRecognition(): SpeechRecognitionLike | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

export function SubmitFlow({
  defaultName,
  onSubmitted,
}: {
  defaultName: string;
  onSubmitted: () => void;
}) {
  const t = useT();
  const [step, setStep] = useState<Step>("home");
  const [track, setTrack] = useState<KaizenTrack>("full");

  // What the operator gave us.
  const [transcript, setTranscript] = useState("");
  const [title, setTitle] = useState("");
  const [stationId, setStationId] = useState("");
  const [onBehalfOf, setOnBehalfOf] = useState("");
  const [findings, setFindings] = useState<VisionFinding[]>([]);
  const [visionRan, setVisionRan] = useState(false);
  const [visionNote, setVisionNote] = useState<string | null>(null);
  const [attachmentId, setAttachmentId] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [answers, setAnswers] = useState<Record<string, string | number>>({});
  const [proposedFix, setProposedFix] = useState("");
  const [needsShutdown, setNeedsShutdown] = useState(false);
  const [classification, setClassification] = useState<KaizenClassification | null>(null);
  const [preview, setPreview] = useState<KaizenPreview | null>(null);
  const [created, setCreated] = useState<KaizenIdea | null>(null);

  const stations = useQuery({ queryKey: ["stations"], queryFn: api.stations });
  const operators = useQuery({ queryKey: ["operators"], queryFn: api.operators });

  const reset = () => {
    setStep("home");
    setTranscript("");
    setTitle("");
    setStationId("");
    setOnBehalfOf("");
    setFindings([]);
    setVisionRan(false);
    setVisionNote(null);
    setAttachmentId(null);
    setCategory("");
    setAnswers({});
    setProposedFix("");
    setNeedsShutdown(false);
    setClassification(null);
    setPreview(null);
    setCreated(null);
  };

  const description = transcript.trim();
  const headline = title.trim() || description.slice(0, 90);

  const interview: KaizenAnswer[] = useMemo(
    () =>
      (classification?.questions ?? [])
        .filter((q) => answers[q.key] !== undefined && answers[q.key] !== "")
        .map((q) => ({
          key: q.key,
          question: q.text,
          answer: answers[q.key],
          unit: q.unit,
          source: "operator" as const,
        })),
    [classification, answers],
  );

  const investmentPaise = useMemo(() => {
    const raw = answers["fix_cost"];
    if (raw === undefined || raw === "") return 0;
    const question = classification?.questions.find((q) => q.key === "fix_cost");
    if (question && question.kind === "choice") {
      const index = question.options.indexOf(String(raw));
      return index >= 0 ? Math.round((question.values[index] ?? 0) * 100) : 0;
    }
    return Math.round(Number(raw) * 100) || 0;
  }, [answers, classification]);

  const payload = () => ({
    title: headline,
    description: description || headline,
    track,
    voice_transcript: transcript || null,
    station_id: stationId || null,
    category: category || null,
    vision_findings: findings,
    vision_ran: visionRan,
    interview,
    investment_paise: investmentPaise,
    needs_shutdown: needsShutdown,
    proposed_fix: proposedFix || null,
    context_confirmed: true,
    credited_operator_id: onBehalfOf || null,
    submitted_by_name: defaultName || null,
    attachment_ids: attachmentId ? [attachmentId] : [],
  });

  const classify = useMutation({
    mutationFn: () =>
      api.kaizenClassify({
        title: headline,
        description: description || headline,
        station_id: stationId || null,
        vision_findings: findings,
      }),
    onSuccess: (result) => {
      setClassification(result);
      setCategory(result.category);
      setStep("confirm");
    },
  });

  const runPreview = useMutation({
    mutationFn: () => api.kaizenPreview(payload()),
    onSuccess: (result) => {
      setPreview(result);
      setStep("review");
    },
  });

  const submit = useMutation({
    mutationFn: () => api.submitKaizen(payload()),
    onSuccess: (idea) => {
      setCreated(idea);
      setStep("done");
      onSubmitted();
    },
  });

  if (step === "home") {
    return (
      <TwoTracks
        onPick={(picked) => {
          setTrack(picked);
          setStep("speak");
        }}
      />
    );
  }

  if (step === "done" && created) {
    return <Submitted idea={created} onAgain={reset} />;
  }

  const index = STEP_ORDER.indexOf(step);
  const visible = track === "quick" ? STEP_ORDER.slice(0, 3) : STEP_ORDER;

  return (
    <Card className="rise">
      <CardContent className="pt-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => (index <= 0 ? reset() : setStep(STEP_ORDER[index - 1]))}
            className="text-2 flex items-center gap-1 text-sm hover:text-[var(--text)]"
          >
            <ArrowLeft className="size-4" />
            {t("Back")}
          </button>
          <div className="flex items-center gap-1.5">
            {visible.map((name, position) => (
              <span
                key={name}
                className={cn(
                  "h-1 rounded-full transition-all",
                  position <= index ? "w-6 bg-[var(--color-brand)]" : "w-3 surface-3",
                )}
              />
            ))}
          </div>
          <Badge variant={track === "quick" ? "accent" : "brand"}>
            {track === "quick" ? t("30 seconds") : t("3 minutes")}
          </Badge>
        </div>

        {step === "speak" ? (
          <SpeakStep
            transcript={transcript}
            title={title}
            onTranscript={setTranscript}
            onTitle={setTitle}
            onNext={() => setStep("photo")}
          />
        ) : null}

        {step === "photo" ? (
          <PhotoStep
            said={description}
            findings={findings}
            note={visionNote}
            onResult={(result) => {
              setFindings(result.findings);
              setVisionRan(result.ran);
              setVisionNote(result.note);
              setAttachmentId(result.attachment_id);
            }}
            onToggle={(position) =>
              setFindings((current) =>
                current.map((finding, i) =>
                  i === position ? { ...finding, accepted: !finding.accepted } : finding,
                ),
              )
            }
            onNext={() => {
              if (track === "quick") {
                submit.mutate();
              } else {
                classify.mutate();
              }
            }}
            pending={classify.isPending || submit.isPending}
            quick={track === "quick"}
            error={(classify.error ?? submit.error) as Error | null}
          />
        ) : null}

        {step === "confirm" && classification ? (
          <ConfirmStep
            classification={classification}
            category={category}
            stationId={stationId}
            stations={stations.data ?? []}
            operators={operators.data ?? []}
            onBehalfOf={onBehalfOf}
            onCategory={setCategory}
            onStation={(value) => {
              setStationId(value);
              // The context changes which questions Core can answer, so the
              // interview is re-derived rather than patched.
              classify.mutate();
            }}
            onBehalf={setOnBehalfOf}
            onNext={() => setStep("interview")}
          />
        ) : null}

        {step === "interview" && classification ? (
          <InterviewStep
            questions={classification.questions}
            answers={answers}
            proposedFix={proposedFix}
            needsShutdown={needsShutdown}
            onAnswer={(key, value) => setAnswers((c) => ({ ...c, [key]: value }))}
            onFix={setProposedFix}
            onShutdown={setNeedsShutdown}
            onNext={() => runPreview.mutate()}
            pending={runPreview.isPending}
            error={runPreview.error as Error | null}
          />
        ) : null}

        {step === "review" && preview ? (
          <ReviewStep
            preview={preview}
            onSubmit={() => submit.mutate()}
            pending={submit.isPending}
            error={submit.error as Error | null}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * Two buttons, not one.
 *
 * Most of what an operator notices is small — a loose guard, an unlabelled bin
 * — and should take 30 seconds, not 3 minutes.
 */
function TwoTracks({ onPick }: { onPick: (track: KaizenTrack) => void }) {
  const t = useT();
  return (
    <div className="grid gap-3">
      <button
        type="button"
        onClick={() => onPick("quick")}
        className="rail border-rail rise flex items-center gap-4 rounded-[var(--radius-card)] border p-5 text-left transition-colors hover:bg-[var(--rail-2)]"
      >
        <span className="grid size-11 shrink-0 place-items-center rounded-full bg-[var(--color-warn-dim)] text-[var(--color-warn)]">
          <Zap className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[1.0625rem] font-bold">{t("Quick observation")}</span>
          <span className="text-2 block text-sm">{t("30 seconds · photo and voice")}</span>
        </span>
        <ChevronRight className="text-3 size-5 shrink-0" />
      </button>

      <button
        type="button"
        onClick={() => onPick("full")}
        className="rail border-rail rise flex items-center gap-4 rounded-[var(--radius-card)] border p-5 text-left transition-colors hover:bg-[var(--rail-2)]"
        style={{ animationDelay: "60ms" }}
      >
        <span className="grid size-11 shrink-0 place-items-center rounded-full bg-[var(--color-accent-dim)] text-[var(--color-accent)]">
          <Sparkles className="size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[1.0625rem] font-bold">{t("Full Kaizen")}</span>
          <span className="text-2 block text-sm">{t("3 minutes · full analysis")}</span>
        </span>
        <ChevronRight className="text-3 size-5 shrink-0" />
      </button>
    </div>
  );
}

/** Step 1 — speak the problem. Typing is offered, never required. */
function SpeakStep({
  transcript,
  title,
  onTranscript,
  onTitle,
  onNext,
}: {
  transcript: string;
  title: string;
  onTranscript: (value: string) => void;
  onTitle: (value: string) => void;
  onNext: () => void;
}) {
  const t = useT();
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(false);
  const [tidying, setTidying] = useState(false);
  const recogniser = useRef<SpeechRecognitionLike | null>(null);

  useEffect(() => {
    setSupported(speechRecognition() !== null);
    return () => recogniser.current?.stop();
  }, []);

  const listen = () => {
    const engine = speechRecognition();
    if (!engine) return;
    recogniser.current = engine;
    // Mixed language is how people actually talk on a shop floor; the browser
    // handles the regional locale and the model tidies the result.
    engine.lang = navigator.language || "en-IN";
    engine.continuous = true;
    engine.interimResults = true;
    engine.onresult = (event) => {
      let text = "";
      for (let i = 0; i < event.results.length; i += 1) {
        text += event.results[i][0].transcript;
      }
      onTranscript(text);
    };
    engine.onerror = () => setListening(false);
    engine.onend = () => setListening(false);
    engine.start();
    setListening(true);
  };

  const stop = async () => {
    recogniser.current?.stop();
    setListening(false);
    if (!transcript.trim()) return;
    // The model tidies the words into one clear line and changes no facts: a
    // rewrite that introduces a number nobody said is discarded server-side.
    setTidying(true);
    try {
      const tidy = await api.kaizenTranscript(transcript);
      onTranscript(tidy.text);
    } catch {
      /* the operator's own words are perfectly usable */
    } finally {
      setTidying(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-[1.0625rem] font-bold">{t("What is the problem?")}</h3>
        <p className="text-2 mt-0.5 text-sm">
          {t("Just talk. Say what happens and where — no need to be tidy about it.")}
        </p>
      </div>

      {supported ? (
        <Button
          type="button"
          variant={listening ? "stop" : "feature"}
          size="xl"
          className="w-full"
          onClick={() => (listening ? void stop() : listen())}
        >
          {listening ? (
            <>
              <span className="led" style={{ background: "white", color: "white" }} />
              {t("Listening — tap when you're done")}
            </>
          ) : (
            <>
              <Mic className="size-5" />
              {transcript ? t("Say more") : t("Tap and speak")}
            </>
          )}
        </Button>
      ) : null}

      <div>
        <Label htmlFor="kz-transcript">
          {supported ? t("Or type it") : t("Describe the problem")}
        </Label>
        <Textarea
          id="kz-transcript"
          rows={4}
          value={transcript}
          onChange={(e) => onTranscript(e.target.value)}
          placeholder={t("Every shift I walk about 20 metres to another machine to get the toolbox. Four of us share it.")}
        />
        {tidying ? (
          <p className="text-3 mt-1 flex items-center gap-1.5 text-xs">
            <Loader2 className="size-3 animate-spin" />
            {t("Tidying that up…")}
          </p>
        ) : null}
      </div>

      <div>
        <Label htmlFor="kz-title">{t("Give it a short name (optional)")}</Label>
        <Input
          id="kz-title"
          value={title}
          onChange={(e) => onTitle(e.target.value)}
          placeholder={t("Toolbox shared across machines")}
        />
      </div>

      <Button
        type="button"
        size="lg"
        className="w-full"
        disabled={transcript.trim().length < 10}
        onClick={onNext}
      >
        {t("Next — take a photo")}<ChevronRight className="size-4" />
      </Button>
    </div>
  );
}

/** Step 2 — the photo, and what the camera noticed in it. */
function PhotoStep({
  said,
  findings,
  note,
  onResult,
  onToggle,
  onNext,
  pending,
  quick,
  error,
}: {
  said: string;
  findings: VisionFinding[];
  note: string | null;
  onResult: (result: {
    findings: VisionFinding[];
    ran: boolean;
    note: string | null;
    attachment_id: string | null;
  }) => void;
  onToggle: (index: number) => void;
  onNext: () => void;
  pending: boolean;
  quick: boolean;
  error: Error | null;
}) {
  const t = useT();
  const [preview, setPreview] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const analyse = async (file: File) => {
    setPreview(URL.createObjectURL(file));
    setLooking(true);
    setFailed(null);
    try {
      onResult(await api.kaizenVision(file, said));
    } catch (exc) {
      setFailed((exc as Error).message);
    } finally {
      setLooking(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-[1.0625rem] font-bold">{t("Take one photo")}</h3>
        <p className="text-2 mt-0.5 text-sm">
          {t("That is all you need to do. Optional, but it makes the report much stronger.")}
        </p>
      </div>

      {preview ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={preview}
          alt={t("The photo you took")}
          className="max-h-64 w-full rounded-xl border object-cover"
        />
      ) : null}

      <label className="block">
        <input
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void analyse(file);
          }}
        />
        <span className="rail border-rail flex h-14 w-full cursor-pointer items-center justify-center gap-2 rounded-full border text-[0.9375rem] font-semibold transition-colors hover:bg-[var(--rail-2)]">
          {looking ? (
            <>
              <Loader2 className="size-5 animate-spin" />
              {t("Looking at your photo…")}
            </>
          ) : (
            <>
              <Camera className="size-5" />
              {preview ? t("Take another") : t("Take a photo")}
            </>
          )}
        </span>
      </label>

      <VisionFindings findings={findings} onToggle={onToggle} />

      {note ? <p className="text-3 text-xs leading-relaxed">{note}</p> : null}
      {failed ? <ErrorNote message={failed} /> : null}
      {error ? <ErrorNote message={error.message} /> : null}

      <Button
        type="button"
        size="lg"
        className="w-full"
        disabled={pending || looking}
        onClick={onNext}
      >
        {pending ? <Loader2 className="size-4 animate-spin" /> : null}
        {quick ? t("Submit observation") : t("Next — confirm the details")}
        {!pending ? <ChevronRight className="size-4" /> : null}
      </Button>
      <p className="text-3 text-center text-xs">
        {quick
          ? t("Takes 5 points and clears your month. Nothing else needed.")
          : t("Skipping the photo is fine.")}
      </p>
    </div>
  );
}

/** Step 3 — confirm the category AND the machine Core inherited. One tap each. */
function ConfirmStep({
  classification,
  category,
  stationId,
  stations,
  operators,
  onBehalfOf,
  onCategory,
  onStation,
  onBehalf,
  onNext,
}: {
  classification: KaizenClassification;
  category: string;
  stationId: string;
  stations: Array<{ id: string; code: string }>;
  operators: Array<{ id: string; name: string }>;
  onBehalfOf: string;
  onCategory: (value: string) => void;
  onStation: (value: string) => void;
  onBehalf: (value: string) => void;
  onNext: () => void;
}) {
  const t = useT();
  const [changing, setChanging] = useState(false);
  const context = classification.context;
  const label =
    classification.categories.find((c) => c.value === category)?.label ??
    classification.category_label;

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-[1.0625rem] font-bold">{t("Quick check")}</h3>
        <p className="text-2 mt-0.5 text-sm">{t("Two taps and we're on to the questions.")}</p>
      </div>

      <div className="surface-2 rounded-xl border px-3.5 py-3">
        <p className="label mb-1">{t("I think this is")}</p>
        {!changing ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[1.0625rem] font-bold">{label}</span>
            <Button size="sm" variant="ghost" onClick={() => setChanging(true)}>
              {t("Change")}
            </Button>
          </div>
        ) : (
          <Select
            value={category}
            onChange={(e) => {
              onCategory(e.target.value);
              setChanging(false);
            }}
          >
            {classification.categories.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        )}
      </div>

      {/* Inherited from Core's job order spine — shown for confirmation, never
          silently assumed: an operator may be standing at a different machine
          from the one they are logged into. */}
      <div className="surface-2 rounded-xl border px-3.5 py-3">
        <p className="label mb-1">{t("Where")}</p>
        {context.station_code ? (
          <p className="mb-2 text-sm">
            <strong>{context.station_code}</strong>
            {context.part_number ? t(", running {0}", { 0: context.part_number }) : ""}
            {context.line ? ` · ${context.line}` : ""} {" "}{t("— correct?")}
          </p>
        ) : (
          <p className="text-2 mb-2 text-sm">{t("Which machine is this about?")}</p>
        )}
        <Select value={stationId} onChange={(e) => onStation(e.target.value)}>
          <option value="">{t("Not about one machine")}</option>
          {stations.map((station) => (
            <option key={station.id} value={station.id}>
              {station.code}
            </option>
          ))}
        </Select>
        {classification.skipped.length > 0 ? (
          <p className="text-3 mt-1.5 text-[0.6875rem]">
            {t("{0} question{1} skipped — the system already knows the answer.", { 0: classification.skipped.length, 1: classification.skipped.length === 1 ? "" : "s" })}
          </p>
        ) : null}
      </div>

      {/* Submit-on-behalf. Points and recognition go to the named operator, not
          to whoever is holding the phone. */}
      <div>
        <Label htmlFor="kz-behalf">{t("Submitting on behalf of (optional)")}</Label>
        <Select id="kz-behalf" value={onBehalfOf} onChange={(e) => onBehalf(e.target.value)}>
          <option value="">{t("Myself")}</option>
          {operators.map((operator) => (
            <option key={operator.id} value={operator.id}>
              {operator.name}
            </option>
          ))}
        </Select>
        {onBehalfOf ? (
          <p className="text-3 mt-1 text-xs">
            {t("The points and the credit go to them, not to you.")}
          </p>
        ) : null}
      </div>

      <Button type="button" size="lg" className="w-full" onClick={onNext}>
        {t("Yes, that's right")}<ChevronRight className="size-4" />
      </Button>
    </div>
  );
}

/** Step 4 — four to six questions, one at a time, like a WhatsApp chat. */
function InterviewStep({
  questions,
  answers,
  proposedFix,
  needsShutdown,
  onAnswer,
  onFix,
  onShutdown,
  onNext,
  pending,
  error,
}: {
  questions: KaizenQuestion[];
  answers: Record<string, string | number>;
  proposedFix: string;
  needsShutdown: boolean;
  onAnswer: (key: string, value: string | number) => void;
  onFix: (value: string) => void;
  onShutdown: (value: boolean) => void;
  onNext: () => void;
  pending: boolean;
  error: Error | null;
}) {
  const t = useT();
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-[1.0625rem] font-bold">
          {t("{0} quick question{1}", { 0: questions.length, 1: questions.length === 1 ? "" : "s" })}
        </h3>
        <p className="text-2 mt-0.5 text-sm">{t("Tap an answer. Nothing here is a test.")}</p>
      </div>

      {questions.map((question) => (
        <div key={question.key}>
          <Label htmlFor={`q-${question.key}`}>{question.text}</Label>
          {question.kind === "choice" ? (
            <div className="flex flex-wrap gap-2">
              {question.options.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => onAnswer(question.key, option)}
                  className={cn(
                    "rounded-full border px-3.5 py-2 text-sm font-medium transition-colors",
                    answers[question.key] === option
                      ? "brand border-transparent"
                      : "surface-2 text-2 hover:surface-3",
                  )}
                >
                  {option}
                </button>
              ))}
            </div>
          ) : question.kind === "boolean" ? (
            <div className="flex gap-2">
              {["Yes", "No"].map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => onAnswer(question.key, option === "Yes" ? "true" : "false")}
                  className={cn(
                    "rounded-full border px-4 py-2 text-sm font-medium transition-colors",
                    (answers[question.key] === "true") === (option === "Yes") &&
                      answers[question.key] !== undefined
                      ? "brand border-transparent"
                      : "surface-2 text-2 hover:surface-3",
                  )}
                >
                  {t(option)}
                </button>
              ))}
            </div>
          ) : (
            <Input
              id={`q-${question.key}`}
              type={question.kind === "number" ? "number" : "text"}
              inputMode={question.kind === "number" ? "numeric" : undefined}
              value={answers[question.key] ?? ""}
              onChange={(e) => onAnswer(question.key, e.target.value)}
              placeholder={question.unit ?? ""}
            />
          )}
        </div>
      ))}

      <div>
        <Label htmlFor="kz-fix">{t("What would you do about it?")}</Label>
        <Textarea
          id="kz-fix"
          rows={2}
          value={proposedFix}
          onChange={(e) => onFix(e.target.value)}
          placeholder={t("Buy a second toolbox and keep it at this machine")}
        />
      </div>

      <label className="text-2 flex cursor-pointer items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={needsShutdown}
          onChange={(e) => onShutdown(e.target.checked)}
          className="size-4 accent-[var(--color-brand)]"
        />
        {t("The machine would have to be stopped to do this")}
      </label>

      {error ? <ErrorNote message={error.message} /> : null}

      <Button type="button" size="lg" className="w-full" disabled={pending} onClick={onNext}>
        {pending ? <Loader2 className="size-4 animate-spin" /> : null}
        {t("See what this is worth")}{!pending ? <ChevronRight className="size-4" /> : null}
      </Button>
    </div>
  );
}

/** Steps 5A-9 — the evidence, the money, the alternative and the forecast. */
function ReviewStep({
  preview,
  onSubmit,
  pending,
  error,
}: {
  preview: KaizenPreview;
  onSubmit: () => void;
  pending: boolean;
  error: Error | null;
}) {
  const t = useT();
  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-[1.0625rem] font-bold">{t("Here is what I found")}</h3>
        <p className="text-2 mt-0.5 text-sm">
          {t("Nothing is submitted yet. Have a look, then send it.")}
        </p>
      </div>

      <HistoryPanel history={preview.history} />
      <ImpactCard impact={preview.impact} />

      {/* Step 7: exactly one alternative, phrased as an alternative and ending
          in a question — AI does not know the plant layout and will sometimes
          suggest something physically impossible. The operator can always send
          their own idea with one tap. */}
      {preview.challenge?.offered ? (
        <div className="surface-2 rounded-xl border px-3.5 py-3">
          <p className="label mb-1">{t("Before we submit — one alternative")}</p>
          <p className="text-sm leading-relaxed">{preview.challenge.alternative}</p>
          {preview.challenge.rough_cost_paise > 0 ? (
            <p className="text-3 mt-1 text-xs">
              {t("Roughly ₹{0}", { 0: Math.round(preview.challenge.rough_cost_paise / 100).toLocaleString("en-IN") })}
            </p>
          ) : null}
          <p className="text-2 mt-2 text-xs leading-relaxed italic">
            {preview.challenge.question}
          </p>
        </div>
      ) : null}

      {preview.forecast ? <ForecastPanel forecast={preview.forecast} /> : null}

      {error ? <ErrorNote message={error.message} /> : null}

      <Button type="button" size="lg" className="w-full" disabled={pending} onClick={onSubmit}>
        {pending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        {t("Submit this Kaizen")}
      </Button>
    </div>
  );
}

function Submitted({ idea, onAgain }: { idea: KaizenIdea; onAgain: () => void }) {
  const t = useT();
  return (
    <Card className="rise">
      <CardContent className="pt-6 text-center">
        <div className="mx-auto mb-3 grid size-12 place-items-center rounded-full bg-[color-mix(in_oklab,var(--color-recovered)_14%,transparent)] text-[var(--color-recovered)]">
          <Check className="size-6" />
        </div>
        <p className="text-3 tnum text-xs font-semibold">{idea.reference}</p>
        <h3 className="mt-0.5 text-[1.0625rem] font-bold">{idea.title}</h3>

        {idea.duplicate_action_id ? (
          <p className="text-2 mt-2 text-sm leading-relaxed">
            {t("Somebody is already on this one — your report has been added to their action, and you still get the credit for spotting it.")}
          </p>
        ) : idea.priority_verdict === "HIGH" ? (
          <p className="mt-2 text-sm leading-relaxed">
            <strong>{t("Your idea is high priority.")}</strong>{" "}
            {idea.history?.rank
              ? t("This problem is the #{0} recurring loss in the plant.", { 0: idea.history.rank })
              : ""}
          </p>
        ) : (
          <p className="text-2 mt-2 text-sm leading-relaxed">
            {t("Logged and sent for a decision. You will get a message the moment anything happens to it.")}
          </p>
        )}

        <p className="text-3 mt-3 text-xs">
          {t("+{0} point{1}", { 0: idea.points_awarded, 1: idea.points_awarded === 1 ? "" : "s" })}
        </p>

        <Button className="mt-4 w-full" size="lg" variant="outline" onClick={onAgain}>
          {t("Report something else")}
        </Button>
      </CardContent>
    </Card>
  );
}
