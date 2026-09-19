"use client";

import { useEffect, useReducer } from "react";
import { Check, Languages } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  LANGUAGES, loadDictionary, translate, useLanguage, useT, type Language,
} from "@/lib/i18n";
import { Toolbar } from "./shared";

/**
 * Language — the one Settings tab that changes every other screen.
 *
 * It is stored per device rather than per account, and that is the deliberate
 * choice. A bench tablet is shared by whoever is on shift; the operator who
 * picks up Murugan's tablet at 2 PM wants the language of the machine in front
 * of them, not the language of whoever logged in last. An owner who opens the
 * same plant on their own phone still gets their own setting there.
 *
 * What does not change: money, counts, machine codes, part numbers and clock
 * times. Those come from the calculation kernel already formatted, and a rupee
 * figure that reads differently on two tablets is a figure nobody trusts.
 */

/* The preview is three real strings from the running app rather than a lorem
   sentence: a supervisor picking a language should see the words they are
   about to work in, not a demonstration of the alphabet.

   Each card previews in *its own* language, which is why they call `translate`
   directly instead of the `useT` hook — the hook is bound to the language that
   is already active, so the Tamil card would sit there in English until after
   you had switched to Tamil. That is exactly backwards for a picker. */
const PREVIEW_KEYS = ["Machine Grid", "Recoverable loss", "Close shift"] as const;

export function LanguageTab() {
  const t = useT();
  const { lang, setLang } = useLanguage();

  // Each card previews in its own language, so this one screen needs all three
  // dictionaries — not just the active one the provider fetched. They are small
  // and this tab is opened rarely, which is exactly when a background download
  // is free.
  const [, dictionariesArrived] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    let live = true;
    const pending = LANGUAGES.map((option) => loadDictionary(option.code)).filter(Boolean);
    if (pending.length) {
      void Promise.all(pending).then(() => {
        if (live) dictionariesArrived();
      });
    }
    return () => {
      live = false;
    };
  }, []);

  return (
    <div>
      <Toolbar
        title="Language"
        hint="Every screen, button and message in the app is shown in the language picked here. Machine codes, part numbers and rupee figures stay as they are."
      />

      <div
        role="radiogroup"
        aria-label={t("App language")}
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
      >
        {LANGUAGES.map((option) => {
          const active = option.code === lang;
          return (
            <button
              key={option.code}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setLang(option.code as Language)}
              className={cn(
                "surface elev-1 flex flex-col gap-3 rounded-[var(--radius-card)] border p-4 text-left",
                "transition-[transform,box-shadow,border-color] duration-150",
                active
                  ? "border-[var(--color-brand)] shadow-[var(--shadow-2)]"
                  : "hover:-translate-y-0.5 hover:shadow-[var(--shadow-2)]",
              )}
            >
              <span className="flex items-start justify-between gap-2">
                <span className="min-w-0">
                  {/* The native name leads. Someone who reads only Tamil should
                      not have to recognise the word "Tamil" in Latin script to
                      find their own language. */}
                  <span className="display block text-[1.125rem] leading-tight font-bold">
                    {option.native}
                  </span>
                  <span className="text-3 mt-0.5 block text-xs">{t(option.label)}</span>
                </span>
                {active ? (
                  <span className="brand grid size-6 shrink-0 place-items-center rounded-full">
                    <Check className="size-3.5" />
                  </span>
                ) : null}
              </span>

              <span className="hairline" />

              <span className="flex flex-col gap-1">
                {PREVIEW_KEYS.map((key) => (
                  <span key={key} lang={option.code} className="text-2 truncate text-[0.8125rem]">
                    {translate(option.code as Language, key)}
                  </span>
                ))}
              </span>
            </button>
          );
        })}
      </div>

      <div className="surface-2 mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border px-4 py-3">
        <Badge variant="neutral">
          <Languages className="size-3" />
          {t("This device only")}
        </Badge>
        <p className="text-2 min-w-0 flex-1 text-[0.8125rem] leading-relaxed">
          {t(
            "The choice is remembered on this tablet or phone, not on the account — a shared bench tablet keeps the language of the people who use it.",
          )}
        </p>
      </div>
    </div>
  );
}
