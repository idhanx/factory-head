"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, FileUp, ListChecks } from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { ADMINS, can } from "@/lib/roles";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { ErrorNote, Skeleton } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import type { ReadinessResult, UploadConfirmResult, UploadPreview } from "@/types/api";
import { useT } from "@/lib/i18n";

type Template = Awaited<ReturnType<typeof api.templates>>[number];

/**
 * §4.1 — onboarding. "Never type what can be uploaded, never demand what can
 * be estimated."
 *
 * Onboarding is the single biggest adoption risk in this product: three days of
 * manual typing per plant does not scale. So this page is two things — the
 * readiness questionnaire (answered before we ever visit) and the seven
 * templates, with T1 and T2 marked as the go-live minimum.
 */
export default function Onboarding() {
  const t = useT();
  const templates = useQuery({ queryKey: ["templates"], queryFn: api.templates });
  const questions = useQuery({ queryKey: ["readiness-q"], queryFn: api.readinessQuestions });
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const [plantName, setPlantName] = useState("");
  const [result, setResult] = useState<ReadinessResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [uploading, setUploading] = useState<Template | null>(null);
  const session = loadSession();
  const isAdmin = session ? can(session.roles, ...ADMINS) : false;

  async function score() {
    if (!plantName.trim()) {
      setScoreError(t("Enter the plant's name so the score is filed against it."));
      return;
    }
    setBusy(true);
    setScoreError(null);
    try {
      setResult(await api.submitReadiness({ plant_name: plantName.trim(), answers }));
    } catch (e) {
      setScoreError(t("Could not score readiness: {0}", { 0: (e as Error).message }));
    } finally {
      setBusy(false);
    }
  }

  const sections = groupBy(questions.data ?? [], (q) => q.section);

  return (
    <div className="space-y-8">
      <header className="rise">
        <p className="label mb-1.5">{t("§4.1 · upload first, type last")}</p>
        <h1 className="display text-[1.75rem] font-bold">{t("Onboarding")}</h1>
        <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
          {t("Upload first, type last. A plant that hands over T1 and T2 can go live the same day.")}
        </p>
      </header>

      {isAdmin ? <DefaultCodes /> : null}

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-2">
          {t("The template pack")}
        </h2>
        {templates.isLoading ? (
          <Skeleton className="h-48" />
        ) : templates.error ? (
          <ErrorNote message={t("Could not load the template list: {0}", { 0: templates.error.message })} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(templates.data ?? []).map((template) => (
              <Card key={template.code}>
                <CardHeader className="flex items-center justify-between pb-2">
                  <CardTitle>{template.code}</CardTitle>
                  {template.go_live_minimum ? (
                    <Badge variant="accent">{t("Go-live minimum")}</Badge>
                  ) : null}
                </CardHeader>
                <CardContent>
                  <p className="text-sm font-medium">{template.name}</p>
                  <p className="mt-1 text-xs text-2">
                    {t("{0} required · {1} optional", { 0: template.required_columns.length, 1: template.optional_columns.length })}
                  </p>
                  <a
                    href={`/api/v1/onboarding/templates/${template.code}/blank`}
                    className="mt-3 inline-flex min-h-11 items-center text-xs text-[var(--color-accent)] hover:underline"
                  >
                    {t("Download blank →")}
                  </a>
                  <Button
                    size="lg"
                    variant="outline"
                    className="mt-1 w-full"
                    onClick={() => setUploading(template)}
                  >
                    <FileUp className="size-4" />
                    {t("Upload filled file")}
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
        <p className="mt-3 text-xs text-2">
          {t("Each template ships with a worked example row, because the commonest onboarding failure is a plant sending their own spreadsheet layout and someone spending a day reshaping it.")}
        </p>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-2">
          {t("Digital readiness")}
        </h2>

        {result ? (
          <Card className="mb-4">
            <CardContent className="pt-5">
              <div className="flex flex-wrap items-baseline gap-3">
                <span className="figure text-[3.5rem] font-bold leading-none">
                  {result.score}
                </span>
                <span className="text-3 text-lg">/ 100</span>
                <Badge variant="accent">{result.band}</Badge>
              </div>
              <p className="mt-3 text-sm">{result.message}</p>
              {result.priority_gaps.length ? (
                <div className="mt-4">
                  <p className="label mb-2">
                    {t("Priority gaps")}
                  </p>
                  <ul className="space-y-1 text-sm">
                    {result.priority_gaps.map((gap) => (
                      <li key={gap} className="flex gap-2">
                        <span className="text-2">·</span>
                        {gap}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <p className="mt-4 text-xs text-2">
                {t("A low score is not a reason not to start — it means there is more to recover. It sets the onboarding effort, nothing else.")}
              </p>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardContent className="space-y-5 pt-5">
            <div className="max-w-md">
              <Label htmlFor="readiness-plant">{t("Plant name")}</Label>
              <Input
                id="readiness-plant"
                value={plantName}
                onChange={(e) => setPlantName(e.target.value)}
                placeholder={t("Sri Balaji Precision Components, Hosur")}
              />
            </div>
            {questions.isLoading ? <Skeleton className="h-40" /> : null}
            {questions.error ? (
              <ErrorNote message={t("Could not load the questions: {0}", { 0: questions.error.message })} />
            ) : null}
            {Object.entries(sections).map(([section, items]) => (
              <div key={section}>
                <p className="label mb-2">
                  {section}
                </p>
                <div className="space-y-1.5">
                  {items.map((question) => (
                    <label
                      key={question.key}
                      className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 hover:surface-2"
                    >
                      <input
                        type="checkbox"
                        checked={answers[question.key] ?? false}
                        onChange={(e) =>
                          setAnswers((prev) => ({ ...prev, [question.key]: e.target.checked }))
                        }
                        className="size-5"
                      />
                      <span className="text-sm">{question.prompt}</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
            {scoreError ? <ErrorNote message={scoreError} /> : null}
            <Button size="lg" onClick={() => void score()} disabled={busy}>
              {busy ? t("Scoring…") : t("Score readiness")}
            </Button>
          </CardContent>
        </Card>
      </section>

      {uploading ? <UploadSheet template={uploading} onClose={() => setUploading(null)} /> : null}
    </div>
  );
}

/**
 * The standard T6 library in one tap. A plant with no reason codes cannot log
 * a single stop, and most plants' reasons are the same thirty — so start from
 * those and edit, rather than typing them. Codes the plant already has are
 * left alone, so pressing it twice is harmless.
 */
function DefaultCodes() {
  const t = useT();
  const queryClient = useQueryClient();
  const load = useMutation({
    mutationFn: api.loadOnboardingDefaults,
    onSuccess: () => {
      for (const key of [["reasons"], ["defects"], ["settings", "reason-codes"], ["settings", "defect-codes"]]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });

  return (
    <Card>
      <CardHeader className="flex-wrap justify-between">
        <CardTitle className="flex items-center gap-1.5">
          <ListChecks className="size-3.5" />
          {t("Standard reason and defect codes")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-2 max-w-2xl text-sm leading-relaxed">
          {t("Loads the default downtime reasons and defect codes (template T6) so the floor can start logging today. Codes this plant already has are skipped; edit or retire any of them later in Settings.")}
        </p>
        {load.data ? (
          <p role="status" className="surface-2 rise flex items-start gap-2 rounded-xl border px-4 py-3 text-sm">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[var(--color-running)]" />
            <span>
              {t("Added {0} reason code{1} and {2} defect code{3}.{4}", { 0: load.data.reason_codes_added, 1: load.data.reason_codes_added === 1 ? "" : "s", 2: load.data.defect_codes_added, 3: load.data.defect_codes_added === 1 ? "" : "s", 4: load.data.reason_codes_added + load.data.defect_codes_added === 0
                ? " This plant already had all of them."
                : "" })}
            </span>
          </p>
        ) : null}
        {load.error ? <ErrorNote message={t("Could not load the default codes: {0}", { 0: load.error.message })} /> : null}
        <Button size="lg" variant="outline" disabled={load.isPending} onClick={() => load.mutate()}>
          {load.isPending ? t("Loading codes…") : t("Load default reason & defect codes")}
        </Button>
      </CardContent>
    </Card>
  );
}

/**
 * Upload → preview → confirm. Nothing reaches the masters until Confirm, and
 * Confirm stays shut while any row has an error: a half-imported part list is
 * worse than none, because nobody knows which half is missing.
 */
function UploadSheet({ template, onClose }: { template: Template; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [done, setDone] = useState<UploadConfirmResult | null>(null);

  const upload = useMutation({
    mutationFn: (chosen: File) => api.uploadTemplate(template.code, chosen),
    onSuccess: setPreview,
  });

  const confirm = useMutation({
    mutationFn: (uploadId: string) => api.confirmUpload(uploadId),
    onSuccess: (result) => {
      setDone(result);
      // Imported rows feed every picker in the app; stale lists would hide them.
      void queryClient.invalidateQueries();
    },
  });

  const reset = () => {
    setFile(null);
    setPreview(null);
    setDone(null);
    upload.reset();
    confirm.reset();
  };

  const hasErrors = (preview?.errors.length ?? 0) > 0;
  const inputId = `upload-${template.code}`;

  return (
    <Sheet
      title={t("Upload {0}", { 0: template.code })}
      subtitle={template.name}
      onClose={onClose}
      className="max-w-4xl"
    >
      {done ? (
        <div className="space-y-4">
          <div role="status" className="surface-2 rise flex items-start gap-3 rounded-xl border p-4">
            <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-[var(--color-running)]" />
            <div>
              <p className="font-semibold">
                {t("Imported {0} row{1} from {2}.", { 0: done.imported, 1: done.imported === 1 ? "" : "s", 2: done.template })}
              </p>
              <p className="text-2 mt-0.5 text-sm">
                {t("They are live now — in the pickers, on the grid and in Settings.")}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="lg" onClick={onClose}>{t("Done")}</Button>
            <Button size="lg" variant="outline" onClick={reset}>{t("Upload another file")}</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (file) upload.mutate(file);
            }}
          >
            <div>
              <Label htmlFor={inputId}>{t("Filled-in {0} file (.xlsx)", { 0: template.code })}</Label>
              <input
                id={inputId}
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setPreview(null);
                  upload.reset();
                  confirm.reset();
                }}
                className="text-2 block w-full text-sm file:mr-3 file:h-11 file:cursor-pointer file:rounded-full file:border file:border-[var(--border)] file:bg-[var(--surface-2)] file:px-4 file:text-sm file:font-medium file:text-[var(--text)]"
              />
              <p className="text-3 mt-1 text-xs">
                {t("Required columns: {0}", { 0: template.required_columns.join(", ") })}
              </p>
            </div>
            <Button type="submit" size="lg" disabled={!file || upload.isPending}>
              {upload.isPending ? t("Reading file…") : t("Upload and preview")}
            </Button>
          </form>

          {upload.error ? <ErrorNote message={t("Could not read the file: {0}", { 0: upload.error.message })} /> : null}

          {preview ? (
            <div className="space-y-4">
              <div className="surface-2 rounded-xl border px-4 py-3">
                <p className="font-semibold">{preview.summary}</p>
                <p className="text-3 mt-0.5 text-xs">
                  {t("{0} · {1} row{2} found{3}", { 0: preview.filename, 1: preview.rows_found, 2: preview.rows_found === 1 ? "" : "s", 3: hasErrors ? t(" · {0} error{1}", { 0: preview.errors.length, 1: preview.errors.length === 1 ? "" : "s" }) : "" })}
                </p>
              </div>

              {hasErrors ? (
                <div>
                  <p className="label mb-2 text-[var(--color-loss)]">{t("Fix these in the file, then upload again")}</p>
                  <div className="max-h-64 overflow-auto rounded-xl border">
                    <table className="w-full text-sm">
                      <thead className="surface-2 sticky top-0">
                        <tr className="border-b">
                          {["Row", "Column", "Problem"].map((h) => (
                            <th key={h} className="label px-3 py-2 text-left whitespace-nowrap">{t(h)}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.errors.map((err, i) => (
                          <tr key={`${err.row}-${err.column}-${i}`} className="border-b last:border-0">
                            <td className="tnum px-3 py-2 font-semibold">{err.row}</td>
                            <td className="px-3 py-2 whitespace-nowrap">{err.column}</td>
                            <td className="px-3 py-2 text-[var(--color-loss)]">{err.message}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}

              {preview.preview.length ? (
                <div>
                  <p className="label mb-2">
                    {t("First {0} rows", { 0: Math.min(10, preview.preview.length) })}
                  </p>
                  <div className="overflow-x-auto rounded-xl border">
                    <table className="w-full text-xs">
                      <thead className="surface-2">
                        <tr className="border-b">
                          {preview.columns.map((col) => (
                            <th key={col} className="label px-3 py-2 text-left whitespace-nowrap">{col}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.preview.slice(0, 10).map((row, i) => (
                          <tr key={i} className="border-b last:border-0">
                            {preview.columns.map((col) => (
                              <td key={col} className="px-3 py-2 whitespace-nowrap">
                                {row[col] === null || row[col] === undefined || row[col] === "" ? (
                                  <span className="text-3">—</span>
                                ) : (
                                  String(row[col])
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}

              {confirm.error ? <ErrorNote message={t("Could not import: {0}", { 0: confirm.error.message })} /> : null}

              <div className="flex flex-wrap items-center gap-3">
                <Button
                  size="touch"
                  disabled={hasErrors || !preview.upload_id || confirm.isPending}
                  onClick={() => preview.upload_id && confirm.mutate(preview.upload_id)}
                >
                  {confirm.isPending ? t("Importing…") : t("Confirm and import {0} rows", { 0: preview.rows_found })}
                </Button>
                {hasErrors ? (
                  <span className="text-3 text-xs">{t("Confirm opens once the file has no errors.")}</span>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </Sheet>
  );
}

function groupBy<T>(items: T[], key: (item: T) => string): Record<string, T[]> {
  return items.reduce<Record<string, T[]>>((acc, item) => {
    const k = key(item);
    (acc[k] ??= []).push(item);
    return acc;
  }, {});
}
