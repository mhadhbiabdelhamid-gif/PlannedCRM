"""Ready-made posts, stories and reels: validate them, schedule them, and
publish them on time.

How it fits together:

* On a post's review page the designs are drawn in the browser in three
  formats: the feed post (4:5), stories (9:16) and a video (9:16) for a reel
  or a video story. Videos are put together on the server (social_video.py)
  in the background, from a listing video or a slideshow of chosen photos.
* "Validate" stores the finished pictures or video on the server with the
  caption as it is at that moment, and puts the item in the queue. A queue
  item is one thing to publish: a post, a story or a reel.
* The Schedule page (/social/schedule) gives each item a publish time, one by
  one or in bulk (one a day at a time you choose), and shows the next two
  weeks so empty days stand out.
* A background thread on the live server publishes items when their time
  comes. Each item is claimed with one conditional UPDATE, so with several
  server workers only one of them publishes it. Failures notify the person
  who validated it.

Admins only, like the rest of Social.
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta

from flask import (current_app, flash, jsonify, redirect, render_template,
                   request, send_from_directory, url_for)

import ai_social as S
import social_video as sv
from ai_intake import current_user_id, db
from ai_social import social_bp, cfg, PLATFORM_NAMES
from auth import admin_required

FORMATS = {"post": "Post", "story": "Story", "reel": "Reel"}
STATUS_LABEL = {
    "rendering": "Making the video", "render_failed": "Video failed",
    "draft": "Video made, not validated", "ready": "Validated, no time yet",
    "scheduled": "Scheduled", "posting": "Publishing now", "posted": "Posted",
    "partial": "Partly posted", "failed": "Failed", "cancelled": "Removed",
}
MEDIA_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|mp4)$")
MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "mp4": "video/mp4"}
STAMP = "%Y-%m-%d %H:%M:%S"
POSTING_STALE_MIN = 20       # a publish that hasn't finished by then died
RENDER_STALE_MIN = 25        # likewise for making a video
MISSED_HOURS = 6             # later than this, don't post; ask for a new time
TICK_SECONDS = 30

_TABLE_READY = False


def ensure_table(conn=None):
    global _TABLE_READY
    if _TABLE_READY:
        return
    conn = conn or db()
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS social_queue ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " post_id INTEGER, format TEXT NOT NULL, media_kind TEXT NOT NULL,"
        " media TEXT NOT NULL DEFAULT '[]', thumb TEXT, caption TEXT,"
        " platforms TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,"
        " publish_at TEXT, results TEXT, error TEXT, progress REAL,"
        " source TEXT, title TEXT, created_by INTEGER, created_at TEXT NOT NULL,"
        " validated_by INTEGER, validated_at TEXT, claimed_at TEXT,"
        " posted_at TEXT);"
        "CREATE INDEX IF NOT EXISTS ix_social_queue_due"
        " ON social_queue (status, publish_at);")
    conn.commit()
    _TABLE_READY = True


@social_bp.before_app_request
def _ensure_queue_table():
    if not _TABLE_READY:
        ensure_table()


# ------------------------------------------------------------------- time
# Stored in UTC like the rest of the CRM; typed and shown in Doha time.
def _offset():
    from db import TZ_OFFSET
    return TZ_OFFSET


def utc_now():
    return datetime.utcnow()


def utc_str(dt):
    return dt.strftime(STAMP)


def local_to_utc(text):
    """'2026-10-30T19:00' (from a datetime-local box) -> UTC stamp, or None."""
    text = (text or "").strip().replace(" ", "T")
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return utc_str(datetime.strptime(text, fmt) - _offset())
        except ValueError:
            pass
    raise ValueError("That time isn't valid.")


def to_local(stamp):
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp[:19], STAMP) + _offset()
    except ValueError:
        return None


def local_today():
    return (utc_now() + _offset()).date()


# ------------------------------------------------------------------ files
def media_folder():
    return S._card_folder()


@social_bp.route("/m/<name>")
def media_file(name):
    """Public, no sign-in: Facebook and Instagram download the stories, reels
    and post images from here. Random 32-hex names, like /social/card/."""
    if not MEDIA_NAME.match(name):
        return "Not found", 404
    return send_from_directory(media_folder(), name,
                               mimetype=MEDIA_TYPES[name.rsplit(".", 1)[1]],
                               conditional=True)


def media_url(name, external=False):
    path = f"/social/m/{name}"
    return (S.public_base() + path) if external else path


def _store(file_storage, ext):
    data = file_storage.read()
    if not data:
        return None
    name = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(media_folder(), name), "wb") as out:
        out.write(data)
    return name


def _upload_path(url, kind):
    """'/uploads/images/x.jpg' -> its file on disk (only inside that folder)."""
    prefix = f"/uploads/{kind}/"
    if not url or not url.startswith(prefix):
        return None
    name = os.path.basename(urllib.parse.unquote(url[len(prefix):]))
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], kind, name)
    return path if os.path.isfile(path) else None


# ------------------------------------------------------------------ helpers
def _row(qid):
    return db().execute("SELECT * FROM social_queue WHERE id = ?",
                        (qid,)).fetchone()


def _post_row(pid):
    row = db().execute("SELECT * FROM social_posts WHERE id = ?",
                       (pid,)).fetchone()
    return row if row and row["status"] != "discarded" else None


def _platforms():
    return [p for p in request.form.getlist("platform") if p in PLATFORM_NAMES]


def _caption_from_form(content):
    return S.build_caption(
        request.form.get("caption_ar") or content.get("caption_ar", ""),
        request.form.get("caption_en") or content.get("caption_en", ""),
        request.form.get("hashtags") or content.get("hashtags", []))


def _check_ready_to_validate(post, fmt, platforms, caption):
    """Plain-language reason it can't be validated yet, or None."""
    import social_cards as sc
    content = json.loads(post["content_json"])
    if sc.needs_photos(post) and not content.get("photos_ok"):
        return "Choose and approve the photos first (Step 1)."
    if not platforms:
        return "Tick Facebook, Instagram or both."
    if fmt in ("post", "reel") and "instagram" in platforms \
            and len(caption) > S.IG_CAPTION_LIMIT:
        return (f"The caption is {len(caption):,} characters; Instagram "
                f"allows {S.IG_CAPTION_LIMIT:,}. Shorten it first.")
    return None


