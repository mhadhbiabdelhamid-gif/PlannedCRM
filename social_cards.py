"""Branded post designs, photo choice, and content posts (tips, area guides,
market updates) for the Social posts assistant.

The pictures themselves are drawn in the browser by static/js/postcards.js
("Gold Light" design). This module decides *what* goes on them:

  * listing_card()  - the title, area, price and facts for a listing post,
                      built from the listing itself, never by the AI;
  * photo choice    - every photo a post could use, and the ones the team
                      picked and approved before the design is made;
  * content posts   - tips, area guides and market updates, plus the weekly
                      content plan that keeps them in the mix.

Facts on a post come from the CRM or from the person creating it. The AI only
writes wording around them, so no invented rents, figures or claims.
"""
import json
import re
from datetime import date, datetime, timedelta

from flask import flash, jsonify, redirect, request, url_for

import areas
from ai_intake import ask_model, current_user_id, db
from ai_social import (COMPANY, SECTIONS, apply_sections, cfg, first_photo,
                       social_bp)
from auth import admin_required

# ----------------------------------------------------------------- listings

AR_TYPE = {  # (Arabic noun, feminine?)
    "apartment": ("شقة", True), "studio": ("استوديو", False), "villa": ("فيلا", True),
    "townhouse": ("تاون هاوس", False), "compound villa": ("فيلا في مجمع", True),
    "compound_villa": ("فيلا في مجمع", True), "penthouse": ("بنتهاوس", False),
    "duplex": ("دوبلكس", False), "office": ("مكتب", False), "shop": ("محل", False),
    "showroom": ("صالة عرض", True), "warehouse": ("مستودع", False), "land": ("أرض", True),
    "full building": ("مبنى كامل", False), "full_building": ("مبنى كامل", False),
    "building": ("مبنى", False),
}
NO_ROOMS = {"office", "shop", "showroom", "warehouse", "land", "studio",
            "full building", "full_building", "building"}


def _furnishing(prop):
    """'Furnished' / 'Semi Furnished' / 'Unfurnished' / None, wherever the
    listing keeps it (column, old extras JSON, or typed into Features)."""
    value = (prop.get("furnishing") or "").strip()
    if not value and prop.get("extras"):
        try:
            value = (json.loads(prop["extras"]) or {}).get("furnishing", "") or ""
        except (ValueError, TypeError, AttributeError):
            value = ""
    if not value:
        text = " ".join(str(prop.get(k) or "") for k in
                        ("features", "description", "title")).lower()
        if re.search(r"semi[\s-]?furnish|نصف مفروش", text):
            value = "Semi Furnished"
        elif re.search(r"un[\s-]?furnish|غير مفروش", text):
            value = "Unfurnished"
        elif "furnish" in text or "مفروش" in text:
            value = "Furnished"
    low = value.lower()
    if low.startswith("semi"):
        return "Semi Furnished"
    if low.startswith("un") or low.startswith("not"):
        return "Unfurnished"
    if low.startswith("furn") or low.startswith("fully"):
        return "Furnished"
    return None


def _rooms_ar(n):
    n = int(n)
    if n == 1:
        return "غرفة"
    if n == 2:
        return "غرفتين"
    return f"{n} غرف" if n <= 10 else f"{n} غرفة"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


