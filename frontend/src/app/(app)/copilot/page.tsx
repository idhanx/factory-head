"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CornerDownLeft, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import type { CopilotAnswer } from "@/types/api";
import { LANGUAGES, useLanguage, type Language } from "@/lib/i18n";

/**
 * Factory Copilot (Layer 3).
 *
 *   "Anyone in the plant can ask a question in plain language and get an answer
 *    computed from their own plant's data. No dashboards to learn, no reports
 *    to dig through — just ask."
 *
 * The tier state is surfaced deliberately. Factual questions are correct from
 * day one; pattern questions are withheld until enough events exist to support
 * them, and the refusal is explicit. One confidently wrong answer to an owner
 * kills trust in every number the product shows — so the product would rather
 * say "I've seen 2 events like this" than guess.
 *
 * Questions about the company itself — what we make, for whom, on how many
 * machines — are answered too. They come from the plant's own onboarding data,
 * which makes them as grounded as a loss figure, and refusing them was the
 * product being obtuse about its own records.
 */
const SUGGESTIONS = [
  "How much did we lose this week?",
  "Which machine lost the most yesterday?",
  "Which defect is increasing?",
  "Who are our customers?",
  "Have we ever had rust spots on brackets before?",
  "Why does CNC-3 keep failing?",
];

/** One question asked once, held in every language it has been read in. */
type Entry = {
  id: number;
  /** As sent to the server. A suggestion is sent in English whatever the
      screen shows, because the plant's own records are written in English. */
  question: string;
  answers: Partial<Record<Language, CopilotAnswer>>;
};

function shown(entry: Entry, lang: Language): { answer: CopilotAnswer; lang: Language } {
  const wanted = entry.answers[lang];
  if (wanted) return { answer: wanted, lang };
  // Not translated yet, or the re-read failed. The answer already on screen is
  // still true — the facts behind it do not change with the language — so it
  // stays up, labelled with the language it is actually in.
  const fallback = (Object.keys(entry.answers) as Language[])[0];
  return { answer: entry.answers[fallback] as CopilotAnswer, lang: fallback };
}

function nativeName(lang: Language): string {
  return LANGUAGES.find((option) => option.code === lang)?.native ?? lang;
}

