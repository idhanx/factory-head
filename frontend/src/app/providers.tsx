"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { setUpdateReady } from "@/lib/offline/status";
import { LanguageProvider } from "@/lib/i18n";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Shopfloor data is hourly at best (§3: "owners do not need
            // second-by-second data"). The WebSocket pushes what is genuinely
            // live; polling on top of that would just burn plant bandwidth.
            staleTime: 30_000,
            refetchOnWindowFocus: true,
            retry: 1,
            // React Query's default pauses every query while the browser says
            // it is offline — so the grid would sit on a skeleton instead of
            // asking the service worker for its last good copy. Try once;
            // only the retries wait for the network.
            networkMode: "offlineFirst",
          },
          mutations: {
            // A write that needs the network should fail with a sentence, not
            // hang on "Saving…". Floor entries do not go through here — they
            // go to the outbox and never wait.
            networkMode: "always",
          },
        },
      }),
  );

  useEffect(() => registerServiceWorker(), []);

  return (
    <QueryClientProvider client={client}>
      <LanguageProvider>{children}</LanguageProvider>
    </QueryClientProvider>
  );
}

/**
 * Register public/sw.js in the browser.
 *
 * Skipped under `next dev` unless `localStorage['fm.sw.dev'] === '1'`: dev
 * chunks are not content-hashed and change on every save, so a cache-first
 * worker would serve yesterday's code and hot reload would appear broken. In
 * dev an old worker left over from a production run on the same port is
 * unregistered for the same reason. Test offline mode against a production
 * build (`npm run build && npm run start`).
 */
function registerServiceWorker(): (() => void) | undefined {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;

  let devOptIn = false;
  try {
    devOptIn = window.localStorage.getItem("fm.sw.dev") === "1";
  } catch {
    /* storage blocked: treat as not opted in */
  }

  if (process.env.NODE_ENV === "development" && !devOptIn) {
    void navigator.serviceWorker
      .getRegistrations()
      .then((registrations) => Promise.all(registrations.map((r) => r.unregister())))
      .catch(() => undefined);
    return;
  }

  // Only a tab that was already running an older worker has "an update";
  // the very first install also activates, and that is not news.
  const hadController = Boolean(navigator.serviceWorker.controller);
  const onMessage = (event: MessageEvent) => {
    if ((event.data as { type?: string } | null)?.type === "sw-activated" && hadController) setUpdateReady();
  };
  navigator.serviceWorker.addEventListener("message", onMessage);

  const register = () =>
    void navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      // Not fatal: the app works online without it, and the outbox (IndexedDB)
      // still keeps entries through a dropped connection.
    });
  if (document.readyState === "complete") register();
  else window.addEventListener("load", register, { once: true });

  return () => {
    navigator.serviceWorker.removeEventListener("message", onMessage);
    window.removeEventListener("load", register);
  };
}