def listing_card(prop):
    """Everything the design needs for one listing, worked out from the
    listing data. Arabic grammar is handled here, not left to the AI."""
    kind = str(prop.get("prop_type") or "").strip().lower()
    noun, fem = AR_TYPE.get(kind, (prop.get("prop_type") or "عقار", False))
    status = str(prop.get("status") or "")
    sale = str(prop.get("listing_type") or "").lower().startswith("sale")
    card_type = ("sold" if status == "Sold" else "rented" if status == "Rented"
                 else "sale" if sale else "rent")
    furn = _furnishing(prop)
    beds = _num(prop.get("bedrooms"))
    baths = _num(prop.get("bathrooms"))
    size = _num(prop.get("size_sqm"))

    ar = noun
    if beds and kind not in NO_ROOMS:
        ar += " " + _rooms_ar(beds)
    if furn == "Furnished":
        ar += " مفروشة" if fem else " مفروش"
    elif furn == "Semi Furnished":
        ar += " نصف مفروشة" if fem else " نصف مفروش"
    ar += " للبيع" if sale else " للإيجار"

    en_type = str(prop.get("prop_type") or "Property").replace("_", " ").upper()
    en = en_type
    if beds and kind not in NO_ROOMS:
        en = f"{int(beds)} BEDROOM {en_type}"
    if furn == "Furnished":
        en = "FURNISHED " + en

    hit = areas.lookup(prop.get("area")) or {}
    area_en = hit.get("en") or (prop.get("area") or "")
    area_ar = hit.get("ar") or ""

    specs = []
    if beds and kind not in NO_ROOMS:
        specs.append({"icon": "bed", "value": str(int(beds)), "ar": "غرف نوم", "en": "Bedrooms"})
    if baths:
        specs.append({"icon": "bath", "value": str(int(baths)), "ar": "حمّام", "en": "Bathrooms"})
    if furn:
        furn_ar = {"Furnished": "مفروشة" if fem else "مفروش",
                   "Semi Furnished": "نصف مفروشة" if fem else "نصف مفروش",
                   "Unfurnished": "غير مفروشة" if fem else "غير مفروش"}[furn]
        specs.append({"icon": "sofa", "value": furn_ar, "ar": "التأثيث", "en": "Furnishing"})
    if size:
        specs.append({"icon": "size", "value": f"{size:,} m²", "ar": "المساحة", "en": "Size"})

    return {"type": card_type, "titleAr": ar, "titleEn": en,
            "areaAr": area_ar, "areaEn": area_en,
            "price": _num(prop.get("price")) or 0, "specs": specs[:4]}


def all_photos(conn, property_id):
    """Every photo of a listing, cover first, as URLs the browser can load."""
    try:
        rows = conn.execute(
            "SELECT filename FROM property_images WHERE property_id = ? "
            "ORDER BY is_cover DESC, id", (property_id,)).fetchall()
    except Exception:
        one = first_photo(conn, property_id)
        return [one] if one else []
    return ["/uploads/images/" + str(r[0]).lstrip("/") for r in rows if r[0]]


def card_contact():
    """Phone and website as printed in the design footer."""
    phone = re.sub(r"[^\d+]", "", cfg("company_phone") or "")
    m = re.fullmatch(r"\+?974(\d{4})(\d{4})", phone)
    if m:
        phone = f"+974 {m.group(1)} {m.group(2)}"
    web = re.sub(r"^(https?://)?(www\.)?", "", (cfg("company_website") or "").strip(), flags=re.I)
    web = web.rstrip("/").upper()
    out = {}
    if phone:
        out["phone"] = phone
    if web:
        out["web"] = web
    return out


# ------------------------------------------------------------ photo choice

def photo_options(conn, row, content, properties):
    """{key: [urls]} - the photos each part of a post may use. A key is a
    property id (as text) for listing posts, or "main" for an area guide."""
    if row["kind"] == "area":
        return {"main": area_photos(conn, (content.get("inputs") or {}).get("area", ""))}
    if row["kind"] in NO_PHOTO_KINDS:
        return {}
    return {str(p["id"]): all_photos(conn, p["id"]) for p in properties}


def chosen_photos(options, content):
    """The approved choice, or a sensible default (first three photos)."""
    saved = content.get("photos") or {}
    out = {}
    for key, opts in options.items():
        pick = [u for u in saved.get(key, []) if u in opts]
        out[key] = pick or opts[:3]
    return out


def needs_photos(row):
    return row["kind"] not in NO_PHOTO_KINDS


