/* Planned CRM service worker — version {{ version }}

   Kept deliberately small:
   * pages always come from the network, so nobody ever sees a stale lead or
     listing; only when the phone has no connection does the offline page show
   * the stylesheet, script and icons are kept as an offline fallback
   * push messages become phone notifications; tapping one opens the record */

var CACHE = "planned-crm-v{{ version }}";
var OFFLINE_URL = "{{ offline_url }}";
var PRECACHE = [
  OFFLINE_URL,
  "{{ icon }}",
  "{{ badge }}"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) { return cache.addAll(PRECACHE); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) {
        return k.indexOf("planned-crm-") === 0 && k !== CACHE;
      }).map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Screens: network only, offline page when there is no network at all.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(function () { return caches.match(OFFLINE_URL); })
    );
    return;
  }

  // Styles, scripts, icons: fresh from the server, so a deploy shows up at
  // once; the cached copy is only the fallback when the phone is offline.
  if (url.pathname.indexOf("/static/") === 0) {
    event.respondWith(
      fetch(req).then(function (res) {
        if (res && res.ok) {
          var copy = res.clone();
          caches.open(CACHE).then(function (cache) { cache.put(req, copy); });
        }
        return res;
      }).catch(function () { return caches.match(req); })
    );
  }
  // Everything else (uploads, JSON, exports) goes straight to the server.
});

/* ------------------------------------------------------------ notifications */

self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch (e) { data = { body: event.data ? event.data.text() : "" }; }

  var options = {
    body: data.body || "",
    icon: "{{ icon }}",
    badge: "{{ badge }}",
    dir: "auto",
    data: { url: data.url || "/" }
  };
  // Same record twice in a row replaces the older alert instead of stacking.
  if (data.tag) { options.tag = data.tag; options.renotify = true; }

  event.waitUntil(self.registration.showNotification(data.title || "Planned CRM", options));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = new URL((event.notification.data && event.notification.data.url) || "/",
                       self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
      // Reuse the app if it is already open, rather than opening a second copy.
      for (var i = 0; i < list.length; i++) {
        var c = list[i];
        if (new URL(c.url).origin === self.location.origin && "focus" in c) {
          return c.focus().then(function (w) {
            return (w && "navigate" in w) ? w.navigate(target) : w;
          });
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