export default function Copilot() {
  // The picker below is the *app's* language, not a second one owned by this
  // screen. Two language settings on one page is how an answer ends up in a
  // different language from the button that asked for it.
  const { lang, setLang, t } = useLanguage();
  const [question, setQuestion] = useState("");
  const [thread, setThread] = useState<Entry[]>([]);
  const [rereading, setRereading] = useState(false);
  const nextId = useRef(0);
  const endRef = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: (q: string) => api.ask(q, lang),
    onSuccess: (answer, q) => {
      setThread((prev) => [
        ...prev,
        { id: nextId.current++, question: q, answers: { [lang]: answer } },
      ]);
      setQuestion("");
    },
  });

  /** One question at a time: two in flight land in the thread in whichever
      order the server finishes them, which reads as the wrong answer to the
      wrong question. */
  const busy = ask.isPending;

  // Switching the language has to carry the conversation with it. Anything on
  // screen that has not been read in the new language is asked for again —
  // rebuilt from the same facts server-side, never translated as text, so the
  // rupee figures in it stay the ones the kernel produced.
  useEffect(() => {
    const missing = thread.filter((entry) => !entry.answers[lang]);
    if (!missing.length) return;

    let live = true;
    setRereading(true);
    void Promise.all(
      missing.map(async (entry) => {
        try {
          return { id: entry.id, answer: await api.rephrase(entry.question, lang) };
        } catch {
          // A failed re-read leaves the previous language on screen, which is
          // an answer the person can still act on. Blanking it would not be.
          return null;
        }
      }),
    ).then((results) => {
      if (!live) return;
      setThread((prev) =>
        prev.map((entry) => {
          const hit = results.find((result) => result?.id === entry.id);
          return hit
            ? { ...entry, answers: { ...entry.answers, [lang]: hit.answer } }
            : entry;
        }),
      );
      setRereading(false);
    });

    return () => {
      live = false;
    };
  }, [lang, thread]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.length]);

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-11rem)] max-w-3xl flex-col">
      <header className="rise mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="label mb-1.5 flex items-center gap-1.5">
            <Sparkles className="size-3" />
            {t("Factory Copilot")}
          </p>
          <h1 className="display text-[1.75rem] font-bold">{t("Ask")}</h1>
          <p className="text-2 mt-1 text-sm">
            {t("Answers computed from this plant's own data — never from anywhere else.")}
          </p>
        </div>
        <div
          role="radiogroup"
          aria-label={t("Answer language")}
          className="surface-2 flex gap-0.5 rounded-lg border p-1"
        >
          {LANGUAGES.map((option) => (
            <button
              key={option.code}
              type="button"
              role="radio"
              aria-checked={option.code === lang}
              lang={option.code}
              onClick={() => setLang(option.code as Language)}
              className={cn(
                "rounded-md px-3 py-1.5 text-[0.8125rem] font-medium transition-colors",
                option.code === lang
                  ? "surface text-[var(--text)] shadow-[var(--shadow-1)]"
                  : "text-2 hover:text-[var(--text)]",
              )}
            >
              {/* The native name, so an operator who reads only Hindi does not
                  have to recognise the word "Hindi" in Latin script. */}
              {option.native}
            </button>
          ))}
        </div>
      </header>

      <div className="flex-1 space-y-5">
        {thread.length === 0 ? (
          <div className="rise">
            <p className="label mb-3">{t("Try asking")}</p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  disabled={busy}
                  // Asked in English whatever the screen is showing: the
                  // answer is retrieved from the plant's own records, and those
                  // are written in English.
                  onClick={() => ask.mutate(suggestion)}
                  className="surface elev-1 rounded-full border px-4 py-2 text-sm transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-[var(--shadow-2)] disabled:pointer-events-none disabled:opacity-50"
                >
                  {t(suggestion)}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {thread.map((entry) => {
          const { answer, lang: answerLang } = shown(entry, lang);
          return (
            <div key={entry.id} className="rise space-y-2.5">
              <div className="flex justify-end">
                {/* Translated on the way out, not stored translated: a question
                    picked from the suggestions follows the screen, one that was
                    typed passes through exactly as it was typed. */}
                <p className="surface-3 max-w-[85%] rounded-2xl rounded-br-md px-4 py-2.5 text-sm font-medium">
                  {t(entry.question)}
                </p>
              </div>

              <div className="surface elev-2 rounded-2xl rounded-bl-md border p-4">
                <p lang={answerLang} className="text-[0.9375rem] leading-relaxed">
                  {answer.answer}
                </p>

                {answer.evidence.length ? (
                  <div className="surface-2 mt-3.5 rounded-xl border p-3">
                    <p className="label mb-2">{t("Evidence")}</p>
                    <ul className="space-y-1.5">
                      {answer.evidence.slice(0, 4).map((item, i) => (
                        <li key={i} className="text-2 text-xs leading-relaxed">
                          {Object.entries(item)
                            .filter(([key]) => !key.endsWith("_paise") && key !== "recoverability")
                            .map(([key, value]) => `${key.replace(/_/g, " ")}: ${String(value)}`)
                            .join(" · ")}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <div className="mt-3.5 flex flex-wrap items-center gap-2">
                  {/* What the question was read as. Translated, because it is
                      a claim about the answer a supervisor should be able to
                      disagree with — "period loss" when they asked about a
                      defect is worth spotting. `answered_by` beside it is not:
                      it is provenance, the same class as OEE and PPM. */}
                  <Badge variant="neutral">{t(answer.intent.replace(/_/g, " "))}</Badge>
                  {!answer.tier_available ? (
                    <Badge variant="warn">{t("Not enough history for pattern answers")}</Badge>
                  ) : null}
                  {/* Said out loud rather than left to be noticed: an answer in
                      a language the reader did not pick should say so. */}
                  {answerLang !== lang ? (
                    <Badge variant="neutral">
                      {rereading
                        ? t("Switching to {0}…", { 0: nativeName(lang) })
                        : t("Shown in {0}", { 0: nativeName(answerLang) })}
                    </Badge>
                  ) : null}
                  <span className="text-3 ml-auto text-[0.6875rem]">
                    {t("{0} · {1}ms", { 0: answer.answered_by, 1: answer.latency_ms })}
                  </span>
                </div>
              </div>
            </div>
          );
        })}

        {/* A question that failed must say so. Silently returning to an idle
            box looks like the tablet ignored the person — and on a floor where
            the answer might be why a line is stopped, that is the worst of the
            possible failures. */}
        {ask.isError ? (
          <ErrorNote
            message={t("That question did not get through: {0} — the question is still in the box, so Ask will try again.", { 0: (ask.error as Error).message })}
          />
        ) : null}

        {ask.isPending ? (
          <div className="surface elev-1 flex items-center gap-2.5 rounded-2xl rounded-bl-md border p-4">
            <span className="led" style={{ background: "var(--color-accent)", color: "var(--color-accent)" }} />
            <span className="text-2 text-sm">{t("Reading the plant's data…")}</span>
          </div>
        ) : null}

        <div ref={endRef} />
      </div>

      <form
        className="sticky bottom-4 mt-6 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy && question.trim()) ask.mutate(question.trim());
        }}
      >
        <div className="relative flex-1">
          <Input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={t("Ask about downtime, losses, defects or past fixes…")}
            className="elev-3 h-13 pr-11 text-[0.9375rem]"
          />
          <CornerDownLeft className="text-3 pointer-events-none absolute right-3.5 top-1/2 size-4 -translate-y-1/2" />
        </div>
        <Button type="submit" size="touch" disabled={busy || !question.trim()}>
          {t("Ask")}
        </Button>
      </form>
    </div>
  );
}