def item_view(row):
    """A queue row as the templates want it."""
    d = dict(row)
    d["media"] = json.loads(row["media"] or "[]")
    d["platforms"] = json.loads(row["platforms"] or "[]")
    d["results"] = json.loads(row["results"] or "{}")
    d["source"] = json.loads(row["source"] or "{}")
    d["format_label"] = FORMATS.get(row["format"], row["format"])
    if row["format"] == "story" and row["media_kind"] == "video":
        d["format_label"] = "Video story"
    d["status_label"] = STATUS_LABEL.get(row["status"], row["status"])
    d["local_at"] = to_local(row["publish_at"])
    d["thumb_url"] = media_url(row["thumb"]) if row["thumb"] else None
    d["video_url"] = (media_url(d["media"][0])
                      if row["media_kind"] == "video" and d["media"] else None)
    # Plain paths, not url_for: the publisher builds these outside a request.
    d["review_url"] = f"/social/post/{row['post_id']}" if row["post_id"] else None
    return d


def post_queue_items(post_id):
    rows = db().execute(
        "SELECT * FROM social_queue WHERE post_id = ? AND status != 'cancelled'"
        " ORDER BY id DESC", (post_id,)).fetchall()
    return [item_view(r) for r in rows]


def answer(ok, message, code=200, **extra):
    return jsonify(dict(ok=ok, message=message, **extra)), code


# ----------------------------------------------- validate a picture format
@social_bp.route("/post/<int:post_id>/queue", methods=["POST"])
@admin_required
def queue_images(post_id):
    """Validate the feed post or the stories exactly as drawn on the page."""
    post = _post_row(post_id)
    if not post:
        return answer(False, "This post no longer exists.", 404)
    fmt = request.form.get("format")
    if fmt not in ("post", "story"):
        return answer(False, "Unknown format.", 400)
    content = json.loads(post["content_json"])
    platforms = _platforms()
    caption = _caption_from_form(content) if fmt == "post" else ""
    problem = _check_ready_to_validate(post, fmt, platforms, caption)
    if problem:
        return answer(False, problem, 400)
    try:
        when = local_to_utc(request.form.get("publish_at"))
    except ValueError as exc:
        return answer(False, str(exc), 400)
    if when and when < utc_str(utc_now()):
        return answer(False, "That time has already passed.", 400)
    names = [n for n in (_store(f, "jpg") for f in
                         request.files.getlist("image")[:10]) if n]
    if not names:
        return answer(False, "The pictures didn't come through. Wait for the "
                      "design to finish drawing, then try again.", 400)
    now = utc_str(utc_now())
    conn = db()
    cur = conn.execute(
        "INSERT INTO social_queue (post_id, format, media_kind, media, thumb,"
        " caption, platforms, status, publish_at, title, created_by,"
        " created_at, validated_by, validated_at) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (post_id, fmt, "image", json.dumps(names), names[0], caption,
         json.dumps(platforms), "scheduled" if when else "ready", when,
         _title(post, content), current_user_id(), now, current_user_id(), now))
    conn.commit()
    what = FORMATS[fmt] + ("s" if fmt == "story" and len(names) > 1 else "")
    return answer(True, f"{what} validated" + (
        f" and scheduled for {to_local(when):%a %d %b, %H:%M}." if when
        else ". Give it a time on the Schedule page."), id=cur.lastrowid)


