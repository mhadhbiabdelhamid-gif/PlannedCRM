"""TikTok publishing (Content Posting API), next to Facebook and Instagram.

What goes to TikTok:
* reels (the 9:16 videos made on a post's Video tab) as TikTok videos;
* feed posts (the 4:5 pictures) as TikTok photo posts.
TikTok has no stories API, so stories stay Facebook / Instagram only.

Setting it up (Social settings → TikTok):
1. On developers.tiktok.com create an app, add the products "Login Kit" and
   "Content Posting API" (with Direct Post on), and the scopes
   user.info.basic, video.publish, video.upload. Add the redirect URI shown in
   Social settings (…/social/tiktok/callback).
2. Paste the app's Client key and Client secret in Social settings and click
   Connect TikTok, then sign in with the company's TikTok account.
3. Until TikTok has audited the app, everything it posts is visible only to
   the account itself ("Only me"). Apply for the audit in the developer
   portal; this page already shows what the audit asks for (the account
   name, a privacy choice with no default, comments on/off, the promotional
   content label and the music usage confirmation).
4. Photo posts: TikTok downloads the pictures itself, only from a verified
   address. In the developer portal add the URL prefix shown in Social
   settings; if it gives you a verification file, paste its name and content
   in Social settings and the CRM serves it. (Videos are uploaded directly
   and need none of this.)

Tokens: the access token lasts 24 hours and is renewed automatically with the
refresh token, which lasts a year; reconnect once a year.
"""
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import (abort, current_app, flash, jsonify, redirect, request,
                   session, url_for, Response)

import ai_social as S
from ai_social import social_bp, cfg, set_cfg
from auth import admin_required

API = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.publish,video.upload"
MUSIC_TERMS = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
BRAND_TERMS = "https://www.tiktok.com/legal/page/global/bc-policy/en"
PRIVACY_LABELS = {
    "PUBLIC_TO_EVERYONE": "Everyone",
    "MUTUAL_FOLLOW_FRIENDS": "Friends",
    "FOLLOWER_OF_CREATOR": "Followers",
    "SELF_ONLY": "Only me",
}
KEYS = ("tiktok_client_key", "tiktok_client_secret", "tiktok_access_token",
        "tiktok_refresh_token", "tiktok_expires_at", "tiktok_refresh_expires_at",
        "tiktok_open_id", "tiktok_username", "tiktok_nickname",
        "tiktok_verify_name", "tiktok_verify_content")
CHUNK = 10 * 1024 * 1024          # TikTok: chunks of 5-64 MB; under 5 MB in one
STATUS_WAIT = 240                 # seconds to wait for TikTok to finish


# ------------------------------------------------------------------ state
def configured():
    return bool(cfg("tiktok_client_key") and cfg("tiktok_client_secret"))


def connected():
    return bool(configured() and cfg("tiktok_refresh_token"))


def redirect_uri():
    return S.public_base() + "/social/tiktok/callback"


def photo_prefix():
    return S.public_base() + "/social/m/"


