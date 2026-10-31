"""Music for reels and video stories: the free library, links, removal.

The Music list on a post's Video tab is the built-in track plus the team's
own tracks in uploads/music (see social_video.music_tracks). This module adds
three ways to put tracks there, besides uploading a file:

* Free library: searches Openverse (api.openverse.org, run by WordPress.org),
  which indexes music from Jamendo, Freesound, ccMixter and Wikimedia. Only
  licences that allow business use and editing are asked for: CC0, Public
  Domain and CC BY. CC BY-SA is left out on purpose: it would require the
  reel itself to be shared under the same licence. CC BY asks for a credit;
  the credit is saved with the track and added to the end of the reel's
  caption when it is validated (stories have no caption, so the page warns).
  No key is needed. Anonymous searches are rate-limited, so the page searches
  only when someone presses Search.
* From a link: paste the address of an MP3/WAV/M4A file (for example the
  download link of a track made in Artlist) and the server fetches it.
* Remove: deletes one of the team's tracks (and its credit file).

Admins only, like the rest of Social.
"""
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from flask import current_app, jsonify, request

import social_video as sv
from ai_social import social_bp
from auth import admin_required

OPENVERSE = "https://api.openverse.org/v1/audio/"
LICENSES = "cc0,pdm,by"            # business use + editing allowed
LICENSE_NAMES = {"cc0": "CC0", "pdm": "Public Domain", "by": "CC BY"}
MIN_SECONDS = 15                   # shorter clips are sound effects, not music
MAX_BYTES = 60 * 1024 * 1024       # a 5-minute WAV is about 50 MB
SEARCH_TIMEOUT = 25
DOWNLOAD_TIMEOUT = 90
AGENT = "PlannedCRM/1.0 (+https://plannedrealestate.com)"
CONTENT_EXT = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav",
               "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mp4": ".m4a",
               "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/ogg": ".ogg"}


def _ok(**extra):
    return jsonify(dict(ok=True, **extra))


def _err(message, code=400):
    return jsonify(ok=False, error=message), code


def _folder():
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "music")
    os.makedirs(folder, exist_ok=True)
    return folder


def _safe_label(text):
    text = re.sub(r"[^\w\- ]+", "", str(text or ""), flags=re.UNICODE).strip()
    return re.sub(r"\s+", "_", text)[:60] or "track"


