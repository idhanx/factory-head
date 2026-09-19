"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, ChevronDown, CircleDashed, FileCheck2, Link2, RefreshCw, ShieldCheck, XCircle,
} from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { can } from "@/lib/roles";
import { formatInr, activeLocale } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, ErrorNote, Skeleton } from "@/components/ui/misc";
import type { AuditPack, Clause, ComplianceOverview } from "@/types/api";
import { useT } from "@/lib/i18n";

/**
 * Compliance / Audit AI (§3 Layer 5).
 *
 *   "Generates audit evidence (ISO 9001, IATF 16949, ZED, customer/OEM
 *    formats) automatically from data the plant already captures daily, and
 *    scores live audit readiness against the relevant clauses. A downtime log
 *    with corrective actions IS audit evidence."
 *
 * Every clause opens onto the actual rows that score it — the same rows, over
 * the same window. A clause marked as a gap never lists evidence, and a clause
 * marked ready always can show what it counted.
 */

const STANDARDS: Record<string, string> = {
  iso_9001: "ISO 9001",
  iatf_16949: "IATF 16949",
  zed: "ZED",
  customer: "Customer",
};

const STATUS = {
  ready: { label: "Ready", variant: "recovered", Icon: CheckCircle2 },
  partial: { label: "Partial", variant: "warn", Icon: CircleDashed },
  gap: { label: "Gap", variant: "stopped", Icon: XCircle },
  not_applicable: { label: "N/A", variant: "neutral", Icon: CircleDashed },
} as const;