@social_bp.route("/post/<int:post_id>/photos", methods=["POST"])
@admin_required
def save_photos(post_id):
    """Save the photos chosen for the design. Only photos that belong to the
    post's listings (or area) are accepted."""
    from ai_social import _posted_urls
    conn = db()
    row = conn.execute("SELECT * FROM social_posts WHERE id = ?", (post_id,)).fetchone()
    if not row:
        return jsonify({"error": "Post not found."}), 404
    if _posted_urls(row):
        return jsonify({"error": "This post has already gone out."}), 400
    content = json.loads(row["content_json"])
    ids = json.loads(row["property_ids"] or "[]")
    props = [{"id": i} for i in ids]
    options = photo_options(conn, row, content, props)
    wanted = (request.get_json(silent=True) or {}).get("photos") or {}
    clean = {}
    for key, opts in options.items():
        pick = [u for u in wanted.get(key, []) if u in opts][:3]
        if not pick:
            return jsonify({"error": "Choose at least one photo for every listing."}), 400
        clean[key] = pick
    content["photos"] = clean
    content["photos_ok"] = True
    conn.execute("UPDATE social_posts SET content_json = ? WHERE id = ?",
                 (json.dumps(content, ensure_ascii=False), post_id))
    conn.commit()
    return jsonify({"ok": True})


# ----------------------------------------------------------------- areas

AREA_COVER_KEY = "area_cover:"


def _area_name(raw):
    hit = areas.lookup(raw or "")
    if not hit:
        return (raw or "").strip()
    return areas.PARENT.get(hit["en"], hit["en"])


def area_stats(conn):
    """Areas we have listings in, with what we can honestly say about them."""
    rows = conn.execute(
        "SELECT id, area, status, price, listing_type, building_no FROM properties "
        "WHERE COALESCE(TRIM(area), '') != '' AND COALESCE(approval, 'approved') = 'approved'"
    ).fetchall()
    out = {}
    for r in rows:
        name = _area_name(r["area"])
        if not name:
            continue
        a = out.setdefault(name, {"name": name, "ar": areas.in_arabic(name) or "",
                                  "available": 0, "rent_from": None, "sale_from": None,
                                  "buildings": set(), "ids": []})
        a["ids"].append(r["id"])
        if (r["building_no"] or "").strip():
            a["buildings"].add(r["building_no"].strip().lower())
        if r["status"] == "Available":
            a["available"] += 1
            if r["price"]:
                key = "sale_from" if str(r["listing_type"] or "").lower().startswith("sale") else "rent_from"
                if a[key] is None or r["price"] < a[key]:
                    a[key] = r["price"]
    for a in out.values():
        a["buildings"] = len(a["buildings"])
    return out


def area_photos(conn, name):
    """The area's own cover photo first, then photos of our listings there."""
    from db import get_setting
    stats = area_stats(conn).get(name)
    urls = []
    own = get_setting(AREA_COVER_KEY + name, "") if name else ""
    if own:
        urls.append("/uploads/images/" + own.lstrip("/"))
    for pid in (stats or {}).get("ids", [])[:12]:
        for u in all_photos(conn, pid)[:2]:
            if u not in urls:
                urls.append(u)
    return urls[:16]


def area_facts(a):
    facts = []
    if a["available"]:
        facts.append({"icon": "key", "label": "وحدات متاحة لدينا", "value": str(a["available"])})
    if a["rent_from"]:
        facts.append({"icon": "pin", "label": "الإيجار يبدأ من", "value": f"{a['rent_from']:,.0f} ر.ق"})
    elif a["sale_from"]:
        facts.append({"icon": "pin", "label": "البيع يبدأ من", "value": f"{a['sale_from']:,.0f} ر.ق"})
    if a["buildings"]:
        facts.append({"icon": "building", "label": "مبانٍ نسوّقها", "value": str(a["buildings"])})
    return facts[:3]


# -------------------------------------------------------- content posts

# Section boxes on the review page for each kind of content post.
CONTENT_SECTIONS = {
    "tip": [("title", "Title"), ("intro", "The tip"), ("contact", "Call to action")],
    "area": [("title", "Title"), ("intro", "About the area"),
             ("details", "What we have there"), ("contact", "Call to action")],
    "market": [("title", "Title"), ("intro", "What it means"),
               ("details", "Source"), ("contact", "Call to action")],
    "news": [("title", "Title"), ("intro", "The news"),
             ("details", "Source"), ("contact", "Call to action")],
}
NO_PHOTO_KINDS = ("tip", "market", "news")
KIND_LABELS = {"tip": "Tip", "area": "Area guide", "market": "Market update",
               "news": "News"}


