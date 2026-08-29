/* 서비스 워커 — 알림을 받아 띄우는 곳.
 *
 * **앱이 닫혀 있을 때 도는 유일한 코드다.** 여기서 오래 걸리는 일을 하면 iOS 가
 * 통째로 죽인다. 하는 일은 두 가지뿐 — 알림 띄우기, 눌렀을 때 앱 열기.
 *
 * 캐시는 일부러 얇게 둔다. 시세와 추천은 **오래된 값을 보여주면 안 되는** 데이터라,
 * 껍데기(HTML·JS)만 캐시하고 `/api/*` 는 절대 캐시하지 않는다.
 */
const SHELL = "market-lens-shell-v1";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(["/", "/manifest.webmanifest"]))
      .catch(() => undefined)
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // **API 는 캐시하지 않는다.** 어제 가격을 오늘 값처럼 보여주면 안 된다.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws")) return;
  if (event.request.method !== "GET") return;

  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(event.request, copy)).catch(() => undefined);
        return res;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || caches.match("/")))
  );
});

self.addEventListener("push", (event) => {
  let data = { title: "market-lens", body: "" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (_) {
    if (event.data) data.body = event.data.text();
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      // 같은 종목의 알림은 **하나로 합친다.** 잠금화면이 같은 종목으로 도배되면
      // 정작 다른 종목 알림을 못 본다.
      tag: data.symbol || data.id || "market-lens",
      renotify: true,
      data: { id: data.id, symbol: data.symbol },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = "/?tab=alerts";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ("focus" in client) return client.focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