def _public_host(url):
    """Only http(s) addresses on the public internet (not this server or the
    office network)."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _download(url, label, info=None):
    """Fetch an audio file into the music folder; check ffmpeg can read it.
    Returns the track id ("u:<file>")."""
    if not _public_host(url):
        raise RuntimeError("That link can't be used. Paste a full https:// "
                           "address of the music file.")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        resp = urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"The music couldn't be downloaded (the site "
                           f"answered {exc.code}). If it's a link that "
                           "expires, get a fresh one.")
    except (urllib.error.URLError, OSError):
        raise RuntimeError("The music couldn't be downloaded. Check the link "
                           "and try again.")
    with resp:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = os.path.splitext(urllib.parse.urlparse(resp.geturl()).path)[1].lower()
        if ext not in sv.MUSIC_EXT:
            ext = CONTENT_EXT.get(ctype, "")
        if not ext and ctype in ("application/octet-stream", "binary/octet-stream", ""):
            ext = ".mp3"                    # ffmpeg checks it below anyway
        if ext not in sv.MUSIC_EXT:
            raise RuntimeError("That link isn't a music file (MP3, WAV, M4A "
                               "or OGG).")
        name = f"{uuid.uuid4().hex[:8]}__{_safe_label(label)}{ext}"
        path = os.path.join(_folder(), name)
        size = 0
        try:
            with open(path, "wb") as out:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise RuntimeError("That file is over 60 MB. Use a "
                                           "shorter track or an MP3.")
                    out.write(chunk)
        except (OSError, urllib.error.URLError):
            if os.path.exists(path):
                os.remove(path)
            raise RuntimeError("The download was cut off. Try again.")
        except RuntimeError:
            if os.path.exists(path):
                os.remove(path)
            raise
    try:
        seconds = sv.probe_audio(path)
    except RuntimeError:
        os.remove(path)
        raise
    info = dict(info or {}, seconds=round(seconds, 1))
    sv.save_music_info(path, info)
    return "u:" + name, seconds


def _openverse(path_or_query, tries=2):
    """One call to the library. Its servers now and then answer 5xx for a
    moment, so that is tried once more before giving up."""
    req = urllib.request.Request(OPENVERSE + path_or_query,
                                 headers={"User-Agent": AGENT,
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 and tries > 1:
            time.sleep(1.5)
            return _openverse(path_or_query, tries - 1)
        if exc.code == 429:
            raise RuntimeError("The free library has had too many searches "
                               "from here for now. Try again in a few minutes.")
        if exc.code == 404:
            raise RuntimeError("That track is no longer in the free library.")
        raise RuntimeError(f"The free library answered with an error "
                           f"({exc.code}). Try again shortly.")
    except (urllib.error.URLError, OSError, ValueError):
        raise RuntimeError("The free library didn't answer in time. Try again, "
                           "or try other words.")


def _credit(r):
    """The credit line CC BY asks for; nothing for CC0 / Public Domain."""
    if r.get("license") != "by":
        return ""
    lic = f"CC BY {r.get('license_version') or ''}".strip()
    url = (r.get("license_url") or "").replace("https://", "").rstrip("/")
    return (f"🎵 Music: \"{r.get('title') or 'Untitled'}\" by "
            f"{r.get('creator') or 'unknown'} ({lic}{', ' + url if url else ''})")


def _track_view(r):
    seconds = round((r.get("duration") or 0) / 1000)
    return {"id": r.get("id"), "title": r.get("title") or "Untitled",
            "creator": r.get("creator") or "", "seconds": seconds,
            "license": LICENSE_NAMES.get(r.get("license"), r.get("license")),
            "needs_credit": r.get("license") == "by",
            "source": (r.get("source") or "").capitalize(),
            "preview": r.get("url"), "page": r.get("foreign_landing_url")}


@social_bp.route("/music/search")
@admin_required
def music_search():
    """Search the free library: music only, business-safe licences, at
    least MIN_SECONDS long."""
    q = (request.args.get("q") or "").strip()[:80]
    if not q:
        return _err("Type a few words: a mood (calm, upbeat), a style "
                    "(piano, corporate, oud) or a use (real estate, luxury).")
    try:
        page = max(1, min(int(request.args.get("page") or 1), 10))
    except ValueError:
        page = 1
    params = urllib.parse.urlencode({
        "q": q, "license": LICENSES, "category": "music",
        "page_size": 20, "page": page, "mature": "false"})
    try:
        data = _openverse("?" + params)
    except RuntimeError as exc:
        return _err(str(exc), 502)
    results = [_track_view(r) for r in data.get("results") or []
               if r.get("url") and (r.get("duration") or 0) >= MIN_SECONDS * 1000
               and r.get("license") in LICENSE_NAMES]
    return _ok(results=results, page=page,
               more=page < min(int(data.get("page_count") or 1), 10))


@social_bp.route("/music/library-add", methods=["POST"])
@admin_required
def music_library_add():
    """Add a free-library track to the team's music. The details are read
    again from the library (not from the page), so the licence and credit
    saved with it are the library's own."""
    tid = (request.get_json(silent=True) or {}).get("id") or ""
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", tid):
        return _err("Pick a track from the search results.")
    try:
        r = _openverse(f"{tid}/")
    except RuntimeError as exc:
        return _err(str(exc), 502)
    if r.get("license") not in LICENSE_NAMES or not r.get("url"):
        return _err("That track's licence doesn't allow use in business "
                    "videos. Pick another one.")
    info = {"title": r.get("title") or "Untitled", "creator": r.get("creator") or "",
            "license": LICENSE_NAMES[r["license"]],
            "license_version": r.get("license_version") or "",
            "license_url": r.get("license_url") or "", "credit": _credit(r),
            "landing": r.get("foreign_landing_url") or "",
            "source": "Free library (" + (r.get("source") or "openverse") + ")",
            "openverse_id": tid}
    try:
        track_id, seconds = _download(r["url"], info["title"], info)
    except RuntimeError as exc:
        return _err(str(exc), 502)
    return _ok(track={"id": track_id, "name": info["title"] + (
        " - " + info["creator"] if info["creator"] else ""),
        "url": "/uploads/music/" + track_id[2:], "credit": info["credit"],
        "removable": True}, seconds=round(seconds))


@social_bp.route("/music/link-add", methods=["POST"])
@admin_required
def music_link_add():
    """Add a track from the address of a music file (e.g. an Artlist
    download link)."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    name = (body.get("name") or "").strip()[:80]
    if not url:
        return _err("Paste the link of the music file.")
    if not name:
        tail = os.path.splitext(os.path.basename(
            urllib.parse.urlparse(url).path))[0]
        name = tail.replace("-", " ").replace("_", " ").strip() or "Track"
    info = {"title": name, "creator": "", "credit": "",
            "source": "Link (" + (urllib.parse.urlparse(url).hostname or "") + ")"}
    try:
        track_id, seconds = _download(url, name, info)
    except RuntimeError as exc:
        return _err(str(exc))
    return _ok(track={"id": track_id, "name": name,
                      "url": "/uploads/music/" + track_id[2:], "credit": "",
                      "removable": True}, seconds=round(seconds))


@social_bp.route("/music/remove", methods=["POST"])
@admin_required
def music_remove():
    """Delete one of the team's tracks. Videos already made keep their
    sound; only the list loses it."""
    tid = (request.get_json(silent=True) or {}).get("id") or ""
    if not tid.startswith("u:"):
        return _err("Built-in music can't be removed.")
    path = sv.music_path(tid, current_app.static_folder,
                         current_app.config["UPLOAD_FOLDER"])
    if not path:
        return _err("That track is already gone.", 404)
    os.remove(path)
    side = os.path.splitext(path)[0] + ".json"
    if os.path.exists(side):
        os.remove(side)
    return _ok()