def sections_for(content):
    keys = content.get("section_keys")
    if keys:
        labels = dict(SECTIONS)
        for kind_sections in CONTENT_SECTIONS.values():
            labels.update(dict(kind_sections))
        return [(k, labels.get(k, k.title())) for k in keys]
    return SECTIONS


# Topics the tips series rotates through, so the page doesn't repeat itself.
TIP_TOPICS = [
    "check whether electricity and water (Kahramaa) are included in the rent",
    "district cooling / chiller charges in towers such as The Pearl and Lusail",
    "post-dated rent cheques and how many are usually asked for",
    "the security deposit: how much and how it is returned",
    "registering the lease contract",
    "what to inspect at a viewing before signing",
    "furnished vs unfurnished: what is really included",
    "who pays for maintenance and repairs",
    "notice periods and renewing the lease",
    "parking spaces and visitor parking",
    "commute time: choosing an area near work or school",
    "photos and a checklist of the unit's condition on move-in day",
    "budgeting beyond the rent: internet, cooling, service charges",
    "questions to ask the agent before a viewing",
    "what landlords look for in a tenant",
    "selling: preparing a property so it photographs well",
    "buying: checking the title deed and ownership before paying",
    "investing: rental yield in simple terms",
]

CONTENT_PROMPT = f"""You write Instagram and Facebook posts for {COMPANY}, a \
licensed real estate agency in Doha, Qatar. Arabic is the main language: write \
it as a native Gulf speaker, natural and warm, not a translation. English is \
written for its own readers, not translated word for word.

Hard rules:
- Never invent numbers, prices, percentages, dates, laws, fees or statistics. \
Use only figures given to you. If a point needs a figure you weren't given, \
make it without one.
- No claims about being the best, cheapest or number one.
- Keep it practical and short. People read this on a phone.
- Emoji: at most one per section, at the end of a line, never at the start.
- The phone number is supplied. Never invent one.

Return ONLY a JSON object, no markdown:
{{
  "card": {{...as described in the task...}},
  "sections_ar": {{"title": str, "intro": str, "details": str, "contact": str}},
  "sections_en": {{"title": str, "intro": str, "details": str, "contact": str}},
  "hashtags": [str],
  "hook_en": str,
  "review_flags": [str]
}}
sections: "title" is one line; "intro" is the main text (2-4 short lines); \
"details" as the task says, else ""; "contact" is a one-line call to action \
ending with the phone number. hashtags: 6-10, Arabic and English, about Qatar \
property; reuse these when they fit: #عقارات_قطر #الدوحة_قطر #RealEstateDoha \
#QatarRealEstate #PlannedRealEstate. review_flags: anything a person should \
double-check before posting (for example a general statement about an area)."""


def _official_block(inputs):
    """The official text a post must be written from, if there is one."""
    art = inputs.get("article")
    if not art:
        return {}
    return {"official_source": {
        "issued_by": art.get("source_ar"), "issued_by_en": art.get("source_en"),
        "link": art.get("url"), "date": art.get("date"),
        "title": art.get("title"), "text": art.get("text")},
        "rule": "Use ONLY facts, figures, names and dates found in "
                "official_source.text. Write in your own words; do not copy "
                "whole sentences. If something isn't in the text, leave it out."}


