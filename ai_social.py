"""
Social Content Agent - Planned Real Estate CRM
==============================================

Turns listings into ready-to-publish social posts: Arabic and English captions,
Qatar-tuned hashtags, and a branded image card. You review and edit, then either
copy the text out or publish straight to the company Facebook Page and Instagram.

Design rules, same as the intake agent:
  1. Nothing is published without a human pressing the button.
  2. Publishing is gated on credentials being present. Without them the page
     still works as a generator and gives you text to copy.
  3. No new Python packages. Image cards are drawn in the browser.

Setup:
  1. Create the table (see README_social.md).
  2. Register in app.py:  from ai_social import social_bp
                          app.register_blueprint(social_bp)
  3. Optional, for auto-publishing, add to instance/ai_keys.env:
       SOCIAL_PUBLIC_BASE_URL=https://your-app.onrender.com
       FB_PAGE_ID=...
       FB_PAGE_TOKEN=...
       IG_USER_ID=...
"""

import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

from flask import (Blueprint, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

from ai_intake import _post, ask_model, db, current_user_id
from areas import lookup as area_lookup
from auth import admin_required

social_bp = Blueprint("social", __name__, url_prefix="/social",
                      template_folder="templates")

# Contact block printed on every card. Editable at /social/settings.
COMPANY = "Planned Real Estate | بلاند العقارية"
DEFAULTS = {
    "company_phone": os.environ.get("COMPANY_PHONE", ""),
    "company_email": os.environ.get("COMPANY_EMAIL", ""),
    "company_website": os.environ.get("COMPANY_WEBSITE", ""),
    "fb_page_id": os.environ.get("FB_PAGE_ID", ""),
    "fb_page_token": os.environ.get("FB_PAGE_TOKEN", ""),
    "ig_user_id": os.environ.get("IG_USER_ID", ""),
    "public_base_url": os.environ.get("SOCIAL_PUBLIC_BASE_URL", ""),
}
GRAPH = "https://graph.facebook.com/v21.0"

CONFIG_KEYS = list(DEFAULTS)
SECRET_KEYS = {"fb_page_token"}      # never sent back to the browser in full


def cfg(key):
    """Read a setting: database first, then environment, then empty."""
    try:
        row = db().execute(
            "SELECT value FROM social_config WHERE key = ?", (key,)).fetchone()
        if row and row["value"]:
            return row["value"]
    except sqlite3.Error:
        pass
    return DEFAULTS.get(key, "")


def set_cfg(key, value):
    conn = db()
    conn.execute(
        "INSERT INTO social_config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value))
    conn.commit()


def contact_block():
    return {"phone": cfg("company_phone"), "email": cfg("company_email"),
            "website": cfg("company_website"), "company": COMPANY}


def can_publish():
    """Instagram fetches images by URL, so a public base URL is required."""
    return bool(cfg("fb_page_token") and cfg("fb_page_id")
                and cfg("public_base_url"))


# ----------------------------------------------------------------------------
# Content generation
# ----------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You write social media posts for {COMPANY}, a property \
consultancy in Doha, Qatar, for Instagram and Facebook. The audience is \
residents and investors in Qatar.

You will receive one or more properties as JSON. Follow the house template \
below exactly. It is how this company always writes, and consistency matters \
more than novelty.

ARABIC TEMPLATE (caption_ar) - keep the section labels, the blank lines and \
the emoji positions:

<type + furnishing> للإيجار في منطقة <area>! <emoji for the building type> ✨

<one sentence of warm framing about the living experience>

تفاصيل الشقة:

المكونات: <bedrooms> 🛏️، <living space> 🛋️، و <bathrooms> 🚿.

المميزات: <feature> 🚗، <feature> 🔧.

سعر الإيجار:

<price> ريال قطري فقط في الشهر! 💰

للحجز والاستفسار والمعاينة، يرجى التواصل معنا مباشرة على الرقم:
📞 <phone>

For a sale, change the first line to للبيع, the price label to السعر:, and \
drop في الشهر.

ENGLISH TEMPLATE (caption_en) - the same structure in natural English, not a \
word-for-word translation. Keep the emoji in the same places.

Rules:
- Arabic is the primary language for this company. Write it as a native Gulf \
speaker would, not as a translation.
- Prices stay in Western numerals in both languages.
- Emoji go after the item they describe, never at the start of a line, and \
never more than one per item.
- Say فقط with the price only when it is genuinely competitive for that area. \
Otherwise drop it.
- These framing phrases are approved house style and may be used when they \
fit: فرصة مميزة, السكن الراقي والهادئ, تصميم عصري ومريح, موقع استراتيجي, \
شاملة أعمال الصيانة. Do not invent other claims about the location, the view, \
the neighbourhood or nearby landmarks. Everything factual must come from the \
data you were given.
- If a detail is missing from the data, leave that item out of the line \
entirely rather than guessing. A shorter post is fine. In particular: when no \
area is given, write the post without naming an area. A building name is not \
an area; never substitute one for the other.
- The price is given to you with its units spelled out. Never reinterpret it. \
It is the price of the whole unit - never a rate per square metre, per person \
or per room, whatever the number happens to be.
- Do not call a property luxury, premium, exclusive, stunning or anything \
similar unless that word appears in the property data itself. Describe what is \
actually listed instead.
- The phone number is supplied to you. Never invent one.

Return ONLY a JSON object, no preamble and no markdown fences:

{{
  "caption_en": string,
  "caption_ar": string,
  "hashtags": [string],
  "hook_en": string,
  "hook_ar": string,
  "slides": [{{"headline_en": string, "headline_ar": string, "lines": [string]}}],
  "best_time_note": string,
  "review_flags": [string]
}}

hashtags: 8 to 12, mixed Arabic and English, relevant to Qatar property, \
including the area name in both scripts. No generic filler.

slides: one per property in the order given. "lines" is 3-5 very short phrases \
for the optional branded card, under 30 characters each. headline is 2-5 words.

review_flags: name anything missing from the data that the template would \
normally carry, such as an unstated number of bathrooms. Empty array if the \
data was complete."""


def property_payload(rows):
    out = []
    for row in rows:
        item = {k: row[k] for k in row.keys()
                if k in ("ref", "title", "area", "prop_type", "listing_type",
                         "size_sqm", "bedrooms", "bathrooms",
                         "features", "description", "building_no", "floor_no")
                and row[k] is not None}

        extra = {}
        if row["extras"]:
            try:
                extra = json.loads(row["extras"]) or {}
            except (json.JSONDecodeError, TypeError):
                extra = {}
        for key in ("furnishing", "price_period"):
            if extra.get(key):
                item[key] = extra[key]

        # Spell the price out. A bare number invites the model to guess at
        # units, and it guessed "per square metre" once already.
        if row["price"] is not None:
            rent = str(row["listing_type"] or "").lower().startswith("rent")
            period = extra.get("price_period") or ("month" if rent else None)
            item["price"] = {
                "amount": row["price"],
                "currency": "QAR",
                "per": period,
                "means": (f"{row['price']:,.0f} Qatari riyals per "
                          f"{period or 'month'} for the whole unit" if rent
                          else f"{row['price']:,.0f} Qatari riyals total"),
            }

        if row["size_sqm"]:
            item["size_sqm"] = {"amount": row["size_sqm"],
                                "means": "total floor area of the unit"}

        # Give the model the area in both scripts so each caption prints the
        # form a reader of that language would expect.
        hit = area_lookup(item.get("area"))
        if hit:
            item["area"] = hit["en"]
            item["area_ar"] = hit["ar"]
        out.append(item)
    return out


def generate(rows):
    payload = property_payload(rows)
    kind = "carousel" if len(payload) > 1 else "single"
    content = ask_model(
        SYSTEM_PROMPT,
        json.dumps({"post_type": kind,
                    "phone": cfg("company_phone"),
                    "properties": payload}, ensure_ascii=False))
    content.setdefault("hashtags", [])
    content.setdefault("slides", [])
    content.setdefault("review_flags", [])
    # The model sometimes writes "#Doha" and sometimes "Doha". Store them bare;
    # the hash is added once, when displayed.
    content["hashtags"] = [t for t in
                           (str(h).strip().lstrip("#").strip()
                            for h in content["hashtags"]) if t]
    return content


# ----------------------------------------------------------------------------
# Publishing
# ----------------------------------------------------------------------------


def _graph(path, params):
    """POST to the Graph API. Returns the decoded response."""
    return _post(f"{GRAPH}/{path}", params,
                 {"content-type": "application/json"})


def publish_facebook(caption, image_urls):
    if len(image_urls) == 1:
        res = _graph(f"{cfg('fb_page_id')}/photos",
                     {"url": image_urls[0], "caption": caption,
                      "access_token": cfg("fb_page_token")})
        return f"https://facebook.com/{res.get('post_id', res.get('id',''))}"

    media_ids = []
    for url in image_urls:
        res = _graph(f"{cfg('fb_page_id')}/photos",
                     {"url": url, "published": "false",
                      "access_token": cfg("fb_page_token")})
        media_ids.append({"media_fbid": res["id"]})
    res = _graph(f"{cfg('fb_page_id')}/feed",
                 {"message": caption, "attached_media": media_ids,
                  "access_token": cfg("fb_page_token")})
    return f"https://facebook.com/{res.get('id', '')}"


def publish_instagram(caption, image_urls):
    """Instagram is a three-step flow: create container(s), then publish."""
    if not cfg("ig_user_id"):
        raise RuntimeError("The Instagram account ID is not set.")

    if len(image_urls) == 1:
        res = _graph(f"{cfg('ig_user_id')}/media",
                     {"image_url": image_urls[0], "caption": caption,
                      "access_token": cfg("fb_page_token")})
        container = res["id"]
    else:
        children = []
        for url in image_urls[:10]:          # Instagram allows 10 per carousel
            res = _graph(f"{cfg('ig_user_id')}/media",
                         {"image_url": url, "is_carousel_item": "true",
                          "access_token": cfg("fb_page_token")})
            children.append(res["id"])
        res = _graph(f"{cfg('ig_user_id')}/media",
                     {"media_type": "CAROUSEL", "children": children,
                      "caption": caption, "access_token": cfg("fb_page_token")})
        container = res["id"]

    res = _graph(f"{cfg('ig_user_id')}/media_publish",
                 {"creation_id": container, "access_token": cfg("fb_page_token")})
    return f"https://instagram.com/p/{res.get('id', '')}"


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------


@social_bp.route("/")
@admin_required
def index():
    conn = db()
    listings = conn.execute(
        "SELECT id, ref, title, area, prop_type, bedrooms, price "
        "FROM properties WHERE status = 'Available' "
        "ORDER BY is_own DESC, id DESC LIMIT 60").fetchall()
    posts = conn.execute(
        "SELECT * FROM social_posts WHERE status IN ('draft','approved') "
        "ORDER BY id DESC LIMIT 30").fetchall()
    drafts = []
    for row in posts:
        item = dict(row)
        item["content"] = json.loads(row["content_json"])
        drafts.append(item)
    return render_template("social.html", listings=listings, drafts=drafts,
                           can_publish=can_publish())


@social_bp.route("/generate", methods=["POST"])
@admin_required
def generate_post():
    ids = (request.get_json(silent=True) or {}).get("property_ids", [])
    ids = [int(i) for i in ids][:10]
    if not ids:
        return jsonify({"error": "Select at least one listing."}), 400

    conn = db()
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM properties WHERE id IN ({marks})", ids).fetchall()
    if not rows:
        return jsonify({"error": "Those listings no longer exist."}), 404

    # Keep the order the user picked.
    by_id = {r["id"]: r for r in rows}
    rows = [by_id[i] for i in ids if i in by_id]

    try:
        content = generate(rows)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO social_posts (property_ids, kind, content_json, status, "
        "created_by, created_at) VALUES (?, ?, ?, 'draft', ?, ?)",
        (json.dumps(ids), "carousel" if len(rows) > 1 else "single",
         json.dumps(content, ensure_ascii=False), current_user_id(), now))
    conn.commit()
    return jsonify({"post_id": cur.lastrowid})


def first_photo(conn, property_id):
    """Best-effort lookup of a property's main image.

    property_images column names vary, so this inspects the table rather than
    assuming. Returns a URL the browser can load, or None.
    """
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(property_images)")]
    except sqlite3.Error:
        return None
    if not cols:
        return None

    path_col = next((c for c in ("filename", "file", "path", "image",
                                 "image_path", "url", "src") if c in cols), None)
    if not path_col:
        return None

    order = " ORDER BY " + ", ".join(
        [f"{c} DESC" for c in ("is_cover", "is_main", "is_primary")
         if c in cols] +
        [c for c in ("sort_order", "position") if c in cols] +
        ["id"]) if "id" in cols else ""
    try:
        row = conn.execute(
            f"SELECT {path_col} FROM property_images WHERE property_id = ?"
            f"{order} LIMIT 1", (property_id,)).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None

    value = str(row[0])
    if value.startswith(("http://", "https://", "/")):
        return value
    # Served by views_main: /uploads/<kind>/<filename>
    return "/uploads/images/" + value.lstrip("/")


@social_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        for key in CONFIG_KEYS:
            value = (request.form.get(key) or "").strip()
            # Blank secret field means "leave it alone", not "clear it".
            if key in SECRET_KEYS and not value:
                continue
            set_cfg(key, value.rstrip("/") if key.endswith("url") else value)
        flash("Settings saved.", "success")
        return redirect(url_for("social.settings"))

    values = {}
    for key in CONFIG_KEYS:
        raw = cfg(key)
        values[key] = ("•" * 12 + raw[-4:]) if (key in SECRET_KEYS and raw) \
            else raw
    return render_template("social_settings.html", values=values,
                           can_publish=can_publish(),
                           secret_keys=list(SECRET_KEYS))


@social_bp.route("/post/<int:post_id>")
@admin_required
def review(post_id):
    conn = db()
    row = conn.execute("SELECT * FROM social_posts WHERE id = ?",
                       (post_id,)).fetchone()
    if not row:
        return "Post not found", 404
    ids = json.loads(row["property_ids"])
    marks = ",".join("?" for _ in ids)
    props = conn.execute(
        f"SELECT * FROM properties WHERE id IN ({marks})", ids).fetchall()
    by_id = {p["id"]: p for p in props}
    ordered = [dict(by_id[i]) for i in ids if i in by_id]
    for item in ordered:
        item["photo_url"] = first_photo(conn, item["id"])
    return render_template(
        "social_review.html", post=dict(row),
        content=json.loads(row["content_json"]),
        properties=ordered, can_publish=can_publish(),
        contact=contact_block())


@social_bp.route("/post/<int:post_id>/save", methods=["POST"])
@admin_required
def save(post_id):
    conn = db()
    row = conn.execute("SELECT content_json FROM social_posts WHERE id = ?",
                       (post_id,)).fetchone()
    if not row:
        return "Post not found", 404
    content = json.loads(row["content_json"])
    content["caption_en"] = request.form.get("caption_en", "").strip()
    content["caption_ar"] = request.form.get("caption_ar", "").strip()
    content["hashtags"] = [h.strip().lstrip("#") for h in
                           request.form.get("hashtags", "").split() if h.strip()]
    conn.execute(
        "UPDATE social_posts SET content_json = ?, status = 'approved' "
        "WHERE id = ?", (json.dumps(content, ensure_ascii=False), post_id))
    conn.commit()
    flash("Saved. Ready to post.", "success")
    return redirect(url_for("social.review", post_id=post_id))


@social_bp.route("/post/<int:post_id>/publish", methods=["POST"])
@admin_required
def publish(post_id):
    """Publishes to the platforms ticked on the review form."""
    if not can_publish():
        flash("Publishing is not configured. Copy the caption and post "
              "manually, or add the Meta credentials first.", "info")
        return redirect(url_for("social.review", post_id=post_id))

    conn = db()
    row = conn.execute("SELECT * FROM social_posts WHERE id = ?",
                       (post_id,)).fetchone()
    if not row or row["status"] == "posted":
        return "Post not available", 404

    content = json.loads(row["content_json"])
    caption = request.form.get("caption_en") or content.get("caption_en", "")
    if content.get("hashtags"):
        caption += "\n\n" + " ".join("#" + h for h in content["hashtags"])

    image_urls = [u for u in request.form.getlist("image_url") if u]
    if not image_urls:
        flash("No images were provided. Save the card images first.", "info")
        return redirect(url_for("social.review", post_id=post_id))

    results, errors = {}, []
    for platform in request.form.getlist("platform"):
        try:
            if platform == "facebook":
                results["facebook"] = publish_facebook(caption, image_urls)
            elif platform == "instagram":
                results["instagram"] = publish_instagram(caption, image_urls)
        except (RuntimeError, KeyError) as exc:
            errors.append(f"{platform}: {exc}")

    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE social_posts SET status = ?, post_urls = ?, error = ?, "
        "posted_by = ?, posted_at = ? WHERE id = ?",
        ("posted" if results else "failed",
         json.dumps(results), "; ".join(errors) or None,
         current_user_id(), now, post_id))
    conn.commit()

    if results:
        flash("Posted to " + ", ".join(results) +
              (f". Failed: {'; '.join(errors)}" if errors else "."), "success")
    else:
        flash("Nothing was posted. " + "; ".join(errors), "error")
    return redirect(url_for("social.review", post_id=post_id))


ALLOWED_IMAGES = {".jpg", ".jpeg", ".png", ".webp"}


@social_bp.route("/post/<int:post_id>/photo", methods=["POST"])
@admin_required
def upload_photo(post_id):
    """Add a photo to a listing from the review page.

    It attaches to the property rather than to the post, so the listing itself
    gains the picture too.
    """
    try:
        property_id = int(request.form.get("property_id", ""))
    except ValueError:
        flash("No listing was chosen for that photo.", "error")
        return redirect(url_for("social.review", post_id=post_id))

    upload = request.files.get("photo")
    if not upload or not upload.filename:
        flash("Choose an image file first.", "error")
        return redirect(url_for("social.review", post_id=post_id))

    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in ALLOWED_IMAGES:
        flash("Images only — JPG, PNG or WEBP.", "error")
        return redirect(url_for("social.review", post_id=post_id))

    folder = os.path.join(
        current_app.config.get(
            "UPLOAD_FOLDER",
            os.path.join(current_app.root_path, "instance", "uploads")),
        "images")
    os.makedirs(folder, exist_ok=True)

    name = f"p{property_id}-{uuid.uuid4().hex[:12]}{ext}"
    upload.save(os.path.join(folder, name))

    conn = db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(property_images)")]
    has_any = conn.execute(
        "SELECT 1 FROM property_images WHERE property_id = ?",
        (property_id,)).fetchone()

    fields = ["property_id", "filename"]
    values = [property_id, name]
    if "is_cover" in cols:
        fields.append("is_cover")
        values.append(0 if has_any else 1)
    if "created_at" in cols:
        fields.append("created_at")
        values.append(datetime.now().isoformat(timespec="seconds"))

    conn.execute(
        f"INSERT INTO property_images ({', '.join(fields)}) "
        f"VALUES ({', '.join('?' for _ in fields)})", values)
    conn.commit()

    flash("Photo added to the listing." if has_any
          else "Photo added and set as the cover image.", "success")
    return redirect(url_for("social.review", post_id=post_id))


@social_bp.route("/post/<int:post_id>/discard", methods=["POST"])
@admin_required
def discard(post_id):
    conn = db()
    conn.execute("UPDATE social_posts SET status = 'discarded' WHERE id = ? "
                 "AND status != 'posted'", (post_id,))
    conn.commit()
    flash("Post discarded.", "info")
    return redirect(url_for("social.index"))