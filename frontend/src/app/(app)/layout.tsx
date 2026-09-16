"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity, BarChart3, BookOpen, Boxes, ClipboardCheck, ClipboardList, GraduationCap, History, LayoutGrid,
  Lightbulb, LogOut, Menu, MessageSquare, Presentation, Search, Settings2,
  ShieldCheck, SlidersHorizontal, Sparkles, TrendingUp,
} from "lucide-react";
import { clearSession, loadSession } from "@/lib/api";
import { NAV, can, canReach, homeFor } from "@/lib/roles";
import { clearApiCache, disablePush } from "@/lib/offline/push";
import { SyncPill, useSyncAgent } from "@/components/sync-status";
import { PushToggle } from "@/components/push-toggle";
import type { Session } from "@/types/api";
import { cn } from "@/lib/utils";

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  "/floor": LayoutGrid,
  "/shift": Activity,
  "/shifts": History,
  "/quality": ClipboardCheck,
  "/meeting": Presentation,
  "/actions": ClipboardList,
  "/memory": BookOpen,
  "/coach": Sparkles,
  "/copilot": MessageSquare,
  "/owner": TrendingUp,
  "/onboarding": Settings2,
  "/kaizen": Lightbulb,
  "/skills": GraduationCap,
  "/compliance": ShieldCheck,
  "/lots": Boxes,
  "/kpis": BarChart3,
  "/settings": SlidersHorizontal,
};

/* The rail is grouped the way the work is: capture what happened, improve on
   it, then see what it was worth. Those are the three verbs of the product,
   and a flat list of links hides them.

   The Layer 5 plugins get their own group rather than being folded into
   Improve: §3 builds them as a parallel stream on the same Core data, and an
   owner asking "where is the audit readiness?" should find it by name. */
