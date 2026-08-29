/**
 * 푸시 구독. **iOS 는 홈 화면에 추가한 경우에만** 웹푸시를 준다(16.4+).
 *
 * 사파리 탭으로 열어 두면 권한 요청 자체가 안 뜬다. 그래서 "안 됩니다" 가 아니라
 * **왜 안 되는지**를 돌려준다 — 화면이 그걸 그대로 안내해야 사용자가 다음 동작을 안다.
 */
import { authHeaders } from "./api";

export type PushState =
  | { ok: true }
  | { ok: false; reason: string; needsHomeScreen?: boolean };

/** 홈 화면에서 실행 중인가. iOS 는 `navigator.standalone`, 나머지는 미디어 쿼리. */
export function installed(): boolean {
  const legacy = (window.navigator as unknown as { standalone?: boolean }).standalone;
  return Boolean(legacy) || window.matchMedia("(display-mode: standalone)").matches;
}

export function isIos(): boolean {
  return /iphone|ipad|ipod/i.test(window.navigator.userAgent);
}

function toBytes(base64: string): ArrayBuffer {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4))
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = window.atob(padded);
  // `ArrayBuffer` 로 못 박는다. `Uint8Array` 는 `SharedArrayBuffer` 도 담을 수 있어
  // `applicationServerKey` 타입과 안 맞는다.
  const buffer = new ArrayBuffer(raw.length);
  const view = new Uint8Array(buffer);
  for (let i = 0; i < raw.length; i += 1) view[i] = raw.charCodeAt(i);
  return buffer;
}

export async function register(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch {
    return null;
  }
}

export async function subscribe(publicKey: string): Promise<PushState> {
  if (!publicKey) return { ok: false, reason: "서버에 푸시 키가 설정돼 있지 않다" };
  if (!("Notification" in window) || !("PushManager" in window)) {
    // iOS 에서 이 분기에 오는 건 대개 홈 화면에 추가를 안 한 경우다.
    return isIos() && !installed()
      ? { ok: false, needsHomeScreen: true,
          reason: "홈 화면에 추가해야 알림을 받을 수 있다" }
      : { ok: false, reason: "이 브라우저는 웹푸시를 지원하지 않는다" };
  }
  if (isIos() && !installed()) {
    return { ok: false, needsHomeScreen: true,
             reason: "홈 화면에 추가해야 알림을 받을 수 있다" };
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return { ok: false, reason: "알림 권한이 거부됐다 — 설정에서 다시 켤 수 있다" };
  }

  const registration = (await navigator.serviceWorker.getRegistration())
    ?? (await register());
  if (!registration) return { ok: false, reason: "서비스 워커를 등록하지 못했다" };

  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: toBytes(publicKey),
  });

  const raw = subscription.toJSON() as { endpoint?: string; keys?: Record<string, string> };
  const res = await fetch("/api/alerts/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ endpoint: raw.endpoint, keys: raw.keys ?? {} }),
  });
  return res.ok ? { ok: true } : { ok: false, reason: "서버가 구독을 받지 못했다" };
}
