"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, BellOff, BellRing } from "lucide-react";
import { cn } from "@/lib/utils";
import { disablePush, enablePush, pushState, type PushState } from "@/lib/offline/push";
import { Button } from "@/components/ui/button";
import { ErrorNote } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import { useT } from "@/lib/i18n";

/**
 * Alerts on this device. A bell in the header for everyone signed in.
 *
 * WhatsApp is the main channel for escalations; browser push is the fallback
 * for the office PC and the owner's phone. The permission prompt appears only
 * after a tap on "Turn on" — a prompt on page load gets a reflexive "Block",
 * and a blocked site can only be unblocked from the browser's settings.
 */

const COPY: Record<PushState, { title: string; hint: string }> = {
  checking: { title: "Checking…", hint: "" },
  on: {
    title: "Alerts are on for this device",
    hint: "Escalations and reports you are sent will pop up here, even with the app closed.",
  },
  off: {
    title: "Alerts are off for this device",
    hint: "Turn them on to get escalations and reports here as notifications.",
  },
  unavailable: {
    title: "Not available yet",
    hint: "Browser alerts have not been set up on the server for this plant. WhatsApp and SMS alerts are unaffected.",
  },
  blocked: {
    title: "Blocked in this browser",
    hint: "Notifications for this site were blocked. Allow them in the browser's site settings (the lock icon beside the address), then come back here.",
  },
  unsupported: {
    title: "This browser cannot show alerts",
    hint: "Browser alerts need Chrome, Edge or Firefox over a secure (https) connection. On an iPhone, add the app to the home screen first.",
  },
  "no-worker": {
    title: "Not ready on this device",
    hint: "The offline helper is not installed in this browser (it is switched off in development). Open the production app to turn on alerts.",
  },
};

export function PushToggle() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<PushState>("checking");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(() => {
    setError(null);
    pushState()
      .then(setState)
      .catch((e: Error) => {
        setState("off");
        setError(t("Could not check alerts: {0}", { 0: e.message }));
      });
  }, []);

  useEffect(() => check(), [check]);

  const change = async (turnOn: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setState(turnOn ? await enablePush() : await disablePush());
    } catch (e) {
      setError(`${turnOn ? "Could not turn alerts on" : "Could not turn alerts off"}: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const Icon = state === "on" ? BellRing : state === "off" || state === "checking" ? Bell : BellOff;

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
          check();
        }}
        aria-haspopup="dialog"
        aria-label={t("Alerts on this device: {0}", { 0: t(COPY[state].title) })}
        className={cn(
          "grid size-11 shrink-0 place-items-center rounded-xl border transition-colors hover:surface-2 hover:text-[var(--text)]",
          state === "on" ? "text-[var(--color-accent)]" : "text-3",
        )}
      >
        <Icon className="size-4" />
      </button>

      {open ? (
        <Sheet title={t("Alerts on this device")} subtitle={t(COPY[state].title)} onClose={() => setOpen(false)}>
          <div className="space-y-4">
            {COPY[state].hint ? (
              <p className="text-2 text-sm leading-relaxed">{t(COPY[state].hint)}</p>
            ) : null}
            {error ? <ErrorNote message={error} /> : null}
            <div className="flex flex-wrap gap-2">
              {state === "off" ? (
                <Button size="touch" disabled={busy} onClick={() => void change(true)}>
                  <BellRing className="size-4" />
                  {busy ? t("Turning on…") : t("Turn on alerts")}
                </Button>
              ) : null}
              {state === "on" ? (
                <Button size="touch" variant="outline" disabled={busy} onClick={() => void change(false)}>
                  <BellOff className="size-4" />
                  {busy ? t("Turning off…") : t("Turn off alerts")}
                </Button>
              ) : null}
              <Button size="touch" variant="ghost" onClick={() => setOpen(false)}>
                {t("Close")}
              </Button>
            </div>
          </div>
        </Sheet>
      ) : null}
    </>
  );
}