def status():
    """For the settings page."""
    left = None
    try:
        left = int((float(cfg("tiktok_refresh_expires_at") or 0) - time.time())
                   // 86400)
    except ValueError:
        pass
    return {"configured": configured(), "connected": connected(),
            "client_key": cfg("tiktok_client_key"),
            "has_secret": bool(cfg("tiktok_client_secret")),
            "username": cfg("tiktok_username"), "nickname": cfg("tiktok_nickname"),
            "redirect_uri": redirect_uri() if S.public_base() else "",
            "photo_prefix": photo_prefix() if S.public_base() else "",
            "verify_name": cfg("tiktok_verify_name"),
            "days_left": left}


# -------------------------------------------------------------------- http
def _form_post(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"TikTok answered {exc.code}: "
                           f"{exc.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Couldn't reach TikTok: {exc.reason}")


def _save_tokens(tok):
    if tok.get("error"):
        raise RuntimeError("TikTok refused the sign-in: "
                           f"{tok.get('error_description') or tok['error']}")
    now = time.time()
    set_cfg("tiktok_access_token", tok["access_token"])
    set_cfg("tiktok_refresh_token", tok.get("refresh_token") or
            cfg("tiktok_refresh_token"))
    set_cfg("tiktok_expires_at", str(now + int(tok.get("expires_in", 86400))))
    if tok.get("refresh_expires_in"):
        set_cfg("tiktok_refresh_expires_at",
                str(now + int(tok["refresh_expires_in"])))
    if tok.get("open_id"):
        set_cfg("tiktok_open_id", tok["open_id"])


def access_token():
    """A valid access token, renewed with the refresh token when due."""
    if not connected():
        raise RuntimeError("TikTok isn't connected (Social settings → TikTok).")
    try:
        expires = float(cfg("tiktok_expires_at") or 0)
    except ValueError:
        expires = 0
    if time.time() > expires - 300:
        _save_tokens(_form_post(f"{API}/oauth/token/", {
            "client_key": cfg("tiktok_client_key"),
            "client_secret": cfg("tiktok_client_secret"),
            "grant_type": "refresh_token",
            "refresh_token": cfg("tiktok_refresh_token")}))
    return cfg("tiktok_access_token")


def _api(path, body):
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + access_token(),
                 "Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            out = json.loads(raw)
        except ValueError:
            raise RuntimeError(f"TikTok answered {exc.code}: {raw[:300]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Couldn't reach TikTok: {exc.reason}")
    err = out.get("error") or {}
    if err.get("code") not in (None, "", "ok"):
        raise RuntimeError(friendly_error(err))
    return out.get("data") or {}


def friendly_error(err):
    code, msg = err.get("code", ""), err.get("message", "")
    plain = {
        "access_token_invalid": "The TikTok sign-in has expired. Reconnect "
                                "TikTok in Social settings.",
        "scope_not_authorized": "The TikTok app isn't allowed to post. Check "
                                "the scopes video.publish and video.upload, "
                                "then reconnect.",
        "spam_risk_too_many_posts": "TikTok's daily posting limit for this "
                                    "account is reached. Try again tomorrow.",
        "spam_risk_user_banned_from_posting": "TikTok has blocked this account "
                                              "from posting for now.",
        "unaudited_client_can_only_post_to_private_accounts":
            "The TikTok app isn't audited yet, so it can only post as "
            "\"Only me\". Choose Only me, or apply for TikTok's audit.",
        "url_ownership_unverified": "TikTok can't take pictures from this "
                                    "address until it is verified (Social "
                                    "settings → TikTok → photo posts).",
        "privacy_level_option_mismatch": "That privacy choice isn't allowed "
                                         "for this account. Pick another.",
    }.get(code)
    return f"{plain} ({code})" if plain else f"TikTok: {msg or code}"


# ------------------------------------------------------------------ routes
@social_bp.route("/tiktok/keys", methods=["POST"])
@admin_required
def tiktok_keys():
    f = request.form
    if f.get("tiktok_client_key", "").strip():
        set_cfg("tiktok_client_key", f["tiktok_client_key"].strip())
    if f.get("tiktok_client_secret", "").strip():
        set_cfg("tiktok_client_secret", f["tiktok_client_secret"].strip())
    name = os.path.basename(f.get("tiktok_verify_name", "").strip())
    set_cfg("tiktok_verify_name", name)
    if f.get("tiktok_verify_content", "").strip() or not name:
        set_cfg("tiktok_verify_content", f.get("tiktok_verify_content", "").strip())
    flash("TikTok settings saved.", "success")
    return redirect(url_for("social.settings") + "#tiktok")


@social_bp.route("/tiktok/connect")
@admin_required
def tiktok_connect():
    if not configured():
        flash("Paste the TikTok app's Client key and Client secret first.", "error")
        return redirect(url_for("social.settings") + "#tiktok")
    if not S.public_base():
        flash("Set the Public address first: TikTok sends you back to it.", "error")
        return redirect(url_for("social.settings"))
    state = secrets.token_urlsafe(16)
    session["tiktok_state"] = state
    return redirect(AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": cfg("tiktok_client_key"), "scope": SCOPES,
        "response_type": "code", "redirect_uri": redirect_uri(),
        "state": state}))


