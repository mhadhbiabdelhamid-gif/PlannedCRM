"""Phone app: installable home-screen app (PWA) and push notifications.

Two halves:

* The installable part — a web app manifest, a service worker and an offline
  page. With these, "Add to Home Screen" on Android or iPhone puts the CRM on
  the phone as an icon that opens full-screen, without the browser's address
  bar. Nothing to publish in an app store.

* Push notifications — every call to db.notify() (lead assigned, follow-up set,
  listing approved, lease ending ...) is also sent to the phones of the person
  it is meant for, if they turned notifications on. The in-app notification
  row is written first and stays the record; the push is a best-effort copy,
  so a phone that is off or a push service that is down never blocks a save.

Keys
----
Web push needs a VAPID key pair. VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY from the
environment win; otherwise a pair is generated once and kept in the settings
table, so it survives redeploys and restores and needs no setup on Render.
Changing the keys signs everyone's phone out of notifications — they just tap
"Turn on" again.
"""
import json
import os
import queue
import sqlite3
import threading
import time

from flask import (Blueprint, Response, current_app, g, jsonify, render_template,
                   request, url_for)

from auth import login_required
from db import execute, get_setting, now, query
from i18n import t

bp = Blueprint("pwa", __name__)

# The colours of the rail and the canvas, so the splash screen and the phone's
# status bar match the app instead of flashing white.
THEME_COLOR = "#23201A"
BACKGROUND_COLOR = "#23201A"

# Bumped whenever sw.js changes in a way an installed phone must pick up.
SW_VERSION = "1"


# ------------------------------------------------------------------ install