export default function Compliance() {
  const t = useT();
  const session = loadSession();
  const queryClient = useQueryClient();
  const canPack = session ? can(session.roles, "owner", "quality", "consultant") : false;
  // Same roles as the evidence pack: scoring writes, so it is an explicit act.
  const canRescore = canPack;
  const [standard, setStandard] = useState<string>("all");

  const overview = useQuery({ queryKey: ["compliance"], queryFn: () => api.compliance() });
  const [pack, setPack] = useState<AuditPack | null>(null);

  // Opening the page shows the last stored scores; it no longer re-scores on
  // every read. Re-scoring is this button, and its answer replaces the cache.
  const rescore = useMutation({
    mutationFn: api.refreshCompliance,
    onSuccess: (data: ComplianceOverview) => {
      queryClient.setQueryData(["compliance"], data);
      void queryClient.invalidateQueries({ queryKey: ["clause-evidence"] });
    },
  });

  const generate = useMutation({
    mutationFn: (std: string) => {
      const end = new Date();
      const start = new Date(end.getTime() - 90 * 86_400_000);
      const iso = (d: Date) => d.toISOString().slice(0, 10);
      return api.generateAuditPack(std, iso(start), iso(end));
    },
    onSuccess: setPack,
  });

  const rescoreButton = canRescore ? (
    <Button size="lg" variant="outline" onClick={() => rescore.mutate()} disabled={rescore.isPending}>
      <RefreshCw className={cn("size-4", rescore.isPending && "animate-spin")} />
      {rescore.isPending ? t("Re-scoring…") : t("Re-score now")}
    </Button>
  ) : null;
  const rescoreError = rescore.error ? (
    <ErrorNote message={t("Could not re-score: {0}", { 0: rescore.error.message })} />
  ) : null;

  if (overview.isLoading) return <Skeleton className="h-[32rem]" />;
  if (overview.error) {
    return (
      <div className="space-y-4">
        <h1 className="display text-[1.75rem] font-bold">{t("Audit readiness")}</h1>
        <ErrorNote message={t("Could not load audit readiness: {0}", { 0: overview.error.message })} />
        <Button size="lg" variant="outline" onClick={() => void overview.refetch()}>
          {t("Try again")}
        </Button>
      </div>
    );
  }
  if (!overview.data) {
    return (
      <div className="space-y-4">
        <Empty
          title={t("Not scored yet")}
          hint={canRescore ? t("Score the plant's records against the clauses now.") : t("Ask the owner or quality lead to run the first score.")}
          action={rescoreButton}
        />
        {rescoreError}
      </div>
    );
  }
  const d = overview.data;

  const clauses = d.clauses.filter((c) => standard === "all" || c.standard === standard);
  const gapsFirst = [...clauses].sort(
    (a, b) => rankStatus(a.status) - rankStatus(b.status),
  );

  return (
    <div className="space-y-6">
      <header className="rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="label mb-1.5 flex items-center gap-1.5">
            <ShieldCheck className="size-3" />
            {t("Layer 5 · Compliance / Audit AI")}
          </p>
          <h1 className="display text-[1.75rem] font-bold">{t("Audit readiness")}</h1>
          <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
            {t("Scored from what the plant records every shift. No three-week scramble before the auditor arrives — the evidence has been accumulating all along.")}
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-6">
          {rescoreButton}
          <div className="text-right">
            <p className="label mb-1">{t("Overall, last 90 days")}</p>
            <span className="figure text-[2.75rem] font-bold leading-none">
              {d.overall_score}
              <span className="text-3 text-xl">%</span>
            </span>
            <p className="text-3 mt-1 text-xs">
              {t("{0} ready · {1} partial · {2} gap{3}", { 0: d.ready, 1: d.partial, 2: d.gaps, 3: d.gaps === 1 ? "" : "s" })}
            </p>
          </div>
        </div>
      </header>

      {rescoreError}
      {rescore.isSuccess ? (
        <p role="status" className="text-3 text-xs">{t("Re-scored just now from the latest records.")}</p>
      ) : null}

      {/* ---- per standard ------------------------------------------------ */}
      <div className="grid gap-3 sm:grid-cols-3">
        {Object.entries(d.by_standard).map(([key, score], index) => (
          <button
            key={key}
            type="button"
            onClick={() => setStandard(standard === key ? "all" : key)}
            aria-pressed={standard === key}
            style={{ animationDelay: `${index * 60}ms` }}
            className={cn(
              "rise surface elev-1 rounded-[var(--radius-card)] border p-4 text-left transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-[var(--shadow-2)]",
              standard === key && "ring-2 ring-[var(--color-brand)]",
            )}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-bold">{STANDARDS[key] ?? key}</span>
              <span className="figure text-2xl font-bold">{score}%</span>
            </div>
            <div className="surface-inset mt-3 h-2 overflow-hidden rounded-full">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${score}%`,
                  background:
                    score >= 80
                      ? "var(--color-running)"
                      : score >= 50
                        ? "var(--color-warn)"
                        : "var(--color-stopped)",
                }}
              />
            </div>
            <p className="text-3 mt-2 text-xs">
              {standard === key ? t("Showing only this standard") : t("Tap to filter")}
            </p>
          </button>
        ))}
      </div>

      {/* ---- evidence pack ------------------------------------------------ */}
      {canPack ? (
        <Card className="rise" style={{ animationDelay: "180ms" }}>
          <CardContent className="flex flex-wrap items-center gap-3 pt-5">
            <FileCheck2 className="size-5 text-[var(--color-accent)]" />
            <div className="min-w-0 flex-1">
              <p className="font-semibold">{t("Freeze an evidence pack")}</p>
              <p className="text-3 text-xs">
                {t("Frozen on purpose — you can always show an auditor exactly what was handed over on a given date, not a live view that has since moved on.")}
              </p>
            </div>
            {Object.keys(d.by_standard).map((key) => (
              <Button
                key={key}
                size="sm"
                variant="outline"
                disabled={generate.isPending}
                onClick={() => generate.mutate(key)}
              >
                {STANDARDS[key] ?? key}
              </Button>
            ))}
          </CardContent>
          {generate.error ? (
            <CardContent>
              <ErrorNote message={(generate.error as Error).message} />
            </CardContent>
          ) : null}
          {pack ? (
            <CardContent>
              <div className="surface-2 rise rounded-xl border px-4 py-3 text-sm" role="status">
                <span className="font-semibold">{STANDARDS[pack.standard] ?? pack.standard}</span>{" "}
                {t("pack frozen for")}{" "}{pack.period_start} → {pack.period_end}{t(": readiness")}{" "}
                <span className="font-semibold">{pack.readiness_score}%</span>, {pack.clause_count}{" "}
                {t("clauses,")}{" "}{pack.evidence_rows} {" "}{t("evidence records.")}
              </div>
            </CardContent>
          ) : null}
        </Card>
      ) : null}

      {/* ---- clauses ------------------------------------------------------ */}
      <section>
        <div className="mb-2.5 flex items-center gap-3">
          <h2 className="label">
            {t("Clauses {0}", { 0: standard !== "all" ? `· ${STANDARDS[standard] ?? standard}` : "" })}
          </h2>
          <span className="hairline flex-1" />
          <span className="text-3 text-xs">{t("gaps first")}</span>
        </div>
        <div className="space-y-2">
          {gapsFirst.map((clause, index) => (
            <ClauseRow key={clause.id} clause={clause} index={index} />
          ))}
        </div>
      </section>
    </div>
  );
}

function rankStatus(status: Clause["status"]) {
  return { gap: 0, partial: 1, ready: 2, not_applicable: 3 }[status];
}

function ClauseRow({ clause, index }: { clause: Clause; index: number }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const status = STATUS[clause.status];
  const evidence = useQuery({
    queryKey: ["clause-evidence", clause.id],
    queryFn: () => api.clauseEvidence(clause.id),
    enabled: open,
  });

  return (
    <Card
      elevation={1}
      className="rise overflow-hidden"
      style={{ animationDelay: `${Math.min(index * 30, 300)}ms` }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-3.5 text-left"
      >
        <status.Icon
          className={cn(
            "mt-0.5 size-4 shrink-0",
            clause.status === "ready"
              ? "text-[var(--color-running)]"
              : clause.status === "partial"
                ? "text-[var(--color-warn)]"
                : clause.status === "gap"
                  ? "text-[var(--color-stopped)]"
                  : "text-3",
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-3 text-xs font-semibold">
              {STANDARDS[clause.standard] ?? clause.standard} {clause.clause_ref}
            </span>
            <Badge variant={status.variant}>{t(status.label)}</Badge>
            <span className="text-3 tnum text-xs">
              {t("{0} record{1}{2}", { 0: clause.evidence_count.toLocaleString("en-IN"), 1: clause.evidence_count === 1 ? "" : "s", 2: clause.last_evidence_at
                ? t(" · latest {0}", { 0: new Date(clause.last_evidence_at).toLocaleDateString(activeLocale(), {
                    day: "numeric",
                    month: "short",
                  }) })
                : "" })}
            </span>
          </div>
          <p className="mt-0.5 font-semibold leading-snug">{clause.title}</p>
          {clause.gap_note && clause.status !== "ready" ? (
            <p className="mt-1 text-xs leading-relaxed text-[var(--color-accent)]">
              {clause.gap_note}
            </p>
          ) : null}
        </div>
        <ChevronDown
          className={cn("text-3 mt-1 size-4 shrink-0 transition-transform", open && "rotate-180")}
        />
      </button>

      {open ? (
        <div className="surface-2 border-t px-4 py-3.5">
          {clause.requirement ? (
            <p className="text-2 mb-3 text-xs leading-relaxed">
              <span className="font-semibold text-[var(--text)]">{t("Requirement:")}</span>{" "}
              {clause.requirement}
            </p>
          ) : null}
          {clause.also_satisfies.length ? (
            <div className="mb-3 flex flex-wrap items-center gap-1.5">
              <Link2 className="text-3 size-3.5" />
              <span className="text-3 text-xs">{t("Same evidence also satisfies")}</span>
              {clause.also_satisfies.map((ref) => {
                const [std, cl] = ref.split(":");
                return (
                  <Badge key={ref} variant="neutral">
                    {STANDARDS[std] ?? std} {cl}
                  </Badge>
                );
              })}
            </div>
          ) : null}
          {evidence.isLoading ? (
            <Skeleton className="h-24" />
          ) : evidence.error ? (
            <ErrorNote message={t("Could not load the evidence: {0}", { 0: evidence.error.message })} />
          ) : (
            <EvidenceTable rows={evidence.data?.evidence ?? []} status={clause.status} />
          )}
        </div>
      ) : null}
    </Card>
  );
}

function EvidenceTable({
  rows,
  status,
}: {
  rows: Array<Record<string, unknown>>;
  status: Clause["status"];
}) {
  const t = useT();
  if (rows.length === 0) {
    return (
      <p className="text-3 py-4 text-center text-xs">
        {status === "gap"
          ? t("Nothing to show an auditor yet — the note above says what to start recording.")
          : t("This clause is evidenced by its record count; open the related screen for detail.")}
      </p>
    );
  }

  const columns = Object.keys(rows[0]).filter((key) => key !== "five_whys");

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b">
            {columns.map((col) => (
              <th key={col} className="label py-1.5 pr-3 text-left whitespace-nowrap">
                {col.replace(/_paise$/, "").replace(/_/g, " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b last:border-0">
              {columns.map((col) => (
                <td key={col} className="py-1.5 pr-3 align-top">
                  {renderCell(col, row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length >= 25 ? (
        <p className="text-3 mt-2 text-xs">{t("Showing the latest 25 records.")}</p>
      ) : null}
    </div>
  );
}

function renderCell(column: string, value: unknown): React.ReactNode {
  if (value === null || value === undefined || value === "") return <span className="text-3">—</span>;
  if (column.endsWith("_paise") && typeof value === "number") {
    return <span className="money">{formatInr(value)}</span>;
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    return value
      .map((item) =>
        typeof item === "object" && item
          ? Object.values(item as Record<string, unknown>).join(" × ")
          : String(item),
      )
      .join(", ");
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
