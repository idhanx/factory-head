"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Activity, ArrowRight, ShieldCheck } from "lucide-react";
import { api, saveSession } from "@/lib/api";
import { homeFor } from "@/lib/roles";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { useT } from "@/lib/i18n";

const DEMO = [
  { label: "Owner", who: "Mr. Kumaran", id: "owner@kumaranauto.in", pw: "owner1234", lands: "Money screen" },
  { label: "Supervisor", who: "Murugan V.", id: "+919000000011", pw: "shift1234", lands: "Machine grid" },
  { label: "Quality", who: "Kavitha R.", id: "+919000000012", pw: "quality1234", lands: "Quality entry" },
  // §6's own two names: the Coach assigns CNC-3's insert to Suresh K. in
  // Maintenance, and asks Ramesh P. in Stores to confirm Line 2's material.
  { label: "Maintenance", who: "Suresh K.", id: "+919000000013", pw: "maint1234", lands: "Maintenance desk" },
  { label: "Stores", who: "Ramesh P.", id: "+919000000014", pw: "stores1234", lands: "Stores desk" },
];

export default function Login() {
  const t = useT();
  const router = useRouter();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(id: string, pw: string) {
    setBusy(true);
    setError(null);
    try {
      const session = await api.login(id, pw);
      saveSession(session);
      router.replace(homeFor(session.roles));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("Could not sign in"));
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      {/* ---- The pitch. §11: "Machine → Money → Action. Nothing else in the
              first ten minutes." ------------------------------------------- */}
      <section className="relative hidden overflow-hidden border-r p-12 lg:flex lg:flex-col lg:justify-between">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(900px 600px at 20% 10%, color-mix(in oklab, var(--color-accent) 14%, transparent), transparent 62%), radial-gradient(700px 500px at 85% 85%, color-mix(in oklab, var(--color-recovered) 11%, transparent), transparent 58%)",
          }}
        />

        <div className="relative flex items-center gap-2.5">
          <span className="brand grid size-9 place-items-center rounded-xl elev-2">
            <Activity className="size-5" />
          </span>
          <span className="display text-lg font-bold">{t("FactoryManager AI")}</span>
        </div>

        <div className="relative max-w-lg">
          <p className="label mb-4">{t("The AI plant manager")}</p>
          <h1 className="display text-[2.75rem] font-bold leading-[1.05]">
            {t("Every loss visible in rupees,")}<br />
            <span className="text-[var(--color-loss)]">{t("the moment it happens.")}</span>
          </h1>
          <p className="text-2 mt-5 text-base leading-relaxed">
            {t("A machine stops. The system knows what it cost before it restarts, ranks it against every other loss in the plant, and tells one named person what to do about it — with a deadline.")}
          </p>

          <div className="mt-10 flex items-center gap-3">
            {["Machine", "Money", "Action"].map((step, index) => (
              <div key={step} className="flex items-center gap-3">
                <span className="surface elev-1 rounded-lg border px-3.5 py-2 text-sm font-semibold">
                  {step}
                </span>
                {index < 2 ? <ArrowRight className="size-4 text-3" /> : null}
              </div>
            ))}
          </div>
        </div>

        <div className="text-3 relative flex items-center gap-2 text-xs">
          <ShieldCheck className="size-3.5" />
          {t("Your data is yours, siloed per plant, never shared.")}
        </div>
      </section>

      {/* ---- Sign in ---------------------------------------------------- */}
      <section className="flex items-center justify-center px-5 py-10 sm:px-10">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <span className="brand mb-3 grid size-10 place-items-center rounded-xl elev-2">
              <Activity className="size-5" />
            </span>
            <h1 className="display text-2xl font-bold">{t("FactoryManager AI")}</h1>
            <p className="text-2 mt-1 text-sm">{t("Machine → Money → Action")}</p>
          </div>

          <div className="mb-6 hidden lg:block">
            <h2 className="display text-2xl font-bold">{t("Sign in")}</h2>
            <p className="text-2 mt-1 text-sm">{t("Kumaran Auto Components, Hosur")}</p>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit(identifier, password);
            }}
            className="space-y-4"
          >
            <div>
              <Label htmlFor="identifier">{t("Phone number or email")}</Label>
              {/* Supervisors sign in with a phone and a PIN, owners with an
                  email. One field, because nobody on a shop floor should have
                  to work out which kind of account they have. */}
              <Input
                id="identifier"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                placeholder="+91 90000 00011"
                autoComplete="username"
                required
              />
            </div>
            <div>
              <Label htmlFor="password">{t("PIN or password")}</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>

            {error ? <ErrorNote message={error} /> : null}

            <Button type="submit" size="touch" className="w-full" disabled={busy}>
              {busy ? t("Signing in…") : t("Sign in")}
            </Button>
          </form>

          <div className="mt-10">
            <div className="mb-3 flex items-center gap-3">
              <span className="label whitespace-nowrap">{t("Demo plant")}</span>
              <span className="hairline flex-1" />
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {DEMO.map((d) => (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => void submit(d.id, d.pw)}
                  disabled={busy}
                  className="group surface elev-1 rounded-lg border px-3.5 py-3 text-left transition-[transform,box-shadow] hover:-translate-y-0.5 hover:shadow-[var(--shadow-2)] disabled:opacity-50"
                >
                  <span className="block text-sm font-semibold">{t(d.label)}</span>
                  <span className="text-3 block text-xs">{d.who}</span>
                  <span className="text-3 mt-1.5 flex items-center gap-1 text-[0.6875rem] transition-colors group-hover:text-[var(--color-accent)]">
                    {t(d.lands)}
                    <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