def _title(post, content):
    return (content.get("hook_en") or "").strip()[:120] or \
        {"tip": "Tip", "area": "Area guide", "market": "Market update"}.get(
            post["kind"], "Listing")


# ---------------------------------------------------------- make a video
_RENDER_LOCK = threading.Lock()


class _RenderSlot:
    """One video at a time on the whole server (all workers): it needs
    about 250 MB while it runs. A lock file, since workers are processes."""

    def __init__(self, folder):
        self.path = os.path.join(folder, ".render.lock")

    def __enter__(self):
        deadline = time.time() + RENDER_STALE_MIN * 60
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > RENDER_STALE_MIN * 60:
                        os.remove(self.path)          # left by a dead worker
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    raise RuntimeError("Another video is still being made. "
                                       "Try again in a few minutes.")
                time.sleep(3)

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass


def _render_in_background(app, qid, job_dir, overlay, endcard, photos, video):
    def run():
        with app.app_context():
            conn = db()

            def progress(frac):
                conn.execute("UPDATE social_queue SET progress = ? WHERE id = ?",
                             (round(frac, 3), qid))
                conn.commit()
            try:
                with _RENDER_LOCK, _RenderSlot(media_folder()):
                    progress(0.01)
                    name = uuid.uuid4().hex + ".mp4"
                    out = os.path.join(media_folder(), name)
                    sv.render(out, overlay, endcard, photos=photos, video=video,
                              progress=progress)
                    thumb = uuid.uuid4().hex + ".jpg"
                    if not sv.poster(out, os.path.join(media_folder(), thumb), 1.2):
                        thumb = None
                conn.execute(
                    "UPDATE social_queue SET status = 'draft', media = ?,"
                    " thumb = ?, progress = 1, error = NULL WHERE id = ?",
                    (json.dumps([name]), thumb, qid))
                conn.commit()
            except Exception as exc:          # report it on the page
                conn.execute(
                    "UPDATE social_queue SET status = 'render_failed',"
                    " error = ? WHERE id = ?", (str(exc)[:900], qid))
                conn.commit()
            finally:
                shutil.rmtree(job_dir, ignore_errors=True)
                conn.close()
    threading.Thread(target=run, daemon=True, name=f"reel-{qid}").start()


@social_bp.route("/post/<int:post_id>/video", methods=["POST"])
@admin_required
def make_video(post_id):
    """Start making the video for a reel or video story. Answers at once
    with the queue id; the page then polls /social/queue/<id>.json."""
    post = _post_row(post_id)
    if not post:
        return answer(False, "This post no longer exists.", 404)
    if not sv.available():
        return answer(False, "The video maker (ffmpeg) isn't installed on this "
                      "server. Run: pip install imageio-ffmpeg", 500)
    import social_cards as sc
    content = json.loads(post["content_json"])
    if sc.needs_photos(post) and not content.get("photos_ok"):
        return answer(False, "Choose and approve the photos first (Step 1).", 400)
    try:
        source = json.loads(request.form.get("source") or "{}")
    except ValueError:
        source = {}
    photos, video = [], None
    if source.get("video_id"):
        ids = json.loads(post["property_ids"] or "[]")
        row = db().execute(
            "SELECT * FROM property_videos WHERE id = ?",
            (int(source["video_id"]),)).fetchone()
        if not row or row["property_id"] not in ids:
            return answer(False, "That video isn't one of this listing's.", 400)
        video = os.path.join(current_app.config["UPLOAD_FOLDER"], "videos",
                             row["filename"])
        if not os.path.isfile(video):
            return answer(False, "That video file is missing on the server.", 400)
    else:
        photos = [p for p in (_upload_path(u, "images") for u in
                              (source.get("photos") or [])[:sv.MAX_PHOTOS]) if p]
        if not photos:
            return answer(False, "Pick a listing video or some photos for the "
                          "slideshow.", 400)
    job_dir = tempfile.mkdtemp(prefix="reeljob-")
    overlay = os.path.join(job_dir, "overlay.png")
    endcard = os.path.join(job_dir, "end.jpg")
    for field, path in (("overlay", overlay), ("endcard", endcard)):
        f = request.files.get(field)
        if not f:
            shutil.rmtree(job_dir, ignore_errors=True)
            return answer(False, "The design didn't come through. Wait for it "
                          "to finish drawing, then try again.", 400)
        f.save(path)
    now = utc_str(utc_now())
    conn = db()
    cur = conn.execute(
        "INSERT INTO social_queue (post_id, format, media_kind, status,"
        " progress, source, title, created_by, created_at) VALUES"
        " (?,?,?,?,?,?,?,?,?)",
        (post_id, "reel", "video", "rendering", 0,
         json.dumps({"video_id": source.get("video_id")} if video else
                    {"photos": source.get("photos")[:sv.MAX_PHOTOS]}),
         _title(post, content), current_user_id(), now))
    conn.commit()
    qid = cur.lastrowid
    _render_in_background(current_app._get_current_object(), qid, job_dir,
                          overlay, endcard, photos, video)
    return answer(True, "Making the video…", id=qid)


