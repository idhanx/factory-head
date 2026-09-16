/**
 * Presentation-edge formatting.
 *
 * The server sends integer paise plus a pre-formatted display string for every
 * money value, and the display string is what screens render. These helpers
 * exist for the two cases the server cannot pre-format: a live ticker counting
 * between WebSocket frames, and a client-side sort.
 *
 * They deliberately mirror `app/kernel/money.py`. If you change a rule here,
 * change it there — or better, add the formatted field to the API response and
 * delete the helper.
 */

const RUPEE = 100;

/** Indian digit grouping: 1234567 -> "12,34,567". */
function groupIndian(value: number): string {
  const s = Math.trunc(Math.abs(value)).toString();
  if (s.length <= 3) return s;
  const head = s.slice(0, -3);
  const tail = s.slice(-3);
  const parts: string[] = [];
  let rest = head;
  while (rest.length > 2) {
    parts.unshift(rest.slice(-2));
    rest = rest.slice(0, -2);
  }
  if (rest) parts.unshift(rest);
  return `${parts.join(",")},${tail}`;
}

export function formatInr(paise: number): string {
  const negative = paise < 0;
  const body = `₹${groupIndian(paise / RUPEE)}`;
  return negative ? `-${body}` : body;
}

export function formatInrCompact(paise: number): string {
  const rupees = Math.abs(paise) / RUPEE;
  const sign = paise < 0 ? "-" : "";
  if (rupees >= 1e7) return `${sign}₹${trim(rupees / 1e7)}Cr`;
  if (rupees >= 1e5) return `${sign}₹${trim(rupees / 1e5)}L`;
  return `${sign}₹${groupIndian(rupees)}`;
}

function trim(value: number): string {
  return value.toFixed(2).replace(/\.?0+$/, "");
}

export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

export function formatPercent(ratio: number, digits = 0): string {
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
  });
}

/** Minutes elapsed since an ISO timestamp, for the live ticker. */
export function minutesSince(iso: string): number {
  return Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
}