def _generate(kind, inputs, conn):
    phone = cfg("company_phone")
    official = _official_block(inputs)
    if kind == "news":
        task = {
            "post": "real estate news for people in Qatar, from an official source",
            **official,
            "card_format": {"titleAr": "the news as a headline, max 9 words",
                            "bodyAr": "one or two short sentences, max 24 words",
                            "labelEn": "2-4 words in English, e.g. NEW REGULATION"},
            "intro": "the news in 2-4 short lines, and why it matters to tenants, "
                     "buyers, owners or investors",
            "details": "credit the source: who issued it, then the link",
            "phone": phone,
        }
    elif kind == "tip":
        done = conn.execute("SELECT content_json FROM social_posts WHERE kind = 'tip' "
                            "AND status != 'discarded' ORDER BY id DESC LIMIT 12").fetchall()
        recent = []
        for r in done:
            try:
                recent.append(json.loads(r[0]).get("card", {}).get("titleAr", ""))
            except ValueError:
                pass
        task = {
            "post": "renting / buying tip, part of a numbered tips series",
            "topic": inputs.get("topic") or "",
            "avoid_repeating_these_recent_tips": [t for t in recent if t],
            "card_format": {"titleAr": "the tip as a short headline, max 6 words",
                            "bodyAr": "one or two short sentences, max 18 words",
                            "labelEn": "2-3 words in English, e.g. RENTING TIP"},
            "details": "leave empty",
            "phone": phone,
        }
        if official:
            task.update(official)
            task["post"] = ("practical tip based on an official rule or service in "
                            "Qatar, part of a numbered tips series")
            task["details"] = "credit the source: who issued it, then the link"
    elif kind == "area":
        a = inputs["stats"]
        task = {
            "post": "area guide for one district of Qatar",
            "area_en": a["name"], "area_ar": a["ar"],
            "figures_you_may_use": {
                "units_available_with_us": a["available"],
                "rent_starts_from_qar": a["rent_from"],
                "sale_starts_from_qar": a["sale_from"],
                "buildings_we_market": a["buildings"],
            },
            "card_format": {"blurbAr": "one line about the area's character, max 10 words"},
            "intro": "what the area is like to live or work in. Only widely known, "
                     "general facts (e.g. waterfront, central, family area). Put "
                     "this in review_flags so a person checks it.",
            "details": "what we have available there, using only the figures given",
            "phone": phone,
        }
    else:  # market
        task = {
            "post": "market update",
            "figure": inputs["figure"], "figure_label": inputs.get("label", ""),
            "headline_ar": inputs["title_ar"], "note": inputs.get("note", ""),
            "source": inputs["source"],
            "card_format": {"bodyAr": "one line explaining the figure, max 14 words, "
                                      "no new numbers"},
            "intro": "what this means for tenants, buyers or investors - no new numbers",
            "details": "credit the source exactly as given",
            "phone": phone,
        }
        if official:
            task.update(official)
            task["intro"] = ("what this means for tenants, buyers or investors. You may "
                             "add one or two more figures, only exactly as they appear "
                             "in official_source.text")
            task["details"] = "credit the source: who issued it, then the link"
    out = ask_model(CONTENT_PROMPT, json.dumps(task, ensure_ascii=False))
    out.setdefault("hashtags", [])
    out["hashtags"] = [h for h in (str(x).strip().lstrip("#") for x in out["hashtags"]) if h]
    out.setdefault("review_flags", [])
    ai_card = out.get("card") or {}
    keys = [k for k, _ in CONTENT_SECTIONS[kind]]
    for lang in ("ar", "en"):
        parts = out.get(f"sections_{lang}") or {}
        out[f"sections_{lang}"] = {k: str(parts.get(k) or "") for k in keys}

    # The card: facts from the CRM or the person; wording from the AI.
    art = inputs.get("article") or {}
    if kind == "news":
        card = {"type": "news",
                "titleAr": ai_card.get("titleAr") or out["sections_ar"]["title"],
                "bodyAr": ai_card.get("bodyAr") or "",
                "labelEn": ai_card.get("labelEn") or "REAL ESTATE NEWS",
                "date": _card_date(art.get("date")),
                "source": art.get("source_en") or ""}
    elif kind == "tip":
        count = conn.execute("SELECT COUNT(*) FROM social_posts WHERE kind = 'tip' "
                             "AND status != 'discarded'").fetchone()[0]
        card = {"type": "tip", "number": f"{count + 1:02d}",
                "titleAr": ai_card.get("titleAr") or out["sections_ar"]["title"],
                "bodyAr": ai_card.get("bodyAr") or "",
                "labelEn": ai_card.get("labelEn") or "RENTING TIP"}
        if art:
            card["source"] = art.get("source_en") or ""
    elif kind == "area":
        a = inputs["stats"]
        card = {"type": "area", "nameAr": a["ar"] or a["name"], "nameEn": a["name"],
                "facts": area_facts(a)}
    else:
        card = {"type": "market", "figure": inputs["figure"],
                "figureLabel": inputs.get("label", ""), "titleAr": inputs["title_ar"],
                "bodyAr": inputs.get("note") or ai_card.get("bodyAr") or "",
                "source": inputs["source"]}
    out["card"] = card
    if art:
        _flag_unsupported(out, art, phone)
    out["section_keys"] = keys
    out["slides"] = [{}]
    out.setdefault("hook_en", KIND_LABELS[kind])
    out["post_kind"] = kind
    return apply_sections(out)