@social_bp.route("/queue/<int:qid>.json")
@admin_required
def queue_status(qid):
    row = _row(qid)
    if not row:
        return answer(False, "Not found.", 404)
    if row["status"] == "rendering":
        started = datetime.strptime(row["created_at"][:19], STAMP)
        if utc_now() - started > timedelta(minutes=RENDER_STALE_MIN):
            db().execute("UPDATE social_queue SET status = 'render_failed',"
                         " error = ? WHERE id = ? AND status = 'rendering'",
                         ("The server restarted while making the video. "
                          "Make it again.", qid))
            db().commit()
            row = _row(qid)
    v = item_view(row)
    return answer(True, v["status_label"], status=row["status"],
                  progress=row["progress"] or 0, error=row["error"],
                  video_url=v["video_url"], thumb_url=v["thumb_url"])


@social_bp.route("/queue/<int:qid>/validate", methods=["POST"])
@admin_required
def validate_video(qid):
    """Validate a finished video as a reel or a video story. The same video
    can be validated as both: the second time adds a copy."""
    row = _row(qid)
    if not row or row["media_kind"] != "video" or row["status"] in (
            "rendering", "render_failed", "cancelled"):
        return answer(False, "Make the video first.", 400)
    post = _post_row(row["post_id"])
    if not post:
        return answer(False, "This post no longer exists.", 404)
    fmt = request.form.get("format")
    if fmt not in ("reel", "story"):
        return answer(False, "Unknown format.", 400)
    content = json.loads(post["content_json"])
    platforms = _platforms()
    caption = _caption_from_form(content) if fmt == "reel" else ""
    problem = _check_ready_to_validate(post, fmt, platforms, caption)
    if problem:
        return answer(False, problem, 400)
    try:
        when = local_to_utc(request.form.get("publish_at"))
    except ValueError as exc:
        return answer(False, str(exc), 400)
    if when and when < utc_str(utc_now()):
        return answer(False, "That time has already passed.", 400)
    now, me = utc_str(utc_now()), current_user_id()
    status = "scheduled" if when else "ready"
    conn = db()
    if row["status"] == "draft":
        conn.execute(
            "UPDATE social_queue SET format = ?, caption = ?, platforms = ?,"
            " status = ?, publish_at = ?, validated_by = ?, validated_at = ?"
            " WHERE id = ?", (fmt, caption, json.dumps(platforms), status,
                              when, me, now, qid))
        new_id = qid
    else:
        new_id = conn.execute(
            "INSERT INTO social_queue (post_id, format, media_kind, media,"
            " thumb, caption, platforms, status, publish_at, source, title,"
            " created_by, created_at, validated_by, validated_at) VALUES"
            " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["post_id"], fmt, "video", row["media"], row["thumb"], caption,
             json.dumps(platforms), status, when, row["source"], row["title"],
             me, now, me, now)).lastrowid
    conn.commit()
    label = "Reel" if fmt == "reel" else "Video story"
    return answer(True, f"{label} validated" + (
        f" and scheduled for {to_local(when):%a %d %b, %H:%M}." if when
        else ". Give it a time on the Schedule page."), id=new_id)


ALLOWED_VIDEOS = {".mp4", ".mov", ".m4v", ".webm", ".3gp"}


