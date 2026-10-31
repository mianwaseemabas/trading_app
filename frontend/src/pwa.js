// Install-as-app (PWA) and phone push notifications.
import { api } from "./api.js";

let deferredPrompt = null;
const listeners = new Set();
export const canInstall = () => !!deferredPrompt;
export const onInstallChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export const isStandalone = () => window.matchMedia?.("(display-mode: standalone)").matches || window.navigator.standalone === true;
export const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);

export function registerPWA() {
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferredPrompt = e; listeners.forEach((f) => f()); });
  window.addEventListener("appinstalled", () => { deferredPrompt = null; listeners.forEach((f) => f()); });
  window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => {}); });
}

export async function promptInstall() {
  if (!deferredPrompt) return false;
  deferredPrompt.prompt();
  const r = await deferredPrompt.userChoice;
  deferredPrompt = null;
  listeners.forEach((f) => f());
  return r.outcome === "accepted";
}

const toBytes = (b64) => {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
};

export const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

export async function pushState() {
  if (!pushSupported()) return "unsupported";
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = reg && (await reg.pushManager.getSubscription());
  if (sub) return "on";
  return Notification.permission === "denied" ? "blocked" : "off";
}

export async function enablePush() {
  if (!pushSupported()) throw new Error(isIOS() && !isStandalone() ? "On iPhone: first tap Share → Add to Home Screen, open the app from the icon, then turn notifications on." : "This browser does not support push notifications.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Notifications were not allowed. Allow them in the browser/phone settings and try again.");
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const { public_key } = await api.get("/push/key");
  const sub = (await reg.pushManager.getSubscription()) || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: toBytes(public_key) }));
  const j = sub.toJSON();
  await api.post("/push/subscribe", { endpoint: j.endpoint, keys: j.keys, device: navigator.userAgent.slice(0, 120) });
  return true;
}

export async function disablePush() {
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = reg && (await reg.pushManager.getSubscription());
  if (sub) {
    await api.post("/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
    await sub.unsubscribe();
  }
}
