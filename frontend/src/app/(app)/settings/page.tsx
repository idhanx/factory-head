"use client";

import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/misc";
import {
  ALERT_RECIPIENTS, DEFECT_CODES, MasterTab, OPERATORS, PARTS, REASON_CODES, SHIFTS, STATIONS,
} from "@/components/settings/masters";
import { UsersTab } from "@/components/settings/users";
import { PlantTab } from "@/components/settings/plant";
import { CostRatesTab } from "@/components/settings/cost-rates";
import { JobOrdersTab } from "@/components/settings/job-orders";
import { HeldEntriesTab } from "@/components/settings/held-entries";
import { NotificationsTab } from "@/components/settings/notifications";
import { LanguageTab } from "@/components/settings/language";
import { useT } from "@/lib/i18n";

/**
 * Settings — the plant's own data, editable without a developer.
 *
 * Onboarding loads it once from the templates; this is where it is kept right
 * afterwards: a new machine, a new customer part, a supervisor who joined, a
 * rate that changed. Owner, consultant and admin only — the API enforces the
 * same line.
 */

const TABS = [
  { key: "language", label: "Language" },
  { key: "users", label: "Users" },
  { key: "plant", label: "Plant" },
  { key: "stations", label: "Stations" },
  { key: "parts", label: "Parts" },
  { key: "reason-codes", label: "Reason codes" },
  { key: "defect-codes", label: "Defect codes" },
  { key: "operators", label: "Operators" },
  { key: "shifts", label: "Shifts" },
  { key: "alert-recipients", label: "Alert recipients" },
  { key: "cost-rates", label: "Cost rates" },
  { key: "job-orders", label: "Job orders" },
  { key: "held-entries", label: "Held entries" },
  { key: "notifications", label: "Notifications" },
] as const;

type Tab = (typeof TABS)[number]["key"];

export default function SettingsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <Settings />
    </Suspense>
  );
}

function Settings() {
  const t = useT();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const requested = params.get("tab");
  const tab: Tab = TABS.some((item) => item.key === requested) ? (requested as Tab) : "users";

  return (
    <div className="space-y-6">
      <header className="rise">
        <p className="label mb-1.5 flex items-center gap-1.5">
          <SlidersHorizontal className="size-3" />
          {t("Plant setup")}
        </p>
        <h1 className="display text-[1.75rem] font-bold">{t("Settings")}</h1>
        <p className="text-2 mt-1 max-w-2xl text-sm leading-relaxed">
          {t("People, machines, parts and codes. Nothing here is ever deleted — retire a row and everything already recorded against it keeps its meaning.")}
        </p>
      </header>

      <div className="surface-2 flex w-fit max-w-full gap-0.5 overflow-x-auto rounded-xl border p-1" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => router.replace(`${pathname}?tab=${item.key}`, { scroll: false })}
            className={cn(
              "min-h-11 rounded-lg px-3.5 text-[0.8125rem] font-medium whitespace-nowrap transition-colors",
              tab === item.key ? "brand font-semibold" : "text-2 hover:text-[var(--text)]",
            )}
          >
            {t(item.label)}
          </button>
        ))}
      </div>

      <section role="tabpanel" aria-label={t(TABS.find((item) => item.key === tab)?.label ?? "Settings")}>
        {tab === "language" ? <LanguageTab /> : null}
        {tab === "users" ? <UsersTab /> : null}
        {tab === "plant" ? <PlantTab /> : null}
        {tab === "stations" ? <MasterTab key="stations" config={STATIONS} /> : null}
        {tab === "parts" ? <MasterTab key="parts" config={PARTS} /> : null}
        {tab === "reason-codes" ? <MasterTab key="reason-codes" config={REASON_CODES} /> : null}
        {tab === "defect-codes" ? <MasterTab key="defect-codes" config={DEFECT_CODES} /> : null}
        {tab === "operators" ? <MasterTab key="operators" config={OPERATORS} /> : null}
        {tab === "shifts" ? <MasterTab key="shifts" config={SHIFTS} /> : null}
        {tab === "alert-recipients" ? <MasterTab key="alert-recipients" config={ALERT_RECIPIENTS} /> : null}
        {tab === "cost-rates" ? <CostRatesTab /> : null}
        {tab === "job-orders" ? <JobOrdersTab /> : null}
        {tab === "held-entries" ? <HeldEntriesTab /> : null}
        {tab === "notifications" ? <NotificationsTab /> : null}
      </section>
    </div>
  );
}