@social_bp.route("/tiktok/callback")
@admin_required
def tiktok_callback():
    back = url_for("social.settings") + "#tiktok"
    if request.args.get("error"):
        flash("TikTok sign-in was cancelled: "
              + (request.args.get("error_description") or request.args["error"]),
              "error")
        return redirect(back)
    if not request.args.get("state") or \
            request.args.get("state") != session.pop("tiktok_state", None):
        flash("That TikTok sign-in didn't come from this page. Try Connect again.",
              "error")
        return redirect(back)
    try:
        _save_tokens(_form_post(f"{API}/oauth/token/", {
            "client_key": cfg("tiktok_client_key"),
            "client_secret": cfg("tiktok_client_secret"),
            "code": request.args.get("code", ""),
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri()}))
        info = creator_info()
        set_cfg("tiktok_username", info.get("creator_username", ""))
        set_cfg("tiktok_nickname", info.get("creator_nickname", ""))
        flash(f"TikTok connected: @{info.get('creator_username', '')}.", "success")
    except RuntimeError as exc:
        flash(str(exc), "error")
    return redirect(back)


@social_bp.route("/tiktok/disconnect", methods=["POST"])
@admin_required
def tiktok_disconnect():
    try:
        _form_post(f"{API}/oauth/revoke/", {
            "client_key": cfg("tiktok_client_key"),
            "client_secret": cfg("tiktok_client_secret"),
            "token": cfg("tiktok_access_token")})
    except RuntimeError:
        pass                              # forget it here either way
    for k in ("tiktok_access_token", "tiktok_refresh_token", "tiktok_expires_at",
              "tiktok_refresh_expires_at", "tiktok_open_id", "tiktok_username",
              "tiktok_nickname"):
        set_cfg(k, "")
    flash("TikTok disconnected.", "info")
    return redirect(url_for("social.settings") + "#tiktok")


@social_bp.route("/tiktok/creator.json")
@admin_required
def tiktok_creator():
    """What the Validate panel shows before posting (TikTok requires it)."""
    try:
        info = creator_info()
    except RuntimeError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(ok=True, username=info.get("creator_username"),
                   nickname=info.get("creator_nickname"),
                   avatar=info.get("creator_avatar_url"),
                   privacy=[{"value": p, "label": PRIVACY_LABELS.get(p, p)}
                            for p in info.get("privacy_level_options", [])],
                   comment_disabled=bool(info.get("comment_disabled")),
                   max_seconds=info.get("max_video_post_duration_sec"))


def _verify_file(name):
    if not name or name != cfg("tiktok_verify_name") or \
            not cfg("tiktok_verify_content"):
        abort(404)
    return Response(cfg("tiktok_verify_content"), mimetype="text/plain")


@social_bp.record_once
def _root_verify_route(state):
    # TikTok's URL-property check may look for its file at the site root as
    # well as under the prefix, so it is served at both (only that one name).
    state.app.add_url_rule("/<name>.txt", "tiktok_verify_root",
                           lambda name: _verify_file(name + ".txt"))


def _legal(page):
    from flask import render_template
    email = cfg("company_email") or "info@plannedrealestate.com"
    phone = cfg("company_phone") or "+974 3111 4024"
    return render_template("legal/page.html", page=page, email=email,
                           phone=phone, updated="28 October 2026",
                           title="Terms of Service" if page == "terms"
                           else "Privacy Policy")