@social_bp.route("/post/<int:post_id>/listing-video", methods=["POST"])
@admin_required
def upload_listing_video(post_id):
    """Add a video to the listing (it stays on the listing too)."""
    back = url_for("social.review", post_id=post_id, tab="video") + "#design"
    post = _post_row(post_id)
    f = request.files.get("video")
    try:
        pid = int(request.form.get("property_id", ""))
    except ValueError:
        pid = None
    if not post or pid not in json.loads(post["property_ids"] or "[]"):
        flash("Pick the listing the video belongs to.", "error")
        return redirect(back)
    if not f or not f.filename or \
            os.path.splitext(f.filename)[1].lower() not in ALLOWED_VIDEOS:
        flash("Choose a video file: MP4 or MOV.", "error")
        return redirect(back)
    from werkzeug.utils import secure_filename
    from db import now as db_now
    ext = os.path.splitext(f.filename)[1].lower()
    name = f"{uuid.uuid4().hex}{ext}"
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "videos")
    os.makedirs(folder, exist_ok=True)
    f.save(os.path.join(folder, name))
    conn = db()
    conn.execute("INSERT INTO property_videos (property_id, filename,"
                 " original_name, created_at) VALUES (?,?,?,?)",
                 (pid, name, secure_filename(f.filename), db_now()))
    conn.commit()
    flash("Video added to the listing. Pick it below and make the video.",
          "success")
    return redirect(back)


def listing_videos(post_row):
    ids = json.loads(post_row["property_ids"] or "[]")
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = db().execute(
        "SELECT v.id, v.filename, v.original_name, v.property_id, p.ref"
        f" FROM property_videos v JOIN properties p ON p.id = v.property_id"
        f" WHERE v.property_id IN ({marks}) ORDER BY v.id", ids).fetchall()
    return [{"id": r["id"], "url": f"/uploads/videos/{r['filename']}",
             "name": r["original_name"] or r["filename"], "ref": r["ref"],
             "property_id": r["property_id"]} for r in rows]


# ------------------------------------------------------------- schedule
@social_bp.route("/schedule")
@admin_required
def schedule():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM social_queue WHERE status IN ('ready','scheduled',"
        "'posting','draft','render_failed') OR (status IN ('posted','partial',"
        "'failed') AND COALESCE(posted_at, created_at) > ?)"
        " ORDER BY publish_at IS NULL, publish_at, id",
        (utc_str(utc_now() - timedelta(days=14)),)).fetchall()
    items = [item_view(r) for r in rows]
    ready = [i for i in items if i["status"] == "ready"]
    scheduled = [i for i in items if i["status"] in ("scheduled", "posting")]
    drafts = [i for i in items if i["status"] in ("draft", "render_failed")]
    done = sorted((i for i in items if i["status"] in ("posted", "partial",
                                                       "failed")),
                  key=lambda i: i["posted_at"] or i["created_at"], reverse=True)
    # The next 14 days, so empty days stand out.
    today = local_today()
    days = []
    for n in range(14):
        d = today + timedelta(days=n)
        on = [i for i in scheduled if i["local_at"] and i["local_at"].date() == d]
        days.append({"date": d, "items": on,
                     "counts": {f: sum(1 for i in on if i["format"] == f)
                                for f in FORMATS}})
    return render_template(
        "social_schedule.html", ready=ready, scheduled=scheduled,
        drafts=drafts, done=done, days=days, formats=FORMATS,
        platform_names=PLATFORM_NAMES, can_publish=S.can_publish(),
        scheduler_on=scheduler_enabled(),
        tomorrow=(today + timedelta(days=1)).isoformat(),
        now_local=(utc_now() + _offset()).strftime("%Y-%m-%dT%H:%M"))


def _back():
    return redirect(request.form.get("next") or url_for("social.schedule"))


@social_bp.route("/queue/<int:qid>/time", methods=["POST"])
@admin_required
def set_time(qid):
    row = _row(qid)
    if not row or row["status"] not in ("ready", "scheduled", "failed",
                                        "partial"):
        flash("That item can't be rescheduled now.", "error")
        return _back()
    try:
        when = None if request.form.get("clear") else \
            local_to_utc(request.form.get("publish_at"))
    except ValueError as exc:
        flash(str(exc), "error")
        return _back()
    if not when and not request.form.get("clear"):
        flash("Pick a date and time first.", "error")
        return _back()
    if when and when < utc_str(utc_now() - timedelta(minutes=1)):
        flash("That time has already passed. Pick a time ahead, or use "
              "Post now.", "error")
        return _back()
    conn = db()
    conn.execute("UPDATE social_queue SET publish_at = ?, status = ?,"
                 " error = CASE WHEN ? IS NULL THEN error ELSE NULL END"
                 " WHERE id = ? AND status != 'posting'",
                 (when, "scheduled" if when else "ready", when, qid))
    conn.commit()
    flash(f"Scheduled for {to_local(when):%a %d %b, %H:%M}." if when
          else "Time removed. It's back in Ready to schedule.", "success")
    return _back()


