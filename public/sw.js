// 「我们」Service Worker（第 5 期，手写，不引 workbox）
// 缓存策略：app shell 预缓存 + 静态资源 cache-first + /api/ 一律 network-only；
// 推送：push 事件弹系统通知，点击 focus/打开对应页面。
const CACHE_VERSION = "v1";
const CACHE_NAME = `us-app-${CACHE_VERSION}`;

// 安装时预缓存的 app shell。SPA 路由由后端回退到 index.html（内容相同）；
// 构建产物是 hash 文件名，不逐个列举，走下面的运行时缓存兜底。
const APP_SHELL = [
  "/",
  "/wall",
  "/knowledge",
  "/wishes",
  "/graph",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // 清掉旧版本缓存，立即接管页面
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  // API 一律 network-only：社交内容不能吃不新鲜缓存
  if (url.pathname.startsWith("/api/")) return;

  // 导航请求：network-first，并顺手刷新缓存的 app shell；离线回退到缓存的 index.html
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put("/", copy));
          return res;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }

  // 其余同源静态资源：cache-first，未命中回源并写入运行时缓存
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
            }
            return res;
          }),
      ),
    );
  }
});

// ---------- Web Push ----------

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    /* 非 JSON payload 兜底用默认文案 */
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "我们", {
      body: data.body || "",
      icon: "/icon-192.png",
      data: { url: data.url || "/wall" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/wall";
  // 已有窗口则聚焦并跳转，否则新开
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((list) => {
        for (const client of list) {
          if ("focus" in client) {
            return client.focus().then((c) => c.navigate(url));
          }
        }
        return self.clients.openWindow(url);
      }),
  );
});