@social_bp.record_once
def _legal_routes(state):
    # Public pages the TikTok (and Meta) app registration links to.
    state.app.add_url_rule("/terms", "legal_terms", lambda: _legal("terms"))
    state.app.add_url_rule("/privacy", "legal_privacy", lambda: _legal("privacy"))


def serve_verify_file(name):
    """Called by /social/m/<name> for the verification file's name."""
    return _verify_file(name)


# ----------------------------------------------------------------- publish
def creator_info():
    return _api("/post/publish/creator_info/query/", {})


def check_options(opts, fmt):
    """Plain reason the TikTok choices can't be used, or None."""
    if fmt == "story":
        return "TikTok has no stories. Untick TikTok for stories."
    if not connected():
        return "TikTok isn't connected (Social settings → TikTok)."
    if not opts.get("privacy"):
        return "Choose who can see it on TikTok."
    try:
        allowed = creator_info().get("privacy_level_options", [])
    except RuntimeError as exc:
        return str(exc)
    if opts["privacy"] not in allowed:
        return ("That TikTok privacy choice isn't available for this account "
                f"now ({', '.join(PRIVACY_LABELS.get(p, p) for p in allowed)}).")
    return None


def _post_info(caption, opts, video):
    info = {"privacy_level": opts["privacy"],
            "disable_comment": not opts.get("comments", True),
            # Our own listings and services: TikTok's "Your brand" label.
            "brand_organic_toggle": True, "brand_content_toggle": False}
    if video:
        info.update(title=caption[:2200], disable_duet=False,
                    disable_stitch=False, video_cover_timestamp_ms=1500)
    else:
        first = (caption.strip().splitlines() or [""])[0]
        info.update(title=first[:90], description=caption[:4000],
                    auto_add_music=True)
    return info


def _wait(publish_id):
    """Follow TikTok's processing; returns the post link."""
    user = cfg("tiktok_username")
    profile = f"https://www.tiktok.com/@{user}" if user else "https://www.tiktok.com/"
    waited = 0
    while waited < STATUS_WAIT:
        time.sleep(5)
        waited += 5
        st = _api("/post/publish/status/fetch/", {"publish_id": publish_id})
        s = st.get("status")
        if s == "FAILED":
            raise RuntimeError("TikTok couldn't publish it: "
                               f"{st.get('fail_reason') or 'unknown reason'}")
        if s == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or \
                st.get("publicly_available_post_id") or []
            if ids and user:
                return f"https://www.tiktok.com/@{user}/video/{ids[0]}"
            return profile                # private ("Only me") posts: no link
    return profile                        # still processing; it carries on


def publish_video(path, caption, opts):
    size = os.path.getsize(path)
    if size < 5 * 1024 * 1024:
        chunk, count = size, 1
    else:
        chunk = CHUNK
        count = max(1, size // chunk)
    data = _api("/post/publish/video/init/", {
        "post_info": _post_info(caption, opts, True),
        "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                        "chunk_size": chunk, "total_chunk_count": count}})
    with open(path, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start)
            body = f.read(end - start + 1)
            req = urllib.request.Request(data["upload_url"], data=body,
                                         method="PUT", headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                "Content-Range": f"bytes {start}-{end}/{size}"})
            try:
                urllib.request.urlopen(req, timeout=180).close()
            except urllib.error.HTTPError as exc:
                if exc.code not in (201, 206):
                    raise RuntimeError(f"TikTok refused the upload ({exc.code}): "
                                       f"{exc.read().decode('utf-8', 'replace')[:200]}")
    return _wait(data["publish_id"])


def publish_photos(urls, caption, opts):
    data = _api("/post/publish/content/init/", {
        "post_info": _post_info(caption, opts, False),
        "source_info": {"source": "PULL_FROM_URL", "photo_cover_index": 0,
                        "photo_images": urls[:35]},
        "post_mode": "DIRECT_POST", "media_type": "PHOTO"})
    return _wait(data["publish_id"])