@bp.route("/manifest.webmanifest")
def manifest():
    company = get_setting("company_name", "Planned Real Estate")
    icon = lambda name: url_for("static", filename=f"img/pwa/{name}")
    data = {
        "id": "/",
        "name": f"{company} CRM",
        "short_name": "Planned CRM",
        "description": "Listings, leads and deals for the Planned Real Estate team.",
        "start_url": "/?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "theme_color": THEME_COLOR,
        "background_color": BACKGROUND_COLOR,
        "lang": "en",
        "dir": "auto",
        "icons": [
            {"src": icon("icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": icon("icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": icon("icon-maskable-512.png"), "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
        # Long-press the icon on Android for these.
        "shortcuts": [
            {"name": "New lead", "url": "/leads/new",
             "icons": [{"src": icon("icon-192.png"), "sizes": "192x192"}]},
            {"name": "Today's priorities", "url": "/leads/priority",
             "icons": [{"src": icon("icon-192.png"), "sizes": "192x192"}]},
        ],
    }
    resp = Response(json.dumps(data), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/sw.js")
def service_worker():
    """Served from the site root, not /static, so its scope is the whole CRM."""
    body = render_template("pwa/sw.js", version=SW_VERSION,
                           offline_url=url_for("pwa.offline"),
                           icon=url_for("static", filename="img/pwa/icon-192.png"),
                           badge=url_for("static", filename="img/pwa/badge-96.png"))
    resp = Response(body, mimetype="application/javascript")
    # A cached worker would keep an installed phone on old code for a day.
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.route("/offline")
def offline():
    return render_template("pwa/offline.html")


# --------------------------------------------------------------------- keys

def _keys():
    """(private_pem, public_b64url), creating and saving a pair the first time."""
    env_priv = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
    env_pub = (os.environ.get("VAPID_PUBLIC_KEY") or "").strip()
    if env_priv and env_pub:
        return env_priv.replace("\\n", "\n"), env_pub

    stored = get_setting("vapid_keys", "")
    if not stored:
        priv, pub = _generate()
        # One row holding both halves, written only if absent: several server
        # workers can get here together, and two of them each saving their own
        # private key and public key separately could leave a mismatched pair.
        execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('vapid_keys', ?)",
                (json.dumps({"private": priv, "public": pub}),))
        stored = get_setting("vapid_keys", "")
    data = json.loads(stored)
    return data["private"], data["public"]


def _generate():
    import base64
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    priv = v.private_pem().decode()
    raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)
    pub = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return priv, pub


def push_available():
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        return False


# ------------------------------------------------------------ subscriptions

@bp.route("/push/key")
@login_required
def push_key():
    if not push_available():
        return jsonify(ok=False, error="Push is not installed on the server."), 503
    return jsonify(ok=True, key=_keys()[1])


def _json_body():
    # Only a real fetch() can send application/json across the wire, so a form
    # on some other site cannot sign anyone up for anything.
    if not request.is_json:
        return None
    return request.get_json(silent=True) or {}


@bp.route("/push/subscribe", methods=("POST",))
@login_required
def push_subscribe():
    data = _json_body()
    if data is None:
        return jsonify(ok=False), 415
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh, auth_key = keys.get("p256dh"), keys.get("auth")
    if not (endpoint.startswith("https://") and p256dh and auth_key):
        return jsonify(ok=False, error="Incomplete subscription."), 400
    device = (request.user_agent.string or "")[:200]
    # One phone, one row. A phone handed to someone else moves with the
    # sign-in, so the new person gets the notifications, not the old one.
    execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    execute("INSERT INTO push_subscriptions"
            " (user_id, endpoint, p256dh, auth, device, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (g.user["id"], endpoint, p256dh, auth_key, device, now()))
    return jsonify(ok=True)


@bp.route("/push/unsubscribe", methods=("POST",))
@login_required
def push_unsubscribe():
    data = _json_body()
    if data is None:
        return jsonify(ok=False), 415
    endpoint = (data.get("endpoint") or "").strip()
    execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
            (endpoint, g.user["id"]))
    return jsonify(ok=True)


@bp.route("/push/test", methods=("POST",))
@login_required
def push_test():
    if _json_body() is None:
        return jsonify(ok=False), 415
    n = query("SELECT COUNT(*) c FROM push_subscriptions WHERE user_id = ?",
              (g.user["id"],), one=True)["c"]
    if not n:
        return jsonify(ok=False, error="Notifications are not turned on for this phone."), 400
    send(g.user["id"], t("Notifications are working on this phone."), "/")
    return jsonify(ok=True)


# -------------------------------------------------------------------- send
#
# Sending is done by one background thread per server worker, fed from a
# queue. Pushing means an HTTPS call to Google / Apple / Mozilla for each
# phone, and an import that assigns forty leads must not sit waiting on
# forty of those before the page comes back.

_queue = queue.Queue()
_worker = None
_worker_lock = threading.Lock()


def send(user_id, message, link=None):
    """Queue a push to every phone `user_id` turned notifications on for.

    Called from db.notify(); never raises, so a push problem can't undo the
    change that caused the notification.
    """
    try:
        if not user_id or not push_available():
            return
        subs = query("SELECT endpoint, p256dh, auth FROM push_subscriptions"
                     " WHERE user_id = ?", (user_id,))
        if not subs:
            return
        private_pem, _pub = _keys()
        payload = json.dumps({
            "title": get_setting("company_name", "Planned Real Estate"),
            "body": message,
            "url": link or "/",
            "tag": link or None,
        })
        job = {
            "db": current_app.config["DATABASE"],
            "key": private_pem,
            "sub": "mailto:" + (os.environ.get("VAPID_CONTACT")
                                or os.environ.get("ADMIN_EMAIL")
                                or "admin@plannedrealestate.qa"),
            "payload": payload,
            "subs": [dict(s) for s in subs],
        }
        _ensure_worker()
        _queue.put(job)
    except Exception as exc:                  # never let a push break a save
        print(f"  Push not queued: {exc}")


def _ensure_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, daemon=True, name="crm-push")
            _worker.start()


def _run():
    while True:
        job = _queue.get()
        try:
            _deliver(job)
        except Exception as exc:
            print(f"  Push failed: {exc}")
        finally:
            _queue.task_done()


def _deliver(job):
    # Vapid02 signs with the standard "vapid t=..., k=..." header (RFC 8292),
    # which Apple's push service requires; Google and Mozilla accept it too.
    from py_vapid import Vapid02
    from pywebpush import WebPushException, webpush

    vapid = Vapid02.from_pem(job["key"].encode())
    gone = []
    for s in job["subs"]:
        info = {"endpoint": s["endpoint"],
                "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}
        try:
            webpush(info, job["payload"], vapid_private_key=vapid,
                    vapid_claims={"sub": job["sub"]}, ttl=24 * 3600, timeout=10)
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            # 404/410: the phone uninstalled the app or revoked permission.
            if code in (404, 410):
                gone.append(s["endpoint"])
            else:
                print(f"  Push to one phone failed ({code}): {exc}")
    if gone:
        for attempt in range(3):
            try:
                con = sqlite3.connect(job["db"], timeout=10)
                con.executemany("DELETE FROM push_subscriptions WHERE endpoint = ?",
                                [(e,) for e in gone])
                con.commit()
                con.close()
                break
            except sqlite3.OperationalError:
                time.sleep(1)
