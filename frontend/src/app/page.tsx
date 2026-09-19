"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { loadSession } from "@/lib/api";
import { homeFor } from "@/lib/roles";
import { useT } from "@/lib/i18n";

/**
 * Role-based routing (§7's central design decision).
 *
 * "Home screens are role-based, and the owner's home is money, not machines.
 *  The supervisor's home stays the machine grid, because he is running a
 *  shift, not reading a P&L. Same data, different doors."
 */
export default function Index() {
  const t = useT();
  const router = useRouter();

  useEffect(() => {
    const session = loadSession();
    router.replace(session ? homeFor(session.roles) : "/login");
  }, [router]);

  return (
    <main className="grid min-h-dvh place-items-center">
      <p className="text-sm text-2">{t("Loading…")}</p>
    </main>
  );
}