@social_bp.route("/queue/<int:qid>/remove", methods=["POST"])
@admin_required
def remove_item(qid):
    conn = db()
    row = _row(qid)
    got = conn.execute("UPDATE social_queue SET status = 'cancelled'"
                       " WHERE id = ? AND status NOT IN ('posting','posted',"
                       "'rendering')", (qid,)).rowcount
    conn.commit()
    if got:
        _drop_files(conn, row)
    flash("Removed from the schedule." if got else
          "That one is publishing or already posted, so it stays.",
          "info" if got else "error")
    return _back()


def _drop_files(conn, row):
    """Delete a removed item's pictures / video, unless another item (the
    same video validated as both a reel and a story) still uses them."""
    names = json.loads(row["media"] or "[]") + ([row["thumb"]] if row["thumb"] else [])
    for name in set(names):
        used = conn.execute(
            "SELECT 1 FROM social_queue WHERE id != ? AND status != 'cancelled'"
            " AND (media LIKE ? OR thumb = ?)",
            (row["id"], f'%"{name}"%', name)).fetchone()
        if not used and MEDIA_NAME.match(name):
            try:
                os.remove(os.path.join(media_folder(), name))
            except OSError:
                pass


@social_bp.route("/queue/<int:qid>/now", methods=["POST"])
@admin_required
def post_now(qid):
    """Publish straight away: due now, and the publisher is woken at once
    (it runs in the background, so this page doesn't wait on Meta)."""
    if not S.can_publish():
        flash("Direct posting isn't set up. Fill in Social settings first.",
              "error")
        return _back()
    conn = db()
    got = conn.execute(
        "UPDATE social_queue SET status = 'scheduled', publish_at = ?,"
        " error = NULL WHERE id = ? AND status IN ('ready','scheduled',"
        "'failed','partial')", (utc_str(utc_now()), qid)).rowcount
    conn.commit()
    if got:
        app = current_app._get_current_object()
        threading.Thread(target=_tick_safely, args=(app,), daemon=True).start()
        flash("Publishing now. Refresh in a minute to see the links.", "success")
    else:
        flash("That item can't be posted now.", "error")
    return _back()


@social_bp.route("/schedule/bulk", methods=["POST"])
@admin_required
def bulk_schedule():
    """One item a day from the start date, at the time given, in the order
    ticked. Days that already have the same kind (post, story or reel)
    scheduled are skipped when asked, so the page keeps one of each a day."""
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    start, at = request.form.get("start", ""), request.form.get("time", "")
    skip = bool(request.form.get("skip_busy"))
    if not ids:
        flash("Tick the items to schedule first.", "error")
        return _back()
    try:
        day = datetime.strptime(start, "%Y-%m-%d").date()
        hh, mm = (int(x) for x in at.split(":")[:2])
    except ValueError:
        flash("Pick a start date and a time.", "error")
        return _back()
    conn = db()
    taken = {}
    for r in conn.execute("SELECT format, publish_at FROM social_queue"
                          " WHERE status IN ('scheduled','posting')"):
        lt = to_local(r["publish_at"])
        if lt:
            taken.setdefault(r["format"], set()).add(lt.date())
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM social_queue WHERE id IN ({','.join('?' for _ in ids)})"
        " AND status IN ('ready','scheduled')", ids)}
    now_local = utc_now() + _offset()
    done, cursor = [], {}
    for i in ids:
        r = rows.get(i)
        if not r:
            continue
        d = cursor.get(r["format"], day)
        while True:
            when_local = datetime(d.year, d.month, d.day, hh, mm)
            busy = skip and d in taken.get(r["format"], set())
            if when_local > now_local and not busy:
                break
            d += timedelta(days=1)
        utc = utc_str(when_local - _offset())
        conn.execute("UPDATE social_queue SET publish_at = ?, status ="
                     " 'scheduled', error = NULL WHERE id = ?", (utc, i))
        taken.setdefault(r["format"], set()).add(d)
        cursor[r["format"]] = d + timedelta(days=1)
        done.append(when_local)
    conn.commit()
    if done:
        flash(f"Scheduled {len(done)}: from {min(done):%a %d %b} to "
              f"{max(done):%a %d %b}, at {hh:02d}:{mm:02d}.", "success")
    else:
        flash("Nothing was scheduled.", "error")
    return _back()


