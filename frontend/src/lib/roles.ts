import type { Role } from "@/types/api";

/** Where each role lands, and what they are allowed to reach. */
export function homeFor(roles: Role[]): string {
  if (roles.includes("owner") || roles.includes("consultant")) return "/owner";
  if (roles.includes("quality")) return "/quality";
  // §1 gives both of these roles a cadence of "when events occur", so their
  // home is the desk that shows what is happening now — not the Actions board,
  // which is the PDCA backlog and answers a different question entirely.
  if (roles.includes("maintenance")) return "/maintenance";
  if (roles.includes("stores")) return "/stores";
  return "/floor";
}

export function can(roles: Role[], ...allowed: Role[]): boolean {
  return roles.includes("admin") || allowed.some((role) => roles.includes(role));
}

/** The role sets the API gates on. `can` adds admin to both. */
export const ADMINS: Role[] = ["owner", "consultant"];
export const SUPS: Role[] = ["supervisor", "owner", "consultant"];

export const NAV: Array<{
  href: string;
  label: string;
  roles: Role[];
  group: "capture" | "improve" | "plugins" | "money";
}> = [
  { href: "/floor", label: "Machine Grid", roles: ["supervisor", "owner", "maintenance"], group: "capture" },
  { href: "/shift", label: "Shift", roles: ["supervisor", "owner"], group: "capture" },
  // Past shifts and the corrections to them. `/shifts/…` is its own prefix, so
  // it never inherits the stricter `/shift` entry above.
  { href: "/shifts", label: "Shift history", roles: ["supervisor", "owner", "consultant"], group: "capture" },
  { href: "/quality", label: "Quality", roles: ["quality", "owner"], group: "capture" },
  // §1's two "when events occur" roles. Each desk reads Core data the plant is
  // already capturing and writes only the one thing §1 names for that role:
  // maintenance logs what fixed a breakdown, stores confirms material staging.
  { href: "/maintenance", label: "Maintenance", roles: ["maintenance", "supervisor", "owner", "consultant"], group: "capture" },
  { href: "/stores", label: "Stores", roles: ["stores", "supervisor", "owner", "consultant"], group: "capture" },
  { href: "/meeting", label: "Morning Meeting", roles: ["supervisor", "owner", "quality", "maintenance", "stores"], group: "improve" },
  { href: "/actions", label: "Actions", roles: ["supervisor", "owner", "maintenance", "stores", "quality"], group: "improve" },
  // §3 traceability and §14.2 stage tracking share one entry: both are about lots.
  { href: "/lots", label: "Lots", roles: ["supervisor", "owner", "quality", "stores", "maintenance", "consultant"], group: "improve" },
  { href: "/memory", label: "Factory Memory", roles: ["supervisor", "owner", "maintenance", "quality", "stores"], group: "improve" },
  { href: "/coach", label: "Shift Coach", roles: ["supervisor", "owner"], group: "improve" },
  { href: "/copilot", label: "Ask", roles: ["supervisor", "owner", "maintenance", "quality", "stores"], group: "improve" },
  // Layer 5 — built in parallel with the core, never as separate products (§3).
  { href: "/kaizen", label: "Kaizen", roles: ["supervisor", "owner", "maintenance", "quality", "stores"], group: "plugins" },
  { href: "/skills", label: "Skill Matrix", roles: ["supervisor", "owner", "consultant"], group: "plugins" },
  { href: "/compliance", label: "Audit", roles: ["owner", "quality", "consultant"], group: "plugins" },
  // §8: one executive page, drill-down per role. Everyone sees machines, lines
  // and reasons — never people — so no role needs to hide a page from another.
  { href: "/kpis", label: "KPIs", roles: ["owner", "consultant", "supervisor", "quality", "maintenance", "stores"], group: "money" },
  { href: "/owner", label: "Money", roles: ["owner", "consultant"], group: "money" },
  { href: "/onboarding", label: "Onboarding", roles: ["owner", "consultant", "admin"], group: "money" },
  { href: "/settings", label: "Settings", roles: ["owner", "consultant", "admin"], group: "money" },
];

/** The NAV entry a path belongs to, by prefix — `/actions/123` is `/actions`. */
export function navEntryFor(pathname: string) {
  return NAV.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
}

/**
 * Sub-routes that are stricter than the NAV entry they sit under.
 *
 * `/kpis` is every role's, but the monthly report it links to is the owner's
 * P&L — `GET /reports/monthly` is owner-only. Without this, prefix matching
 * hands `/kpis/monthly` the parent's roles and a supervisor who types the URL
 * lands on exactly the page of 403s the redirect above exists to prevent.
 */
const STRICTER_SUBROUTES: Array<{ prefix: string; roles: Role[] }> = [
  { prefix: "/kpis/monthly", roles: ADMINS },
];

/** A page with no NAV entry is not role-gated here; the API still is. */
export function canReach(roles: Role[], pathname: string): boolean {
  const stricter = STRICTER_SUBROUTES.find(
    (item) => pathname === item.prefix || pathname.startsWith(`${item.prefix}/`),
  );
  if (stricter) return can(roles, ...stricter.roles);
  const entry = navEntryFor(pathname);
  return !entry || can(roles, ...entry.roles);
}
