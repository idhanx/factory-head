/**
 * The languages the plant floor actually reads.
 *
 * English is the source language: every message in the code is written in it,
 * and the dictionaries are keyed by the English sentence itself rather than by
 * an invented `settings.tab.users` identifier. That is a deliberate trade. A
 * key-based catalogue drifts — the key stays while the English under it is
 * reworded, and nobody notices the Tamil now says something else. Keyed by the
 * sentence, a reworded message misses the dictionary and falls back to English,
 * which is visible, honest and fixable.
 *
 * `native` is what the option calls itself. A Tamil-first operator should not
 * have to find their language spelled in a script they do not read.
 */
export const LANGUAGES = [
  { code: "en", label: "English", native: "English" },
  { code: "ta", label: "Tamil", native: "தமிழ்" },
  { code: "hi", label: "Hindi", native: "हिन्दी" },
] as const;

export type Language = (typeof LANGUAGES)[number]["code"];

export const DEFAULT_LANGUAGE: Language = "en";

/** Per device, not per user: a bench tablet is shared, the language is not. */
export const LANGUAGE_STORAGE_KEY = "fm.lang";

export function isLanguage(value: unknown): value is Language {
  return typeof value === "string" && LANGUAGES.some((l) => l.code === value);
}

/**
 * The locale each language formats dates and numbers in.
 *
 * All three stay on the Indian locale: a Tamil-reading supervisor still wants
 * ₹12,34,567 grouped the Indian way and the same 24-hour clock the shift board
 * uses. Only the script of the month name changes.
 */
export const LOCALES: Record<Language, string> = {
  en: "en-IN",
  ta: "ta-IN",
  hi: "hi-IN",
};
