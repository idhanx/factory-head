"use client";

import { api } from "@/lib/api";

/**
 * Browser push on this device, and the one message the page sends the worker.
 *
 * Push is per device, not per person: a supervisor who wants escalations on
 * the office PC and on their phone turns it on in both. The server keys a
 * subscription on its endpoint, so turning it on twice is harmless.
 */

export type PushState =
  | "checking"
  | "unsupported" // no service worker / Push API in this browser (or not a secure context)
  | "no-worker" // the worker is not registered (dev mode without fm.sw.dev)
  | "unavailable" // the server has no push key configured
  | "blocked" // the person (or the browser) denied notifications for this site
  | "off"
  | "on";

function supported(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  // `ready` never resolves when nothing is registered, so ask first.
  const existing = await navigator.serviceWorker.getRegistration("/");
  return existing ? navigator.serviceWorker.ready : null;
}

export async function pushState(): Promise<PushState> {
  if (!supported()) return "unsupported";
  const reg = await registration();
  if (!reg) return "no-worker";
  if (Notification.permission === "denied") return "blocked";
  const subscription = await reg.pushManager.getSubscription();
  if (subscription) return "on";
  const key = await api.pushPublicKey();
  return key.enabled && key.public_key ? "off" : "unavailable";
}

/** The VAPID key arrives base64url-encoded; the Push API wants the raw bytes. */
function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = `${base64url}${"=".repeat((4 - (base64url.length % 4)) % 4)}`.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

export async function enablePush(): Promise<PushState> {
  if (!supported()) return "unsupported";
  const key = await api.pushPublicKey();
  if (!key.enabled || !key.public_key) return "unavailable";

  const permission = await Notification.requestPermission();
  if (permission === "denied") return "blocked";
  if (permission !== "granted") return "off";

  const reg = await registration();
  if (!reg) return "no-worker";
  const subscription =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key.public_key) }));

  const json = subscription.toJSON();
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    throw new Error("This browser returned an incomplete push subscription.");
  }
  try {
    await api.subscribePush({
      endpoint: json.endpoint,
      keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
      user_agent: navigator.userAgent.slice(0, 255),
    });
  } catch (e) {
    // A subscription the server does not know about would look "on" here and
    // never ring. Undo it so the state on screen is the truth.
    await subscription.unsubscribe().catch(() => undefined);
    throw e;
  }
  return "on";
}

export async function disablePush(): Promise<PushState> {
  if (!supported()) return "unsupported";
  const reg = await registration();
  const subscription = reg ? await reg.pushManager.getSubscription() : null;
  if (subscription) {
    // Tell the server first: if that fails the device stays subscribed and
    // the screen still says so, rather than ringing for a switched-off device.
    await api.unsubscribePush(subscription.endpoint);
    await subscription.unsubscribe();
  }
  return "off";
}

/** Sign-out or plant switch: the cached pick-lists belonged to the previous login. */
export function clearApiCache() {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  navigator.serviceWorker.controller?.postMessage({ type: "clear-api-cache" });
}