# ---------------------------------------------------------------- publish
def _token():
    return cfg("fb_page_token")


def _rupload(upload_url, file_url):
    """Facebook video upload by link: Facebook fetches the file itself."""
    req = urllib.request.Request(upload_url, data=b"", method="POST", headers={
        "Authorization": "OAuth " + _token(), "file_url": file_url})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"API returned {exc.code}: "
                           f"{exc.read().decode('utf-8', 'replace')[:300]}")


def _ig_publish(params, max_wait):
    params = dict(params, access_token=_token())
    container = S._graph(f"{cfg('ig_user_id')}/media", params)["id"]
    S._wait_until_ready(container, max_wait=max_wait)
    res = S._graph(f"{cfg('ig_user_id')}/media_publish",
                   {"creation_id": container, "access_token": _token()})
    return res.get("id", "")


def ig_story(urls, video):
    if not cfg("ig_user_id"):
        raise RuntimeError("The Instagram account ID is not set.")
    for url in urls:
        _ig_publish({"media_type": "STORIES",
                     ("video_url" if video else "image_url"): url},
                    300 if video else 60)
    return "https://www.instagram.com/stories/"


def ig_reel(caption, url):
    if not cfg("ig_user_id"):
        raise RuntimeError("The Instagram account ID is not set.")
    media_id = _ig_publish({"media_type": "REELS", "video_url": url,
                            "caption": caption, "share_to_feed": "true"}, 300)
    return S._graph_get(media_id, "permalink").get("permalink") or \
        "https://www.instagram.com/"


def fb_story(urls, video):
    page = cfg("fb_page_id")
    for url in urls:
        if video:
            start = S._graph(f"{page}/video_stories",
                             {"upload_phase": "start", "access_token": _token()})
            _rupload(start["upload_url"], url)
            S._graph(f"{page}/video_stories",
                     {"upload_phase": "finish", "video_id": start["video_id"],
                      "access_token": _token()})
        else:
            photo = S._graph(f"{page}/photos", {"url": url, "published": "false",
                                                "access_token": _token()})
            S._graph(f"{page}/photo_stories",
                     {"photo_id": photo["id"], "access_token": _token()})
    return f"https://www.facebook.com/stories/{page}"


def fb_reel(caption, url):
    page = cfg("fb_page_id")
    start = S._graph(f"{page}/video_reels",
                     {"upload_phase": "start", "access_token": _token()})
    vid = start["video_id"]
    _rupload(start.get("upload_url") or
             f"https://rupload.facebook.com/video-upload/v21.0/{vid}", url)
    S._graph(f"{page}/video_reels",
             {"upload_phase": "finish", "video_id": vid,
              "video_state": "PUBLISHED", "description": caption,
              "access_token": _token()})
    return f"https://www.facebook.com/reel/{vid}"


def publish_item(item):
    """Publish one queue item to the platforms it still needs.
    Returns (results, errors) for this attempt."""
    urls = [media_url(n, external=True) for n in item["media"]]
    already = item["results"]
    todo = [p for p in item["platforms"] if p not in already]
    results, errors = {}, {}
    if not urls:
        return results, {"all": "The pictures or video for this item are missing."}
    problem = S.check_public_image(urls[0])
    if problem:
        return results, {"all": problem}
    video = item["media_kind"] == "video"
    for p in todo:
        try:
            if item["format"] == "post":
                results[p] = (S.publish_facebook if p == "facebook" else
                              S.publish_instagram)(item["caption"] or "", urls)
            elif item["format"] == "story":
                results[p] = (fb_story if p == "facebook" else ig_story)(urls, video)
            else:
                results[p] = (fb_reel if p == "facebook" else ig_reel)(
                    item["caption"] or "", urls[0])
        except (RuntimeError, KeyError, ValueError) as exc:
            errors[p] = S._friendly_meta_error(str(exc))
    return results, errors