def _card_date(iso):
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d %b %Y").upper()
    except (TypeError, ValueError):
        return ""


def _flag_unsupported(out, art, phone):
    """Any number in the post that isn't in the official text gets flagged
    for a person to check (the phone number and hashtags aside)."""
    import official_sources as osrc
    words = " ".join(list(out["sections_ar"].values()) + list(out["sections_en"].values()))
    card = out.get("card") or {}
    words += " " + " ".join(str(card.get(k) or "") for k in
                            ("titleAr", "bodyAr", "figure", "figureLabel"))
    for drop in (phone or "", "".join(ch for ch in (phone or "") if ch.isdigit())):
        if drop:
            words = words.replace(drop, " ")
    words = re.sub(r"\+?974[\s\d]{8,12}", " ", words)
    bad = [n for n in osrc.unsupported_numbers(words, art.get("text", "") + " "
                                               + art.get("title", "") + " "
                                               + art.get("date", ""))
           if len(n.replace(".", "")) > 1]
    if bad:
        out["review_flags"].append(
            "These numbers are not in the official text: " + ", ".join(bad)
            + ". Check or remove them before posting.")


MARKET_PICK_PROMPT = """You pick figures for real estate social media posts in \
Qatar from official announcements. Read official_source.text and choose the \
single figure most useful to tenants, buyers or investors (for example the \
week's total sales value, a change in percent, the number of building \
permits). Use it exactly as it appears in the text: never round, convert or \
calculate. Return ONLY JSON:
{"figure": "the figure in Latin digits, max 10 characters, may end with M, B or %, e.g. 654.4M or +21.5%",
 "figure_label": "English, what it measures (with the unit if money) and the period, max 34 characters, e.g. Sales value, QAR · Week 39",
 "title_ar": "Arabic headline, max 9 words",
 "note_ar": "one Arabic line explaining it, max 14 words; any number in it must be in the text",
 "quote": "the exact sentence from the text that contains the figure"}"""


def pick_market_figure(article):
    """Ask for the headline figure, then check every number it gave against
    the official text. One retry; then refuse rather than guess."""
    import official_sources as osrc
    task = {"official_source": {"issued_by": article.get("source_ar"),
                                "title": article.get("title"),
                                "text": article.get("text")}}
    src = article.get("text", "") + " " + article.get("title", "")
    for attempt in range(2):
        got = ask_model(MARKET_PICK_PROMPT, json.dumps(task, ensure_ascii=False))
        figure = str(got.get("figure") or "").strip()
        claimed = " ".join(str(got.get(k) or "") for k in ("figure", "figure_label",
                                                           "title_ar", "note_ar"))
        bad = osrc.unsupported_numbers(claimed, src)
        if figure and not bad:
            return {"figure": figure, "label": str(got.get("figure_label") or "").strip(),
                    "title_ar": str(got.get("title_ar") or "").strip(),
                    "note": str(got.get("note_ar") or "").strip(),
                    "quote": str(got.get("quote") or "").strip()}
        task["previous_answer_had_numbers_not_in_the_text"] = bad or ["(no figure)"]
    raise RuntimeError("No figure in that announcement could be matched exactly to "
                       "its text, so no market post was made. Try another item, or "
                       "make it by hand.")


