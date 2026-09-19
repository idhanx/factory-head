"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CornerDownLeft, Languages, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { cn } from "@/lib/utils";
import type { CopilotAnswer } from "@/types/api";
import { useT } from "@/lib/i18n";

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
 */
const SUGGESTIONS = [
  "How much did we lose this week?",
  "Which machine lost the most yesterday?",
  "Which defect is increasing?",
  "Have we ever had rust spots on brackets before?",
  "Why does CNC-3 keep failing?",
];

export default function Copilot() {
  const t = useT();
  const [question, setQuestion] = useState("");
  const [language, setLanguage] = useState<"en" | "ta">("en");
  const [thread, setThread] = useState<Array<{ q: string; a: CopilotAnswer }>>([]);
  const endRef = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: (q: string) => api.ask(q, language),
    onSuccess: (answer, q) => {
      setThread((prev) => [...prev, { q, a: answer }]);
      setQuestion("");
    },
  });

  /** One question at a time: two in flight land in the thread in whichever
      order the server finishes them, which reads as the wrong answer to the
      wrong question. */
  const busy = ask.isPending;

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
        <div className="surface-2 flex gap-0.5 rounded-lg border p-1">
          {(["en", "ta"] as const).map((code) => (
            <button
              key={code}
              type="button"
              onClick={() => setLanguage(code)}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[0.8125rem] font-medium transition-colors",
                language === code
                  ? "surface text-[var(--text)] shadow-[var(--shadow-1)]"
                  : "text-2 hover:text-[var(--text)]",
              )}
            >
              {code === "en" ? <Languages className="size-3.5" /> : null}
              {code === "en" ? t("English") : "தமிழ்"}
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

        {thread.map((entry, index) => (
          <div key={index} className="rise space-y-2.5">
            <div className="flex justify-end">
              <p className="surface-3 max-w-[85%] rounded-2xl rounded-br-md px-4 py-2.5 text-sm font-medium">
                {entry.q}
              </p>
            </div>

            <div className="surface elev-2 rounded-2xl rounded-bl-md border p-4">
              <p className="text-[0.9375rem] leading-relaxed">{entry.a.answer}</p>

              {entry.a.evidence.length ? (
                <div className="surface-2 mt-3.5 rounded-xl border p-3">
                  <p className="label mb-2">{t("Evidence")}</p>
                  <ul className="space-y-1.5">
                    {entry.a.evidence.slice(0, 4).map((item, i) => (
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
                <Badge variant="neutral">{entry.a.intent.replace(/_/g, " ")}</Badge>
                {!entry.a.tier_available ? (
                  <Badge variant="warn">{t("Not enough history for pattern answers")}</Badge>
                ) : null}
                <span className="text-3 ml-auto text-[0.6875rem]">
                  {t("{0} · {1}ms", { 0: entry.a.answered_by, 1: entry.a.latency_ms })}
                </span>
              </div>
            </div>
          </div>
        ))}

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