const GROUPS = [
  { key: "capture", label: "Shop floor" },
  { key: "improve", label: "Improve" },
  { key: "plugins", label: "Plugins" },
  { key: "money", label: "Business" },
] as const;

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  // Entries made on this tablet keep flowing to the server from every screen,
  // not only from the one they were entered on.
  useSyncAgent();

  useEffect(() => {
    const current = loadSession();
    if (!current) {
      router.replace("/login");
      return;
    }
    setSession(current);
    setReady(true);
  }, [router]);

  // Hiding a link is not a lock. A supervisor who types /owner into the
  // address bar is sent to their own home rather than a page of 403s.
  const allowed = session ? canReach(session.roles, pathname) : true;
  const home = session ? homeFor(session.roles) : "/login";
  useEffect(() => {
    if (!allowed && home !== pathname) router.replace(home);
  }, [allowed, home, pathname, router]);

  // A drawer that survives navigation is a drawer in the way.
  useEffect(() => setNavOpen(false), [pathname]);

  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setNavOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen]);

  // Waiting on the redirect renders the same loading state — never a flash of
  // the page the role cannot use. (A home that is itself off-limits would loop,
  // so that one case falls through to the page and its API's own 403.)
  if (!ready || !session || (!allowed && home !== pathname)) {
    return (
      <div className="grid min-h-dvh place-items-center">
        <div className="flex items-center gap-2.5">
          <span
            className="led"
            style={{ background: "var(--color-brand)", color: "var(--color-brand)" }}
          />
          <span className="text-2 text-sm">Loading…</span>
        </div>
      </div>
    );
  }

  const items = NAV.filter((item) => can(session.roles, ...item.roles));

  const rail = (
    <div className="rail border-rail flex h-full flex-col rounded-[var(--radius-rail)] border p-4">
      {/* ---- Mark ------------------------------------------------------- */}
      <Link href="/" className="flex items-center gap-2.5 px-2 py-1.5">
        <span className="brand grid size-9 shrink-0 place-items-center rounded-xl">
          <Activity className="size-[1.125rem]" />
        </span>
        <span className="min-w-0">
          <span className="display block text-[0.9375rem] font-bold leading-none">
            FactoryManager
          </span>
          <span className="text-rail-2 mt-1 block truncate text-[0.6875rem] leading-none">
            {session.plant_name}
          </span>
        </span>
      </Link>

      {/* ---- Navigation -------------------------------------------------- */}
      <nav className="mt-5 flex-1 space-y-4 overflow-y-auto [@media(max-height:920px)]:mt-3 [@media(min-height:801px)_and_(max-height:920px)]:space-y-2.5 [@media(max-height:800px)]:space-y-1.5" aria-label="Main">
        {GROUPS.map((group) => {
          const groupItems = items.filter((item) => item.group === group.key);
          if (!groupItems.length) return null;
          return (
            <div key={group.key}>
              <p className="text-rail-2 px-3 pb-1.5 text-[0.625rem] font-semibold tracking-[0.1em] uppercase [@media(max-height:800px)]:pb-0.5">
                {group.label}
              </p>
              <ul className="space-y-0.5">
                {groupItems.map((item) => {
                  const active =
                    pathname === item.href || pathname.startsWith(`${item.href}/`);
                  const Icon = ICONS[item.href] ?? Activity;
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-colors [@media(min-height:801px)_and_(max-height:920px)]:py-1.5 [@media(max-height:800px)]:py-1",
                          active
                            ? "brand font-semibold"
                            : "text-rail-2 hover:bg-[var(--rail-2)] hover:text-[var(--rail-text)]",
                        )}
                      >
                        <Icon className="size-[1.0625rem] shrink-0" />
                        <span className="truncate">{item.label}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </nav>

      {/* ---- §10, the no-blame rule -------------------------------------
          It is a design constraint, not a footnote: "reports target reasons,
          machines and processes, never individuals." It used to run as a strip
          across the top of every screen, which is exactly how a standing
          principle becomes wallpaper. Here it holds the one spot in the rail
          the eye returns to. */}
      {/* On a short laptop screen (1366×768 is the common office machine) the
          full card pushed Money and Onboarding out of sight inside the rail.
          There it collapses to one line; the sentence stays for screen readers
          and as a tooltip, so the rule is compacted, never dropped. */}
      <div
        className="brand mt-4 shrink-0 rounded-2xl p-4 [@media(max-height:920px)]:mt-3 [@media(max-height:920px)]:flex [@media(max-height:920px)]:items-center [@media(max-height:920px)]:gap-2.5 [@media(max-height:920px)]:px-3.5 [@media(max-height:920px)]:py-2.5"
        title="This data targets machines, reasons and processes — never individuals."
      >
        <ShieldCheck className="size-5 shrink-0 [@media(max-height:920px)]:size-4" />
        <p className="mt-2.5 text-[0.8125rem] font-bold [@media(max-height:920px)]:mt-0">No blame</p>
        <p className="mt-1 text-[0.75rem] leading-snug opacity-70 [@media(max-height:920px)]:sr-only">
          This data targets machines, reasons and processes — never individuals.
        </p>
      </div>
    </div>
  );

  return (
    <div className="min-h-dvh lg:flex lg:gap-3 lg:p-3">
      {/* ---- Rail: fixed on desktop, a drawer on a tablet held sideways --- */}
      <aside className="hidden w-[16.5rem] shrink-0 lg:block">
        <div className="sticky top-3 h-[calc(100dvh-1.5rem)]">{rail}</div>
      </aside>

      {navOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setNavOpen(false)}
            className="absolute inset-0 bg-[oklch(20%_0.01_70/0.45)] backdrop-blur-sm"
          />
          <div className="rise absolute inset-y-2 left-2 w-[16.5rem]">{rail}</div>
        </div>
      ) : null}

      {/* ---- Page -------------------------------------------------------- */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="glass sticky top-0 z-40 flex items-center gap-3 rounded-none border-b px-4 py-3 lg:top-3 lg:rounded-[var(--radius-card)] lg:border lg:px-4">
          <button
            type="button"
            onClick={() => setNavOpen(true)}
            aria-label="Open navigation"
            className="text-2 grid size-10 shrink-0 place-items-center rounded-xl border transition-colors hover:surface-2 hover:text-[var(--text)] lg:hidden"
          >
            <Menu className="size-5" />
          </button>

          {/* The search slot in the reference is a real destination here: the
              Ask screen answers plant questions in plain language. */}
          <Link
            href="/copilot"
            className="text-3 surface-2 flex h-10 min-w-0 flex-1 items-center gap-2.5 rounded-full px-4 text-sm transition-colors hover:surface-3 sm:max-w-sm"
          >
            <Search className="size-4 shrink-0" />
            <span className="truncate">Ask about the plant…</span>
          </Link>

          <div className="ml-auto flex shrink-0 items-center gap-2">
            <SyncPill />
            <PushToggle />
            <span className="text-2 surface-2 hidden rounded-full px-3.5 py-2 text-[0.8125rem] font-medium xl:block">
              {new Date().toLocaleDateString("en-IN", {
                day: "numeric",
                month: "short",
                year: "numeric",
              })}
            </span>

            <div className="flex items-center gap-2.5 rounded-full border py-1 pr-1 pl-1 sm:pl-3">
              <div className="hidden text-right sm:block">
                <span className="block text-[0.8125rem] font-semibold leading-tight">
                  {session.full_name}
                </span>
                <span className="text-3 block text-[0.6875rem] capitalize leading-tight">
                  {session.roles.join(" · ")}
                </span>
              </div>
              <span className="brand grid size-8 shrink-0 place-items-center rounded-full text-[0.6875rem] font-bold">
                {initials(session.full_name)}
              </span>
            </div>

            <button
              type="button"
              disabled={signingOut}
              onClick={async () => {
                setSigningOut(true);
                // This device should stop ringing for the person leaving it, but
                // a dead network must not trap anyone signed in.
                await Promise.race([
                  disablePush().catch(() => undefined),
                  new Promise((resolve) => setTimeout(resolve, 2500)),
                ]);
                clearApiCache();
                clearSession();
                router.replace("/login");
              }}
              aria-label="Sign out"
              className="text-3 grid size-11 shrink-0 place-items-center rounded-xl border transition-colors hover:surface-2 hover:text-[var(--text)]"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </header>

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-5 sm:py-7">{children}</main>
      </div>
    </div>
  );
}