def make_official(kind, article, conn, topic=""):
    """A market update, news post or tip written from an official article."""
    if kind == "market":
        pick = pick_market_figure(article)
        inputs = dict(pick, source=article.get("source_en") or "", article=article)
    elif kind == "news":
        inputs = {"article": article}
    elif kind == "tip":
        inputs = {"topic": topic, "article": article}
    else:
        raise RuntimeError("Unknown kind of post.")
    return make_content(kind, inputs, conn)


def _inputs_from_form(kind, conn):
    f = request.form
    if kind == "tip":
        return {"topic": f.get("topic", "").strip()}, None
    if kind == "area":
        name = f.get("area", "").strip()
        stats = area_stats(conn).get(name)
        if not stats:
            return None, "Choose an area we have listings in."
        return {"area": name, "stats": stats}, None
    figure = f.get("figure", "").strip()
    title = f.get("title_ar", "").strip()
    source = f.get("source", "").strip()
    if not (figure and title and source):
        return None, "A market update needs the figure, the Arabic headline and its source."
    return {"figure": figure, "label": f.get("label", "").strip(), "title_ar": title,
            "note": f.get("note", "").strip(), "source": source}, None


def make_content(kind, inputs, conn):
    """Write a content post; returns its content dict (raises RuntimeError)."""
    if kind == "area":
        # Refresh the figures every time, so a rewrite never shows stale ones.
        stats = area_stats(conn).get(inputs["area"])
        if not stats:
            raise RuntimeError("We have no listings in that area any more.")
        inputs = dict(inputs, stats=stats)
    content = _generate(kind, inputs, conn)
    saved = dict(inputs)
    saved.pop("stats", None)
    content["inputs"] = saved
    if inputs.get("article"):
        content["source_url"] = inputs["article"].get("url")
    return content


@social_bp.route("/content/new", methods=["POST"])
@admin_required
def new_content():
    kind = request.form.get("kind", "")
    if kind not in CONTENT_SECTIONS:
        return redirect(url_for("social.index"))
    conn = db()
    inputs, problem = _inputs_from_form(kind, conn)
    if problem:
        flash(problem, "error")
        return redirect(url_for("social.index") + "#plan")
    try:
        content = make_content(kind, inputs, conn)
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("social.index") + "#plan")
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO social_posts (property_ids, kind, content_json, status, "
        "created_by, created_at) VALUES ('[]', ?, ?, 'draft', ?, ?)",
        (kind, json.dumps(content, ensure_ascii=False), current_user_id(), now))
    conn.commit()
    return redirect(url_for("social.review", post_id=cur.lastrowid))


# ---------------------------------------------------------- content plan

# What a good week on the page looks like. Listings sell; tips, area guides and
# market updates are why people follow the page between listings.
WEEKLY_PLAN = [
    ("listing", "Listings", "عقارات", 3),
    ("tip", "Tips", "نصائح", 1),
    ("area", "Area guide", "دليل المناطق", 1),
    # Aqarat and the Ministry of Justice publish weekly figures, so a market
    # update every week; news from official sources too.
    ("market", "Market update", "السوق العقاري", 1),
    ("news", "News", "أخبار عقارية", 1),
]
MONTHLY_PLAN = []


def _week_start(today=None):
    """Weeks run Sunday to Saturday, as the office's working week does."""
    today = today or date.today()
    return today - timedelta(days=(today.weekday() + 1) % 7)


