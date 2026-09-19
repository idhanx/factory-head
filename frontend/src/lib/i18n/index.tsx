"use client";

import * as React from "react";
import {
  DEFAULT_LANGUAGE, isLanguage, LANGUAGE_STORAGE_KEY, LOCALES, type Language,
} from "./languages";
import { setFormatLanguage } from "@/lib/format";

/**
 * Translation at the presentation edge.
 *
 * Every visible string in the app goes through `t()`. The English text *is* the
 * lookup key, so a message with no entry yet renders in English instead of
 * rendering `settings.language.title` at a supervisor — a missing translation
 * should degrade to something readable, never to debug output.
 *
 * What is deliberately *not* translated: money, counts, clock times and machine
 * codes. Those come from the kernel already formatted (`app/kernel/money.py`),
 * and a rupee figure re-rendered in Tamil digits would stop matching the paper
 * it is reconciled against.
 */

/**
 * `null` and `undefined` are allowed because these values come straight out of
 * JSX, where `{maybeNull}` renders as nothing. A placeholder fed a null must do
 * the same rather than printing the word "null" into a supervisor's screen.
 */
export type Vars = Record<string, string | number | null | undefined>;

/**
 * Dictionaries are fetched, not bundled.
 *
 * Together they are about 200 KB of text, and statically importing them put all
 * of it into the first load of every screen — for every plant, including the
 * ones running in English. §7's brief is a tablet on a weak plant network, so
 * each language is a separate chunk and a plant only ever downloads its own.
 *
 * The download starts when this module is evaluated rather than when the
 * provider mounts, so it is already in flight while React hydrates and has
 * normally landed by the first paint.
 */
const LOADERS: Record<Exclude<Language, "en">, () => Promise<Record<string, string>>> = {
  ta: () => import("./dictionaries/ta").then((m) => m.ta),
  hi: () => import("./dictionaries/hi").then((m) => m.hi),
};

const DICTIONARIES: Partial<Record<Language, Record<string, string>>> = { en: {} };
const inFlight: Partial<Record<Language, Promise<void>>> = {};

/** Resolves once `lang` can be translated. `undefined` means it already can. */
export function loadDictionary(lang: Language): Promise<void> | undefined {
  if (lang === "en" || DICTIONARIES[lang]) return undefined;
  inFlight[lang] ??= LOADERS[lang]()
    .then((dictionary) => {
      DICTIONARIES[lang] = dictionary;
    })
    .catch(() => {
      // A chunk that will not load must not leave the app blank: mark the
      // language as empty and every lookup falls through to English.
      DICTIONARIES[lang] = {};
    });
  return inFlight[lang];
}

/** `{name}` and `{0}` placeholders. Unknown names are left as written. */
function interpolate(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => {
    if (!(key in vars)) return whole;
    const value = vars[key];
    return value === null || value === undefined ? "" : String(value);
  });
}

export function translate(lang: Language, source: string, vars?: Vars): string {
  // Not loaded yet (or failed): render the English source rather than nothing.
  const dictionary = DICTIONARIES[lang] ?? {};
  const hit = dictionary[source];
  if (hit !== undefined) return interpolate(hit, vars);

  // A message that only differs by surrounding whitespace is the same message.
  const trimmed = source.trim();
  if (trimmed !== source) {
    const near = dictionary[trimmed];
    if (near !== undefined) return interpolate(near, vars);
  }

  return interpolate(source, vars);
}

export type Translator = (source: string, vars?: Vars) => string;

type LanguageValue = {
  lang: Language;
  setLang: (next: Language) => void;
  t: Translator;
  locale: string;
  /** True once the stored choice has been applied, so nothing flashes twice. */
  ready: boolean;
};

const LanguageContext = React.createContext<LanguageValue | null>(null);

function readStored(): Language {
  try {
    const raw = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (isLanguage(raw)) return raw;
  } catch {
    /* storage blocked (private window, locked-down tablet): stay on English */
  }
  return DEFAULT_LANGUAGE;
}

// Kick the stored language's chunk off now, in parallel with hydration.
if (typeof window !== "undefined") void loadDictionary(readStored());

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  // The server has no localStorage, so the first client render must match the
  // English HTML it sent or React discards the whole tree. The stored choice is
  // applied in a layout effect instead: that runs after hydration but before
  // the browser paints, so the switch is never seen.
  const [lang, setLangState] = React.useState<Language>(DEFAULT_LANGUAGE);
  const [ready, setReady] = React.useState(false);
  // Bumped when a dictionary finishes downloading, so the tree re-renders with
  // it. The dictionary itself lives in a module map, not in state — it is
  // immutable once loaded and shared by every provider on the page.
  const [dictionaryVersion, dictionaryArrived] = React.useReducer((n: number) => n + 1, 0);

  React.useLayoutEffect(() => {
    const stored = readStored();
    if (stored !== DEFAULT_LANGUAGE) setLangState(stored);
    setReady(true);
  }, []);

  React.useEffect(() => {
    void loadDictionary(lang)?.then(dictionaryArrived);
  }, [lang]);

  // Screen readers, hyphenation and the font stack all key off `lang`.
  React.useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  // A tablet with the app open in two tabs should not disagree with itself.
  React.useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== LANGUAGE_STORAGE_KEY) return;
      if (isLanguage(event.newValue)) setLangState(event.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setLang = React.useCallback((next: Language) => {
    setLangState(next);
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
    } catch {
      /* the choice still applies to this session, it just will not survive it */
    }
  }, []);

  const value = React.useMemo<LanguageValue>(() => {
    const t: Translator = (source, vars) => translate(lang, source, vars);
    // `lib/format.ts` is called from plain functions that cannot read context,
    // so it is handed the language here — during render, before any child of
    // this provider formats a date or a duration with it.
    setFormatLanguage(LOCALES[lang], t);
    return {
      lang,
      setLang,
      ready,
      locale: LOCALES[lang],
      t,
    };
    // `dictionaryVersion` is a dependency rather than a value: a new object
    // here is what tells every `useContext` consumer to re-render with the
    // dictionary that just landed.
  }, [lang, setLang, ready, dictionaryVersion]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageValue {
  const value = React.useContext(LanguageContext);
  if (!value) {
    throw new Error("useLanguage must be used inside <LanguageProvider>");
  }
  return value;
}

/** The hook screens use: `const t = useT()`, then `t("Save")`. */
export function useT(): Translator {
  return useLanguage().t;
}

export { LANGUAGES, DEFAULT_LANGUAGE, type Language } from "./languages";
