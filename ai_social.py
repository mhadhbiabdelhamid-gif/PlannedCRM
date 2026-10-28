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
import time
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, flash, g, get_flashed_messages,
                   jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

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


_TABLES_READY = False


@social_bp.before_app_request
def _ensure_tables():
    """Create the social tables if they're missing. They used to be created by
    hand (see README_social.md), so a fresh install or a restored backup
    without them broke the Social pages. Runs once per server process."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    conn = db()
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS social_posts (id INTEGER PRIMARY KEY "
        "AUTOINCREMENT, property_ids TEXT NOT NULL, kind TEXT NOT NULL, "
        "content_json TEXT NOT NULL, status TEXT NOT NULL, post_urls TEXT, "
        "error TEXT, created_by INTEGER, created_at TEXT NOT NULL, "
        "posted_by INTEGER, posted_at TEXT);"
        "CREATE TABLE IF NOT EXISTS social_config (key TEXT PRIMARY KEY, value TEXT);")
    conn.commit()
    _TABLES_READY = True


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
  "sections_ar": {{"title": string, "intro": string, "details": string,
                  "amenities": string, "price": string, "contact": string}},
  "sections_en": {{"title": string, "intro": string, "details": string,
                  "amenities": string, "price": string, "contact": string}},
  "hashtags": [string],
  "hook_en": string,
  "hook_ar": string,
  "slides": [{{"headline_en": string, "headline_ar": string, "lines": [string]}}],
  "best_time_note": string,
  "review_flags": [string]
}}

sections_ar / sections_en: the caption template above, split into its parts \
in order. Each holds exactly the text that appears in the caption for that \
part, including its own label line, so that joining them in order with one \
blank line between them gives the full caption:
- title: the first line (type, area, emoji).
- intro: the one framing sentence.
- details: the details label line and the rooms / size / bathrooms line.
- amenities: the features line, with its label.
- price: the price label line and the price line.
- contact: the closing call to action and the phone line.
A section with no data behind it (for example no features listed) is an \
empty string. Never fill one in by guessing. For a post with several \
properties, write each section to cover all of them, in the order given.

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
                         "features", "description", "building_no", "floor_no",
                         "furnishing", "view", "balcony", "bills")
                and row[k] not in (None, "")}

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
    return apply_sections(content)


# The caption, part by part. Each part is edited in its own box on the review
# page; the caption that gets posted is the parts joined in this order.
SECTIONS = [
    ("title", "Title"),
    ("intro", "Description"),
    ("details", "Property details"),
    ("amenities", "Amenities"),
    ("price", "Price"),
    ("contact", "Contact"),
]
SECTION_KEYS = [k for k, _ in SECTIONS]


def join_sections(parts):
    return "\n\n".join(str(parts.get(k) or "").strip() for k in SECTION_KEYS
                       if str(parts.get(k) or "").strip())


def apply_sections(content):
    """Normalise the section dicts and rebuild both captions from them.
    Posts written before sections existed have no section dicts and are
    left as they are."""
    for lang in ("en", "ar"):
        parts = content.get(f"sections_{lang}")
        if not isinstance(parts, dict):
            continue
        parts = {k: str(parts.get(k) or "").strip() for k in SECTION_KEYS}
        content[f"sections_{lang}"] = parts
        content[f"caption_{lang}"] = join_sections(parts)
    return content


# What a listing should have before it's posted, and which part of the post
# each item feeds. Checked by the CRM itself from the listing, not by the AI,
# so the list is reliable and never asks an office for bedrooms.
NO_BEDROOMS = {"office", "shop", "warehouse", "land", "studio",
               "full_building", "full building"}
NO_BATHROOMS = {"land"}
NO_FURNISHING = {"land", "warehouse", "full_building", "full building"}


def listing_gaps(prop, photo_url):
    """[(field label, section key or 'images', 'required'|'recommended')]."""
    kind = str(prop.get("prop_type") or "").strip().lower()
    gaps = []

    def need(ok, label, section, level="recommended"):
        if not ok:
            gaps.append((label, section, level))

    need(prop.get("area"), "Area", "title", "required")
    need(prop.get("price"), "Price", "price", "required")
    need(photo_url, "Photo", "images", "required")
    need(prop.get("size_sqm"), "Size (sqm)", "details")
    if kind not in NO_BEDROOMS:
        need(prop.get("bedrooms"), "Bedrooms", "details")
    if kind not in NO_BATHROOMS:
        need(prop.get("bathrooms"), "Bathrooms", "details")
    need(str(prop.get("features") or "").strip(), "Features / amenities",
         "amenities")
    if kind not in NO_FURNISHING:
        # Older listings keep furnishing inside the extras JSON instead of
        # the furnishing column; the post reads both, so the check does too.
        furnishing = prop.get("furnishing")
        if not furnishing:
            try:
                furnishing = (json.loads(prop.get("extras") or "{}") or {}).get(
                    "furnishing")
            except (json.JSONDecodeError, TypeError, AttributeError):
                furnishing = None
        if not furnishing:
            # Often typed into Features ("Furnished") instead of the dropdown.
            text = " ".join(str(prop.get(k) or "") for k in
                            ("features", "description", "title")).lower()
            furnishing = "furnish" in text or "مفروش" in text
        need(furnishing, "Furnishing", "amenities")
    return gaps


# ----------------------------------------------------------------------------
# Publishing
# ----------------------------------------------------------------------------


def _graph(path, params):
    """POST to the Graph API. Returns the decoded response.

    No automatic retry: a publish that seemed to fail may have gone through,
    and retrying would post the same listing twice.
    """
    return _post(f"{GRAPH}/{path}", params,
                 {"content-type": "application/json"}, retry=False)


def _wait_until_ready(container, max_wait=40):
    """Instagram processes an uploaded image before it can be published;
    publishing too early fails with 'media not ready'. Poll its status."""
    url = (f"{GRAPH}/{container}?fields=status_code&access_token="
           + urllib.parse.quote(cfg("fb_page_token")))
    waited = 0
    while waited < max_wait:
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                status = json.loads(resp.read().decode("utf-8")).get(
                    "status_code")
        except (urllib.error.URLError, ValueError):
            status = None               # can't tell; just try to publish
        if status in ("FINISHED", None):
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError("Instagram couldn't process the image "
                               f"(status {status}). Try a different photo.")
        time.sleep(3)
        waited += 3


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

    _wait_until_ready(container)
    res = _graph(f"{cfg('ig_user_id')}/media_publish",
                 {"creation_id": container, "access_token": cfg("fb_page_token")})
    # media_publish returns an internal ID; ask for the real post link.
    link = _graph_get(res.get("id", ""), "permalink").get("permalink")
    return link or "https://www.instagram.com/"


def _graph_get(object_id, fields):
    """Best-effort GET from the Graph API; returns {} on any failure."""
    if not object_id:
        return {}
    url = (f"{GRAPH}/{object_id}?fields={fields}&access_token="
           + urllib.parse.quote(cfg("fb_page_token")))
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError):
        return {}


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
        # Half-posted and failed posts stay listed so they can be retried.
        "SELECT * FROM social_posts WHERE status IN "
        "('draft','approved','partial','failed','posting') "
        "ORDER BY id DESC LIMIT 30").fetchall()
    drafts = []
    for row in posts:
        item = dict(row)
        item["content"] = json.loads(row["content_json"])
        drafts.append(item)
    import social_cards as sc
    stats = sc.area_stats(conn)
    area_choices = sorted((a for a in stats.values() if a["available"]),
                          key=lambda a: -a["available"])
    import social_queue as sq
    sq.ensure_table(conn)
    queue = conn.execute(
        "SELECT status, COUNT(*) n, MIN(publish_at) first FROM social_queue"
        " WHERE status IN ('ready','scheduled') GROUP BY status").fetchall()
    queue = {r["status"]: r for r in queue}
    nxt = queue.get("scheduled")
    schedule_summary = {
        "ready": queue["ready"]["n"] if "ready" in queue else 0,
        "scheduled": nxt["n"] if nxt else 0,
        "next": sq.to_local(nxt["first"]) if nxt else None}
    return render_template("social.html", listings=listings, drafts=drafts,
                           schedule_summary=schedule_summary,
                           can_publish=can_publish(), plan=sc.content_plan(conn),
                           tip_topic=sc.next_tip_topic(conn),
                           area_choices=area_choices,
                           suggested_area=sc.suggested_area(conn),
                           flashes=get_flashed_messages(with_categories=True))


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
    import social_cards as sc
    ids = json.loads(row["property_ids"] or "[]")
    props = []
    if ids:
        marks = ",".join("?" for _ in ids)
        props = conn.execute(
            f"SELECT * FROM properties WHERE id IN ({marks})", ids).fetchall()
    by_id = {p["id"]: p for p in props}
    ordered = [dict(by_id[i]) for i in ids if i in by_id]
    here = url_for("social.review", post_id=post_id, listing_saved=1)
    # Which listings are missing what, and the post section each gap affects.
    by_section = {}
    for item in ordered:
        item["photo_url"] = first_photo(conn, item["id"])
        item["gaps"] = listing_gaps(item, item["photo_url"])
        item["edit_url"] = url_for("properties.form", pid=item["id"], next=here)
        for label, section, level in item["gaps"]:
            by_section.setdefault(section, []).append(
                {"label": label, "level": level, "ref": item.get("ref"),
                 "edit_url": item["edit_url"]})
    if not cfg("company_phone"):
        by_section.setdefault("contact", []).append(
            {"label": "Company phone (Social settings)", "level": "required",
             "ref": None, "edit_url": url_for("social.settings")})

    content = json.loads(row["content_json"])
    is_content = row["kind"] in sc.CONTENT_SECTIONS
    if is_content:
        by_section = {}                    # no listing to check
    # Design data: one card per listing (or the content post's own card),
    # plus every photo it may use and the ones currently chosen.
    for item in ordered:
        item["card"] = sc.listing_card(item)
    options = sc.photo_options(conn, row, content, ordered)
    chosen = sc.chosen_photos(options, content)
    if is_content:
        cards = [dict(content.get("card") or {}, key="main")]
    else:
        cards = [dict(p["card"], key=str(p["id"]), ref=p.get("ref")) for p in ordered]
    return render_template(
        "social_review.html", post=dict(row), content=content,
        properties=ordered, can_publish=can_publish(),
        contact=contact_block(), posted=_posted_urls(row),
        platform_names=PLATFORM_NAMES, sections=sc.sections_for(content),
        has_sections=isinstance(content.get("sections_en"), dict),
        gaps_by_section=by_section, multi=len(ordered) > 1,
        listing_saved=bool(request.args.get("listing_saved")),
        flashes=get_flashed_messages(with_categories=True),
        is_content=is_content, cards=cards, photo_options=options,
        chosen_photos=chosen, needs_photos=sc.needs_photos(row) and bool(options),
        photos_ok=bool(content.get("photos_ok")), card_contact=sc.card_contact(),
        area_name=(content.get("inputs") or {}).get("area", ""),
        **_format_context(row, options))


def _format_context(row, options):
    """What the Story / Video tabs and the Validate panel need."""
    import social_queue as sq
    import social_video as sv
    items = sq.post_queue_items(row["id"])
    videos = sq.listing_videos(row)
    latest = next((i for i in items if i["media_kind"] == "video" and i["status"]
                   in ("rendering", "draft", "ready", "scheduled", "posted",
                       "partial", "failed")), None)
    return dict(
        queue_items=items, listing_videos=videos,
        can_video=bool(videos or any(options.values())),
        video_tool=sv.available(),
        latest_video={"id": latest["id"], "status": latest["status"],
                      "video_url": latest["video_url"],
                      "progress": latest["progress"] or 0} if latest else None,
        tab=request.args.get("tab", "post"),
        now_local=(datetime.utcnow() + sq._offset()).strftime("%Y-%m-%dT%H:%M"))


@social_bp.route("/post/<int:post_id>/save", methods=["POST"])
@admin_required
def save(post_id):
    conn = db()
    row = conn.execute("SELECT content_json FROM social_posts WHERE id = ?",
                       (post_id,)).fetchone()
    if not row:
        return "Post not found", 404
    content = json.loads(row["content_json"])
    if isinstance(content.get("sections_en"), dict):
        # One box per section and language; the captions are rebuilt from them.
        keys = content.get("section_keys") or SECTION_KEYS
        for lang in ("en", "ar"):
            content[f"sections_{lang}"] = {
                k: request.form.get(f"sec_{lang}_{k}", "").strip()
                for k in keys}
        apply_sections(content)
    else:
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


@social_bp.route("/post/<int:post_id>/rewrite", methods=["POST"])
@admin_required
def rewrite(post_id):
    """Write the post again from the listings as they are now, e.g. after
    filling in missing details. Replaces the text of this same post."""
    conn = db()
    row = conn.execute("SELECT * FROM social_posts WHERE id = ?",
                       (post_id,)).fetchone()
    if not row or row["status"] == "discarded":
        return "Post not found", 404
    if row["status"] in ("posted", "partial", "posting") or _posted_urls(row):
        flash("This post has already gone out, so it can't be rewritten. "
              "Create a new post for the listing instead.", "error")
        return redirect(url_for("social.review", post_id=post_id))

    import social_cards as sc
    old = json.loads(row["content_json"])
    if row["kind"] in sc.CONTENT_SECTIONS:
        # Tips, area guides, market updates: write again from the same inputs
        # (an area guide picks up the latest figures from the listings).
        try:
            content = sc.make_content(row["kind"], old.get("inputs") or {}, conn)
        except (RuntimeError, KeyError) as exc:
            flash(str(exc) or "This post can't be rewritten.", "error")
            return redirect(url_for("social.review", post_id=post_id))
    else:
        ids = json.loads(row["property_ids"] or "[]")
        rows = []
        if ids:
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT * FROM properties WHERE id IN ({marks})", ids).fetchall()
        by_id = {r["id"]: r for r in rows}
        rows = [by_id[i] for i in ids if i in by_id]
        if not rows:
            flash("The listings in this post no longer exist.", "error")
            return redirect(url_for("social.review", post_id=post_id))
        try:
            content = generate(rows)
        except RuntimeError as exc:
            flash(str(exc), "error")
            return redirect(url_for("social.review", post_id=post_id))
    # Keep the photos already chosen; the text is what's being rewritten.
    for keep in ("photos", "photos_ok"):
        if keep in old:
            content[keep] = old[keep]
    conn.execute(
        "UPDATE social_posts SET content_json = ?, status = 'draft', "
        "error = NULL WHERE id = ?",
        (json.dumps(content, ensure_ascii=False), post_id))
    conn.commit()
    flash("Rewritten from the latest listing details. Check each section, "
          "then save or post.", "success")
    return redirect(url_for("social.review", post_id=post_id))


IG_CAPTION_LIMIT = 2200
CAPTION_DIVIDER = "\n\n———\n\n"


def build_caption(caption_ar, caption_en, hashtags):
    """What actually gets posted: Arabic first (the company's main language),
    then English, then the hashtags. hashtags may be a list or a "#a #b" string."""
    if isinstance(hashtags, str):
        hashtags = hashtags.split()
    tags = " ".join("#" + str(h).strip().lstrip("#") for h in hashtags
                    if str(h).strip().lstrip("#"))
    body = CAPTION_DIVIDER.join(t.strip() for t in (caption_ar, caption_en)
                                if t and t.strip())
    return "\n\n".join(p for p in (body, tags) if p)


PLATFORM_NAMES = {"facebook": "Facebook", "instagram": "Instagram"}
CARD_DIR = "social_cards"            # under UPLOAD_FOLDER (persistent disk)
CARD_NAME = re.compile(r"^[0-9a-f]{32}\.jpg$")
LOCK_MINUTES = 5                     # a "posting" lock older than this is stale


def _card_folder():
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], CARD_DIR)
    os.makedirs(folder, exist_ok=True)
    return folder


@social_bp.route("/card/<name>")
def card_image(name):
    """Public, no sign-in: Facebook and Instagram download the post images
    from here. Names are random 32-hex strings, so nothing else in the uploads
    folder can be reached and cards can't be guessed."""
    if not CARD_NAME.match(name):
        return "Not found", 404
    return send_from_directory(_card_folder(), name, mimetype="image/jpeg")


def public_base():
    """The site part of the Public address setting. Forgives the usual slips:
    a missing https://, or a whole page address pasted in (".../social/settings")
    instead of just the site."""
    raw = (cfg("public_base_url") or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else raw.rstrip("/")


def _save_cards(files):
    """Store the card images drawn in the browser; return their public URLs."""
    base = public_base()
    urls = []
    for f in files[:10]:
        data = f.read()
        if not data:
            continue
        name = uuid.uuid4().hex + ".jpg"
        with open(os.path.join(_card_folder(), name), "wb") as out:
            out.write(data)
        urls.append(base + url_for("social.card_image", name=name))
    return urls


def check_public_image(url):
    """Open a card link the way Meta does (from outside, not signed in) and
    make sure a picture comes back. Returns None if fine, else a plain-language
    explanation naming the address and what was found there instead."""
    base = public_base()
    advice = (f" Fix: in Social settings, set Public address to exactly the "
              f"address you open the CRM on (now: {base or 'empty'}), "
              "for example https://your-crm.onrender.com, with nothing after it.")
    req = urllib.request.Request(url, headers={
        "User-Agent": "facebookexternalhit/1.1 (Planned CRM self-check)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            final, ctype = resp.geturl(), resp.headers.get("Content-Type", "")
            server = resp.headers.get("Server", "")
    except urllib.error.HTTPError as exc:
        server = exc.headers.get("Server", "") if exc.headers else ""
        extra = (" It looks like Cloudflare is blocking robots; allow "
                 "facebookexternalhit in Cloudflare." if "cloudflare" in
                 server.lower() else "")
        return (f"Meta can't get the post image: {url} answers with error "
                f"{exc.code} instead of a picture.{extra}" + advice)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        reason = str(getattr(exc, "reason", exc))
        if "timed out" in reason.lower():
            # The address is right but the server didn't answer in time:
            # it's too busy to serve the image while it handles this post.
            return ("Meta can't get the post image: the CRM server didn't "
                    f"answer in time when {url} was opened (it's busy handling "
                    "this post). Wait a minute and try again; if it keeps "
                    "happening, the server needs more capacity "
                    "(see gunicorn.conf.py).")
        return (f"Meta can't get the post image: {base or 'the Public address'} "
                f"can't be reached ({reason})." + advice)
    if ctype.startswith(("image/", "video/")):
        return None
    if "/login" in final or "/auth" in final:
        found = "the CRM sign-in page"
    elif "cloudflare" in server.lower():
        found = "a Cloudflare security page"
    else:
        found = f"a web page ({ctype or 'unknown type'})"
    return (f"Meta can't get the post image: {url} opens {found} instead of a "
            "picture." + advice)


def _posted_urls(row):
    try:
        return json.loads(row["post_urls"] or "{}") or {}
    except (json.JSONDecodeError, TypeError):
        return {}


@social_bp.route("/post/<int:post_id>/publish", methods=["POST"])
@admin_required
def publish(post_id):
    """Publish to the ticked platforms and answer in JSON, so the review page
    can show exactly what worked and what didn't.

    Guards against double posting:
      * a platform this post already went to is skipped, never re-posted;
      * the post is locked while publishing, so a second click (or a second
        person) is refused until the first attempt finishes.
    """
    def answer(status, message, results=None, errors=None, code=200):
        return jsonify({"status": status, "message": message,
                        "results": results or {}, "errors": errors or {}}), code

    if not can_publish():
        return answer("error", "Direct posting isn't set up. Fill in Social "
                      "settings first.", code=400)

    conn = db()
    row = conn.execute("SELECT * FROM social_posts WHERE id = ?",
                       (post_id,)).fetchone()
    if not row or row["status"] == "discarded":
        return answer("error", "This post no longer exists.", code=404)

    already = _posted_urls(row)
    wanted = [p for p in request.form.getlist("platform") if p in PLATFORM_NAMES]
    if not wanted:
        return answer("error", "Tick Facebook, Instagram or both.", code=400)
    todo = [p for p in wanted if p not in already]
    if not todo:
        return answer("posted", "Already posted to " + " and ".join(
            PLATFORM_NAMES[p] for p in wanted) + ". Nothing was posted again.",
            results={p: already[p] for p in wanted})

    content = json.loads(row["content_json"])
    import social_cards as sc
    if sc.needs_photos(row) and not content.get("photos_ok"):
        return answer("failed", "Choose and approve the photos first "
                      "(Step 1 on the right), then post.", code=400)
    caption = build_caption(
        request.form.get("caption_ar") or content.get("caption_ar", ""),
        request.form.get("caption_en") or content.get("caption_en", ""),
        request.form.get("hashtags") or content.get("hashtags", []))
    if "instagram" in todo and len(caption) > IG_CAPTION_LIMIT:
        return answer("failed", f"The caption is {len(caption):,} characters; "
                      f"Instagram allows {IG_CAPTION_LIMIT:,}. Shorten a "
                      "section or remove some hashtags, then try again.",
                      code=400)

    # Take the lock in one statement so two clicks can't both get through.
    now = datetime.now()
    stale = (now - timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
    got = conn.execute(
        "UPDATE social_posts SET status = 'posting', posted_at = ? "
        "WHERE id = ? AND (status != 'posting' OR posted_at < ?)",
        (now.isoformat(timespec="seconds"), post_id, stale)).rowcount
    conn.commit()
    if not got:
        return answer("busy", "This post is already being published. Wait for "
                      "the result instead of clicking again.", code=409)

    results, errors = {}, {}
    try:
        image_urls = _save_cards(request.files.getlist("card"))
        problem = check_public_image(image_urls[0]) if image_urls else None
        if not image_urls:
            errors["all"] = ("The post images didn't come through. Reload the "
                             "page so the cards are drawn, then try again.")
        elif problem:
            # Checked before contacting Meta, so a wrong address costs nothing
            # and the message says exactly what to fix.
            errors["all"] = problem
        else:
            for platform in todo:
                try:
                    if platform == "facebook":
                        results[platform] = publish_facebook(caption, image_urls)
                    else:
                        results[platform] = publish_instagram(caption, image_urls)
                except (RuntimeError, KeyError, ValueError) as exc:
                    errors[platform] = _friendly_meta_error(str(exc))
    finally:
        # Always release the lock and record what happened.
        merged = {**already, **results}
        if all(p in merged for p in wanted):
            status = "posted"
        elif merged:
            status = "partial"
        else:
            status = "failed"
        conn.execute(
            "UPDATE social_posts SET status = ?, post_urls = ?, error = ?, "
            "posted_by = ?, posted_at = ? WHERE id = ?",
            (status, json.dumps(merged),
             "; ".join(f"{k}: {v}" for k, v in errors.items()) or None,
             current_user_id(), datetime.now().isoformat(timespec="seconds"),
             post_id))
        conn.commit()

    ok = [PLATFORM_NAMES[p] for p in results]
    bad = [PLATFORM_NAMES.get(p, "Post") for p in errors]
    if ok and not bad:
        msg = "Posted to " + " and ".join(ok) + "."
    elif ok:
        msg = ("Posted to " + " and ".join(ok) + ", but " + " and ".join(bad) +
               " failed. Click Post again to retry only " + " and ".join(bad) + ".")
    else:
        msg = "Nothing was posted."
    return answer(status, msg, results={**already, **results}, errors=errors)


def _friendly_meta_error(raw):
    """Turn Meta's error JSON into one plain sentence, keeping the detail."""
    detail = raw
    m = re.search(r'"message"\s*:\s*"([^"]+)"', raw)
    if m:
        detail = m.group(1)
    low = raw.lower()
    code = re.search(r'"code"\s*:\s*(\d+)', raw)
    code = int(code.group(1)) if code else None
    if code == 190 or "access token" in low:
        return ("The Meta access token has expired or is invalid. Make a new "
                "one and paste it in Social settings. (" + detail + ")")
    if (("image" in low or "media" in low)
            and any(w in low for w in ("download", "fetch", " uri", " url"))):
        return ("Meta couldn't download the image. Check the Public address in "
                "Social settings is your live CRM address. (" + detail + ")")
    if code in (10, 200) or "permission" in low:
        return "Meta refused: missing permission. (" + detail + ")"
    return detail


ALLOWED_IMAGES = {".jpg", ".jpeg", ".png", ".webp"}


@social_bp.route("/post/<int:post_id>/photo", methods=["POST"])
@admin_required
def upload_photo(post_id):
    """Add photos from the review page.

    For a listing post they attach to the listing itself, so the listing gains
    the pictures too. For an area guide the photo becomes the area's cover.
    """
    back = url_for("social.review", post_id=post_id) + "#photos"
    uploads = [u for u in request.files.getlist("photo") if u and u.filename]
    if not uploads:
        flash("Choose an image file first.", "error")
        return redirect(back)
    for u in uploads:
        if os.path.splitext(u.filename)[1].lower() not in ALLOWED_IMAGES:
            flash("Images only - JPG, PNG or WEBP.", "error")
            return redirect(back)

    folder = os.path.join(
        current_app.config.get(
            "UPLOAD_FOLDER",
            os.path.join(current_app.root_path, "instance", "uploads")),
        "images")
    os.makedirs(folder, exist_ok=True)
    conn = db()

    area = request.form.get("area", "").strip()
    if area:
        # Area guide: the photo becomes the area's own cover photo, the same
        # one shown on its card under Properties > Browse by area.
        import social_cards as sc
        from db import set_setting
        u = uploads[0]
        name = f"area-{uuid.uuid4().hex[:12]}{os.path.splitext(u.filename)[1].lower()}"
        u.save(os.path.join(folder, name))
        set_setting(sc.AREA_COVER_KEY + area, name)
        flash("Photo added as the area's cover photo.", "success")
        return redirect(back)

    try:
        property_id = int(request.form.get("property_id", ""))
    except ValueError:
        flash("No listing was chosen for that photo.", "error")
        return redirect(back)

    cols = [r[1] for r in conn.execute("PRAGMA table_info(property_images)")]
    has_any = conn.execute(
        "SELECT 1 FROM property_images WHERE property_id = ?",
        (property_id,)).fetchone()
    for u in uploads:
        name = f"p{property_id}-{uuid.uuid4().hex[:12]}{os.path.splitext(u.filename)[1].lower()}"
        u.save(os.path.join(folder, name))
        fields = ["property_id", "filename"]
        values = [property_id, name]
        if "is_cover" in cols:
            fields.append("is_cover")
            values.append(0 if has_any else 1)
            has_any = True
        if "created_at" in cols:
            fields.append("created_at")
            values.append(datetime.now().isoformat(timespec="seconds"))
        conn.execute(
            f"INSERT INTO property_images ({', '.join(fields)}) "
            f"VALUES ({', '.join('?' for _ in fields)})", values)
    conn.commit()

    flash(f"{len(uploads)} photo{'s' if len(uploads) > 1 else ''} added. "
          "Pick them below.", "success")
    return redirect(back)


@social_bp.route("/post/<int:post_id>/discard", methods=["POST"])
@admin_required
def discard(post_id):
    conn = db()
    conn.execute("UPDATE social_posts SET status = 'discarded' WHERE id = ? "
                 "AND status != 'posted'", (post_id,))
    conn.commit()
    flash("Post discarded.", "info")
    return redirect(url_for("social.index"))


# Branded designs, photo choice and content posts add routes to social_bp;
# so do stories, reels and the publishing schedule.
import social_cards  # noqa: E402,F401
import social_queue  # noqa: E402,F401
