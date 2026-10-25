/* Planned CRM — phone app: install to home screen and push notifications.
   Server side lives in pwa.py. The visible text is rendered by the templates
   (so it is translated); this script only decides which parts to show.

   Anything with  data-install-show="a b"  is shown when the install state is
   a or b, and anything with  data-push-show="..."  likewise for notifications.

   Install states: installed | can-install | ios-browser | manual
   Push states:    on | off | denied | unsupported | ios-install | busy      */

(function () {
  "use strict";

  /* --------------------------------------------- "Desktop site" on a phone
     A phone set to "Desktop site" ignores the viewport tag and lays the page
     out ~1000px wide, shrunk to fit: the whole desktop screen in miniature.
     Nothing in the page can undo that setting, so say how to turn it off.
     The note is scaled up by the same factor the page was shrunk, or it
     would be as unreadable as everything else. */
  (function () {
    var note = document.getElementById("desktop-mode-note");
    if (!note) return;
    var touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    var phoneWide = Math.min(screen.width, screen.height);
    if (!(touch && phoneWide && phoneWide < 700 && window.innerWidth > 860)) return;
    try { if (sessionStorage.getItem("crm-desktop-note") === "closed") return; } catch (e) {}
    var scale = Math.max(1, window.innerWidth / phoneWide);
    note.style.fontSize = Math.round(15 * scale) + "px";
    note.hidden = false;
    var close = note.querySelector("[data-desktop-note-close]");
    if (close) close.addEventListener("click", function () {
      note.hidden = true;
      try { sessionStorage.setItem("crm-desktop-note", "closed"); } catch (e) {}
    });
  })();

  if (!("serviceWorker" in navigator)) {
    setPush("unsupported");
    setInstall("manual");
    return;
  }

  var ua = navigator.userAgent || "";
  var isIOS = /iphone|ipad|ipod/i.test(ua) ||
              (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var standalone = window.matchMedia("(display-mode: standalone)").matches ||
                   window.navigator.standalone === true;
  var deferredPrompt = null;
  var registration = null;

  /* --------------------------------------------------------- show / hide */

  function apply(attr, state) {
    document.querySelectorAll("[" + attr + "]").forEach(function (el) {
      var wanted = (el.getAttribute(attr) || "").split(/\s+/);
      el.hidden = wanted.indexOf(state) === -1;
    });
  }
  function setInstall(state) { apply("data-install-show", state); }
  function setPush(state) {
    apply("data-push-show", state);
    var banner = document.getElementById("push-banner");
    if (banner) banner.hidden = !(state === "off" && standalone && !dismissed());
  }

  function dismissed() {
    try { return localStorage.getItem("crm-push-banner") === "later"; }
    catch (e) { return false; }
  }

  function note(key) {
    document.querySelectorAll("[data-push-note]").forEach(function (el) {
      el.hidden = el.getAttribute("data-push-note") !== key;
    });
  }

  /* ------------------------------------------------------------- install */

  setInstall(standalone ? "installed" : (isIOS ? "ios-browser" : "manual"));

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
    if (!standalone) setInstall("can-install");
  });
  window.addEventListener("appinstalled", function () {
    deferredPrompt = null;
    setInstall("installed");
  });

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-pwa-install]");
    if (!btn || !deferredPrompt) return;
    deferredPrompt.prompt();
    deferredPrompt.userChoice.finally(function () { deferredPrompt = null; });
  });

  /* ------------------------------------------------------ service worker */

  navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function (reg) {
    registration = reg;
    return refreshPush();
  }).catch(function () { setPush("unsupported"); });

  /* -------------------------------------------------------- notifications */

  var pushSupported = "PushManager" in window && "Notification" in window;

  function refreshPush() {
    if (!pushSupported) {
      // iPhone only offers notifications to the app opened from the home screen.
      setPush(isIOS && !standalone ? "ios-install" : "unsupported");
      return Promise.resolve();
    }
    if (Notification.permission === "denied") { setPush("denied"); return Promise.resolve(); }
    return registration.pushManager.getSubscription().then(function (sub) {
      if (sub) {
        // Re-send it: harmless if the server already has it, and it repairs
        // the case where the phone was signed in as someone else before.
        if (document.body.hasAttribute("data-signed-in")) {
          post("/push/subscribe", sub.toJSON()).catch(function () {});
        }
        setPush("on");
      } else {
        setPush("off");
      }
    });
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
        return data;
      });
    });
  }

  function keyBytes(b64) {
    var pad = "=".repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function turnOn() {
    if (!registration || !pushSupported) return;
    setPush("busy"); note("");
    // Must be asked straight from the tap, or iPhone refuses.
    Notification.requestPermission().then(function (perm) {
      if (perm !== "granted") {
        setPush(perm === "denied" ? "denied" : "off");
        return;
      }
      return fetch("/push/key", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) throw new Error(data.error || "no key");
          return registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: keyBytes(data.key)
          });
        })
        .then(function (sub) { return post("/push/subscribe", sub.toJSON()); })
        .then(function () { setPush("on"); note("enabled"); })
        .catch(function () { setPush("off"); note("failed"); });
    });
  }

  function turnOff() {
    if (!registration) return;
    setPush("busy"); note("");
    registration.pushManager.getSubscription().then(function (sub) {
      if (!sub) return;
      return post("/push/unsubscribe", { endpoint: sub.endpoint })
        .catch(function () {})
        .then(function () { return sub.unsubscribe(); });
    }).then(function () { setPush("off"); note("disabled"); })
      .catch(function () { refreshPush(); });
  }

  function sendTest() {
    note("");
    post("/push/test").then(function () { note("test-sent"); })
      .catch(function () { note("failed"); });
  }

  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-push-on]")) { e.preventDefault(); turnOn(); }
    else if (e.target.closest("[data-push-off]")) { e.preventDefault(); turnOff(); }
    else if (e.target.closest("[data-push-test]")) { e.preventDefault(); sendTest(); }
    else if (e.target.closest("[data-push-later]")) {
      e.preventDefault();
      try { localStorage.setItem("crm-push-banner", "later"); } catch (err) {}
      var banner = document.getElementById("push-banner");
      if (banner) banner.hidden = true;
    }
  });
})();