def content_plan(conn, today=None):
    today = today or date.today()
    week0 = _week_start(today)
    month0 = today.replace(day=1)
    rows = conn.execute(
        "SELECT kind, status, posted_at, created_at FROM social_posts "
        "WHERE status != 'discarded' AND COALESCE(posted_at, created_at) >= ?",
        ((min(week0, month0)).isoformat(),)).fetchall()

    def bucket(kind):
        return "listing" if kind in ("single", "carousel") else kind

    def counts(since):
        posted, drafted = {}, {}
        for r in rows:
            k = bucket(r["kind"])
            if r["status"] in ("posted", "partial") and (r["posted_at"] or "") >= since:
                posted[k] = posted.get(k, 0) + 1
            elif r["status"] not in ("posted", "partial") and (r["created_at"] or "") >= since:
                drafted[k] = drafted.get(k, 0) + 1
        return posted, drafted

    wp, wd = counts(week0.isoformat())
    mp, md = counts(month0.isoformat())
    items = []
    for key, en, ar, target in WEEKLY_PLAN:
        items.append({"key": key, "en": en, "ar": ar, "target": target, "period": "this week",
                      "posted": wp.get(key, 0), "drafts": wd.get(key, 0)})
    for key, en, ar, target in MONTHLY_PLAN:
        items.append({"key": key, "en": en, "ar": ar, "target": target, "period": "this month",
                      "posted": mp.get(key, 0), "drafts": md.get(key, 0)})
    for it in items:
        it["done"] = it["posted"] >= it["target"]
    return {"items": items, "week_start": week0, "all_done": all(i["done"] for i in items)}


def next_tip_topic(conn):
    n = conn.execute("SELECT COUNT(*) FROM social_posts WHERE kind = 'tip' "
                     "AND status != 'discarded'").fetchone()[0]
    return TIP_TOPICS[n % len(TIP_TOPICS)]


def suggested_area(conn):
    """The area with the most available units that hasn't had a guide lately."""
    stats = area_stats(conn)
    recent = set()
    since = (date.today() - timedelta(weeks=8)).isoformat()
    for r in conn.execute("SELECT content_json FROM social_posts WHERE kind = 'area' "
                          "AND status != 'discarded' AND created_at >= ?", (since,)):
        try:
            recent.add((json.loads(r[0]).get("inputs") or {}).get("area"))
        except ValueError:
            pass
    ranked = sorted((a for a in stats.values() if a["available"]),
                    key=lambda a: -a["available"])
    for a in ranked:
        if a["name"] not in recent:
            return a["name"]
    return ranked[0]["name"] if ranked else ""


# ------------------------------------------------- from official sources

def _used_sources(conn):
    """Official links already made into a post (not discarded): url -> kind."""
    used = {}
    for r in conn.execute("SELECT kind, content_json FROM social_posts WHERE kind IN "
                          "('market','news','tip') AND status != 'discarded' "
                          "ORDER BY id DESC LIMIT 300"):
        try:
            url = json.loads(r["content_json"]).get("source_url")
        except ValueError:
            continue
        if url:
            used.setdefault(url, r["kind"])
    return used


@social_bp.route("/official.json")
@admin_required
def official_items():
    """Latest real-estate items from the official feeds (kept 3 hours)."""
    import official_sources as osrc
    from ai_social import set_cfg
    items, problems = osrc.discover(cfg, set_cfg, force=bool(request.args.get("refresh")))
    used = _used_sources(db())
    for it in items:
        it["used"] = used.get(it["link"])
    return jsonify({"items": items, "problems": problems})


@social_bp.route("/official/make", methods=["POST"])
@admin_required
def official_make():
    """Make a market update, news post or tip from an official item or link.
    The page is opened from the server; if it can't be, the text pasted by
    hand is used instead."""
    import official_sources as osrc
    kind = request.form.get("kind", "")
    url = (request.form.get("url") or "").strip()
    pasted = (request.form.get("text") or "").strip()
    back = url_for("social.index") + "#official"
    if kind not in ("market", "news", "tip"):
        flash("Choose Market update, News or Tip.", "error")
        return redirect(back)
    try:
        article = osrc.pasted_article(url, pasted) if pasted else osrc.fetch_article(url)
    except RuntimeError as exc:
        flash(str(exc) if pasted or "official" in str(exc) else
              f"{exc} You can paste the article's text in the box and try again.",
              "error")
        return redirect(back)
    conn = db()
    try:
        content = make_official(kind, article, conn,
                                topic=(request.form.get("topic") or "").strip())
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(back)
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO social_posts (property_ids, kind, content_json, status, "
        "created_by, created_at) VALUES ('[]', ?, ?, 'draft', ?, ?)",
        (kind, json.dumps(content, ensure_ascii=False), current_user_id(), now))
    conn.commit()
    return redirect(url_for("social.review", post_id=cur.lastrowid))