# -------------------------------------------------------------- scheduler
def scheduler_enabled():
    """On by default on the live server (Render sets RENDER=true) and off
    elsewhere, so an office copy restored from a backup never posts the same
    schedule a second time. SOCIAL_SCHEDULER=1 or 0 overrides."""
    flag = os.environ.get("SOCIAL_SCHEDULER", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(os.environ.get("RENDER"))


def _claim_next(conn):
    """Take the next due item, or None. The UPDATE only succeeds for one
    worker, however many look at the same moment."""
    now = utc_str(utc_now())
    row = conn.execute(
        "SELECT id FROM social_queue WHERE status = 'scheduled'"
        " AND publish_at <= ? ORDER BY publish_at, id LIMIT 1", (now,)).fetchone()
    if not row:
        return None
    got = conn.execute(
        "UPDATE social_queue SET status = 'posting', claimed_at = ?"
        " WHERE id = ? AND status = 'scheduled'", (now, row["id"])).rowcount
    conn.commit()
    return row["id"] if got else False


def _notify(item, message):
    who = item.get("validated_by") or item.get("created_by")
    if not who:
        return
    try:
        from db import notify
        notify(who, message, "/social/schedule")
    except Exception as exc:              # a note can never stop publishing
        print(f"  Social schedule note skipped: {exc}")


def tick(app=None):
    """One pass: tidy up stuck items, then publish whatever is due."""
    conn = db()
    ensure_table(conn)
    now = utc_now()
    # A publish that never finished (the server restarted mid-way): it may
    # have gone out, so it is never retried by itself.
    for r in conn.execute(
            "SELECT * FROM social_queue WHERE status = 'posting'"
            " AND claimed_at < ?",
            (utc_str(now - timedelta(minutes=POSTING_STALE_MIN)),)).fetchall():
        conn.execute("UPDATE social_queue SET status = 'failed', error = ?"
                     " WHERE id = ? AND status = 'posting'",
                     ("Publishing was interrupted (the server restarted). "
                      "Check the Page and Instagram: it may have posted. "
                      "If not, use Post now.", r["id"]))
        conn.commit()
        _notify(dict(r), f"A scheduled {r['format']} may not have posted: "
                f"{r['title'] or ''}. Check the schedule.")
    # Far too late (the server was off): ask for a new time instead.
    for r in conn.execute(
            "SELECT * FROM social_queue WHERE status = 'scheduled'"
            " AND publish_at < ?",
            (utc_str(now - timedelta(hours=MISSED_HOURS)),)).fetchall():
        conn.execute("UPDATE social_queue SET status = 'ready', publish_at ="
                     " NULL, error = ? WHERE id = ? AND status = 'scheduled'",
                     ("Missed its time ("
                      + to_local(r["publish_at"]).strftime("%a %d %b, %H:%M")
                      + ") while the server was off. Pick a new time.",
                      r["id"]))
        conn.commit()
        _notify(dict(r), f"A scheduled {r['format']} missed its time: "
                f"{r['title'] or ''}. Pick a new time.")
    for _ in range(5):                    # a few per pass at most
        qid = _claim_next(conn)
        if qid is None:
            break
        if qid is False:
            continue                      # another worker took it
        item = item_view(_row(qid))
        if not S.can_publish():
            results, errors = {}, {"all": "Direct posting isn't set up "
                                   "(Social settings)."}
        else:
            try:
                results, errors = publish_item(item)
            except Exception as exc:      # never leave it stuck in 'posting'
                results, errors = {}, {"all": str(exc)[:500]}
        merged = {**item["results"], **results}
        if all(p in merged for p in item["platforms"]):
            status = "posted"
        elif merged:
            status = "partial"
        else:
            status = "failed"
        conn.execute(
            "UPDATE social_queue SET status = ?, results = ?, error = ?,"
            " posted_at = ? WHERE id = ?",
            (status, json.dumps(merged),
             "; ".join(f"{PLATFORM_NAMES.get(k, 'Post')}: {v}"
                       for k, v in errors.items()) or None,
             utc_str(utc_now()), qid))
        conn.commit()
        if status != "posted":
            _notify(item, f"Scheduled {item['format_label'].lower()} "
                    f"{'partly ' if status == 'partial' else ''}failed: "
                    f"{item['title'] or ''}. Open the schedule to retry.")


def _tick_safely(app):
    try:
        with app.app_context():
            tick(app)
    except Exception as exc:
        print(f"  Social schedule: {exc}")


def start_scheduler(app):
    if not scheduler_enabled():
        return None

    def loop():
        time.sleep(20)                    # let start-up settle
        while True:
            _tick_safely(app)
            time.sleep(TICK_SECONDS)

    t = threading.Thread(target=loop, daemon=True, name="social-schedule")
    t.start()
    return t
