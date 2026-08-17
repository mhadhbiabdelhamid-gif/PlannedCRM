"""
Listing Intake Agent - Planned Real Estate CRM
==============================================

Paste raw listing messages (WhatsApp / email, Arabic or English, one or many
units per message) and get structured draft listings for human review.

Design rules:
  1. The agent NEVER writes to the properties table. It writes to intake_drafts.
     A human approves, and only then does a row land in properties.
  2. Numbers, phones and bedrooms are normalised in Python, not by the model.
     Models are good at understanding messy text, less reliable at formatting.
  3. Zero new dependencies - uses urllib from the standard library.

Setup:
  1. Run schema.sql against your CRM database.
  2. Set the ANTHROPIC_API_KEY environment variable on the office PC.
  3. Adjust FIELD_MAP below to match your real properties table columns.
  4. In your app factory:  from ai_intake import intake_bp; app.register_blueprint(intake_bp)
"""

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime

from flask import (Blueprint, current_app, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

from auth import admin_required

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

def _load_key_file():
    """Read instance/ai_keys.env if present, so keys work regardless of how
    the server was launched (shortcut, .bat, scheduled task, terminal).
    Real environment variables always take priority.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "instance", "ai_keys.env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(),
                                  value.strip().strip('"').strip("'"))


_load_key_file()

# Which AI provider to use: "gemini" (free tier) or "anthropic" (paid).
PROVIDER = os.environ.get("INTAKE_PROVIDER", "gemini").lower()

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("INTAKE_MODEL_GEMINI", "gemini-2.5-flash")
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("INTAKE_MODEL", "claude-sonnet-5")

API_TIMEOUT = 90


def api_key_present():
    return bool(GEMINI_KEY if PROVIDER == "gemini" else ANTHROPIC_KEY)

# Map extracted field -> real column in the properties table.
# Verified against PRAGMA table_info(properties) on instance/crm.sqlite3.
FIELD_MAP = {
    "listing_type":  "listing_type",
    "property_type": "prop_type",
    "area":          "area",
    "building":      "building_no",
    "floor":         "floor_no",
    "unit":          "unit_no",
    "bedrooms":      "bedrooms",
    "bathrooms":     "bathrooms",
    "size_sqm":      "size_sqm",
    "price":         "price",
    "amenities":     "features",
    "notes":         "description",
}

# These have no column of their own, so they are stored as JSON in `extras`.
EXTRA_FIELDS = ["furnishing", "price_period", "available_from",
                "contact_phone", "source_type", "source_name"]

# Status given to a newly approved listing. Matches the existing value used
# in properties.status.
DEFAULT_STATUS = "Available"

# Reference format, continuing the existing PRE-P-0171 sequence.
REF_PREFIX = "PRE-P-"
REF_DIGITS = 4

intake_bp = Blueprint(
    "intake", __name__, url_prefix="/intake", template_folder="templates"
)

# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------


def db():
    """Reuse the app's connection if it exists, otherwise open one."""
    if "db" in g:
        return g.db
    path = current_app.config.get("DATABASE") or os.environ.get(
        "CRM_DB", "instance/crm.sqlite3")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    g.db = conn
    return conn


def current_user_id():
    user = getattr(g, "user", None)
    if user is not None:
        return user["id"]
    return session.get("user_id")


# ----------------------------------------------------------------------------
# Normalisation helpers - deterministic, no model involved
# ----------------------------------------------------------------------------

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def fix_digits(text):
    return text.translate(ARABIC_DIGITS) if isinstance(text, str) else text


def norm_phone(raw):
    """Qatar numbers -> +974XXXXXXXX. Anything else is returned digits-only."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", fix_digits(str(raw)))
    if digits.startswith("00974"):
        digits = digits[5:]
    elif digits.startswith("974") and len(digits) == 11:
        digits = digits[3:]
    if len(digits) == 8 and digits[0] in "3567":
        return "+974" + digits
    return "+" + digits if digits else None


def norm_bedrooms(raw):
    """Studio -> 0. '3 BHK', 'ثلاث غرف', '3+maid' -> 3."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    s = fix_digits(str(raw)).strip().lower()
    if not s:
        return None
    if any(w in s for w in ("studio", "ستوديو", "استوديو")):
        return 0
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "غرفة": 1, "غرفتين": 2, "ثلاث": 3, "أربع": 4, "اربع": 4,
             "خمس": 5, "ست": 6}
    m = re.search(r"\d+", s)
    if m:
        return int(m.group())
    for word, val in words.items():
        if word in s:
            return val
    return None


def norm_number(raw):
    """'12,500 QAR' -> 12500.0"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^\d.]", "", fix_digits(str(raw)).replace(",", ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def normalise(listing):
    listing["contact_phone"] = norm_phone(listing.get("contact_phone"))
    listing["bedrooms"] = norm_bedrooms(listing.get("bedrooms"))
    for key in ("price", "size_sqm", "bathrooms"):
        listing[key] = norm_number(listing.get(key))
    for key in ("floor", "unit", "building", "area", "source_name"):
        val = listing.get(key)
        if isinstance(val, str):
            listing[key] = fix_digits(val).strip() or None
    return listing


# ----------------------------------------------------------------------------
# The agent
# ----------------------------------------------------------------------------

SYSTEM_PROMPT = """You extract structured property listings from raw messages \
sent to a real estate brokerage in Doha, Qatar. Messages arrive by WhatsApp or \
email, from property owners and from partner agencies. They are written in \
English, Arabic (including Gulf and Levantine dialect), or a mix of both, and \
are often badly formatted.

A single message may describe ONE unit or MANY units (for example a tower with \
several available apartments at different prices). Return one object per \
distinct unit. If a message lists "2BR - 8000, 3BR - 10000" that is two units.

Return ONLY a JSON object, no preamble and no markdown fences:

{"listings": [{
  "listing_type": "rent" | "sale" | null,
  "property_type": "apartment" | "villa" | "townhouse" | "office" | "shop" | "land" | "warehouse" | "compound_villa" | "full_building" | null,
  "area": string | null,
  "building": string | null,
  "floor": string | null,
  "unit": string | null,
  "bedrooms": string | null,
  "bathrooms": string | null,
  "size_sqm": string | null,
  "price": string | null,
  "price_period": "monthly" | "yearly" | "total" | null,
  "furnishing": "furnished" | "semi_furnished" | "unfurnished" | null,
  "amenities": string | null,
  "available_from": string | null,
  "source_type": "owner" | "agency" | null,
  "source_name": string | null,
  "contact_phone": string | null,
  "notes": string | null,
  "review_flags": [string],
  "title_en": string,
  "title_ar": string
}]}

Rules:
- Copy numbers exactly as written. Do not convert currencies or units.
- Sizes given in square feet: keep the number and note the unit in "notes".
- "available_from" as YYYY-MM-DD if a date is stated, otherwise "immediate" or null.
- source_type is "agency" if the sender mentions a company, "Mr/Ms + agency", or \
signs off with a brokerage name. "owner" if they speak as the property owner \
(malik / المالك). Use null when genuinely unclear.
- amenities: short comma-separated list, English (pool, gym, parking, balcony, \
maid room, kitchen appliances, security, sea view).
- notes: anything a broker would want to keep but that has no field - commission \
terms, payment cheques, viewing times, unit condition, "price negotiable".
- review_flags: name any field you guessed or that was ambiguous, plus a short \
reason. Example: ["price - unclear if monthly or yearly"]. Empty array if the \
message was clear.
- title_en / title_ar: one short human-readable line, e.g. "2BR furnished \
apartment, Al Sadd" / "شقة غرفتين مفروشة، السد".
- NEVER invent a value. Missing information is null, not a guess.
- If the message is not a property listing at all, return {"listings": []}."""


def _post(url, payload, headers):
    """POST JSON and return the decoded response, retrying on rate limits."""
    body = json.dumps(payload).encode("utf-8")
    delay = 2
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8")[:300]
            if exc.code == 429 and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            if exc.code == 429:
                raise RuntimeError(
                    "Rate limit reached. The free tier allows a limited number "
                    "of requests per minute - wait a moment and try again.")
            if exc.code in (401, 403):
                raise RuntimeError(
                    "The API key was rejected. Check it is correct and active.")
            raise RuntimeError(f"API returned {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not reach the API: {exc.reason}")
    raise RuntimeError("Rate limit reached after several retries.")


def _parse_listings(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    try:
        return json.loads(text).get("listings", [])
    except (json.JSONDecodeError, AttributeError):
        raise RuntimeError("The model did not return valid JSON.")


def ask_model(system_prompt, user_text, max_tokens=4000):
    """Send a prompt, get parsed JSON back. Shared by all CRM agents."""
    if not api_key_present():
        raise RuntimeError(
            f"No API key set for provider '{PROVIDER}'. Set "
            f"{'GEMINI_API_KEY' if PROVIDER == 'gemini' else 'ANTHROPIC_API_KEY'}"
            " in instance/ai_keys.env and restart the server.")
    if PROVIDER == "gemini":
        data = _post(
            GEMINI_URL.format(model=GEMINI_MODEL) + f"?key={GEMINI_KEY}",
            {"system_instruction": {"parts": [{"text": system_prompt}]},
             "contents": [{"role": "user", "parts": [{"text": user_text}]}],
             "generationConfig": {"responseMimeType": "application/json",
                                  "temperature": 0}},
            {"content-type": "application/json"})
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            raise RuntimeError(
                "Gemini returned no content. The message may have been "
                "blocked by a safety filter.")
        text = "".join(p.get("text", "") for p in parts)
    else:
        data = _post(
            ANTHROPIC_URL,
            {"model": ANTHROPIC_MODEL, "max_tokens": max_tokens,
             "system": system_prompt,
             "messages": [{"role": "user", "content": user_text}]},
            {"content-type": "application/json", "x-api-key": ANTHROPIC_KEY,
             "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in data.get("content", []))

    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError("The model did not return valid JSON.")


def call_model(raw_text):
    return ask_model(SYSTEM_PROMPT, raw_text).get("listings", [])


def next_ref(conn):
    """Continue the existing PRE-P-#### sequence."""
    highest = 0
    for row in conn.execute(
            "SELECT ref FROM properties WHERE ref LIKE ?",
            (REF_PREFIX + "%",)):
        match = re.search(r"(\d+)\s*$", row["ref"] or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{REF_PREFIX}{highest + 1:0{REF_DIGITS}d}"


def link_source(conn, source_type, name, phone, now):
    """Find the owner or partner this listing came from, creating one if new.

    Returns (column_name, row_id, what_happened) so the reviewer can be told.
    """
    table = "partners" if source_type == "agency" else "owners"
    column = "partner_id" if source_type == "agency" else "owner_id"
    if not name and not phone:
        return column, None, "no source given"

    rows = conn.execute(f"SELECT id, name, phone FROM {table}").fetchall()

    if phone:
        target = norm_phone(phone)
        for row in rows:
            if row["phone"] and norm_phone(row["phone"]) == target:
                return column, row["id"], f"matched existing {table[:-1]} by phone"

    if name:
        target = name.strip().lower()
        for row in rows:
            if row["name"] and row["name"].strip().lower() == target:
                return column, row["id"], f"matched existing {table[:-1]} by name"

    cur = conn.execute(
        f"INSERT INTO {table} (name, phone, notes, created_at) "
        "VALUES (?, ?, ?, ?)",
        (name, norm_phone(phone), "Added by listing intake", now))
    return column, cur.lastrowid, f"created a new {table[:-1]} record"


def find_duplicates(listing):
    """Flag likely duplicates already in the properties table."""
    conn = db()
    hits = []
    bld, unit = listing.get("building"), listing.get("unit")
    if bld and unit:
        rows = conn.execute(
            f"SELECT id FROM properties WHERE {FIELD_MAP['building']} = ? "
            f"COLLATE NOCASE AND {FIELD_MAP['unit']} = ? COLLATE NOCASE",
            (bld, unit)).fetchall()
        hits += [(r["id"], "same building and unit") for r in rows]

    # No contact column on properties, so match on the shape of the listing.
    area, beds, price = (listing.get("area"), listing.get("bedrooms"),
                         listing.get("price"))
    if area and beds is not None and price:
        rows = conn.execute(
            f"SELECT id FROM properties WHERE {FIELD_MAP['area']} = ? "
            f"COLLATE NOCASE AND {FIELD_MAP['bedrooms']} = ? "
            f"AND {FIELD_MAP['price']} = ?", (area, beds, price)).fetchall()
        hits += [(r["id"], "same area, bedrooms and price") for r in rows]

    seen, out = set(), []
    for pid, reason in hits:
        if pid not in seen:
            seen.add(pid)
            out.append({"property_id": pid, "reason": reason})
    return out


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------


@intake_bp.route("/")
@admin_required
def index():
    rows = db().execute(
        "SELECT * FROM intake_drafts WHERE status = 'pending' "
        "ORDER BY created_at DESC LIMIT 100").fetchall()
    drafts = []
    for row in rows:
        d = dict(row)
        d["data"] = json.loads(row["extracted_json"])
        drafts.append(d)
    return render_template("intake.html", drafts=drafts,
                           key_missing=not api_key_present(),
                           provider=PROVIDER)


@intake_bp.route("/parse", methods=["POST"])
@admin_required
def parse():
    raw = (request.form.get("raw_text") or
           (request.json or {}).get("raw_text", "")).strip()
    if not raw:
        return jsonify({"error": "Paste a message first."}), 400

    try:
        listings = call_model(raw)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    if not listings:
        return jsonify({"error": "No property listing found in that text.",
                        "count": 0}), 200

    conn = db()
    created = []
    now = datetime.now().isoformat(timespec="seconds")
    for listing in listings:
        listing = normalise(listing)
        listing["duplicates"] = find_duplicates(listing)
        cur = conn.execute(
            "INSERT INTO intake_drafts (raw_text, extracted_json, status, "
            "created_by, created_at) VALUES (?, ?, 'pending', ?, ?)",
            (raw, json.dumps(listing, ensure_ascii=False),
             current_user_id(), now))
        created.append(cur.lastrowid)
    conn.commit()
    return jsonify({"count": len(created), "draft_ids": created})


@intake_bp.route("/draft/<int:draft_id>")
@admin_required
def review(draft_id):
    row = db().execute("SELECT * FROM intake_drafts WHERE id = ?",
                       (draft_id,)).fetchone()
    if not row:
        return "Draft not found", 404
    return render_template("review.html", draft=dict(row),
                           data=json.loads(row["extracted_json"]),
                           fields=list(FIELD_MAP.keys()))


@intake_bp.route("/draft/<int:draft_id>/approve", methods=["POST"])
@admin_required
def approve(draft_id):
    conn = db()
    row = conn.execute("SELECT * FROM intake_drafts WHERE id = ?",
                       (draft_id,)).fetchone()
    if not row or row["status"] != "pending":
        return "Draft not available", 404

    # The reviewer's edits from the form win over the extracted values.
    form = {k: (v.strip() or None) for k, v in request.form.items()}

    values = {}
    for field, column in FIELD_MAP.items():
        values[column] = form.get(field)
    values["bedrooms"] = norm_bedrooms(values["bedrooms"])
    for col in ("price", "size_sqm", "bathrooms"):
        values[col] = norm_number(values[col])

    # Fields with no column of their own ride along in extras as JSON.
    extras = {k: form.get(k) for k in EXTRA_FIELDS if form.get(k)}
    if extras.get("contact_phone"):
        extras["contact_phone"] = norm_phone(extras["contact_phone"])

    now = datetime.now().isoformat(timespec="seconds")

    # Link to an existing owner or partner, or create one.
    source_col, source_id, source_note = link_source(
        conn, form.get("source_type"), form.get("source_name"),
        form.get("contact_phone"), now)

    values.update({
        "ref": next_ref(conn),
        "title": form.get("title") or None,
        "status": DEFAULT_STATUS,
        "extras": json.dumps(extras, ensure_ascii=False) if extras else None,
        "is_own": 1 if form.get("is_own") else 0,
        "agent_id": current_user_id(),
        "import_source": "intake_agent",
        "imported_at": now,
        "created_at": now,
        "updated_at": now,
    })
    if source_id:
        values[source_col] = source_id

    cols = ", ".join(values.keys())
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(
        f"INSERT INTO properties ({cols}) VALUES ({marks})",
        list(values.values()))
    property_id = cur.lastrowid

    conn.execute(
        "UPDATE intake_drafts SET status = 'approved', property_id = ?, "
        "reviewed_by = ?, reviewed_at = ? WHERE id = ?",
        (property_id, current_user_id(), now, draft_id))
    conn.commit()
    flash(f"Added {values['ref']} — {source_note}.", "success")
    return redirect(url_for("intake.index"))


@intake_bp.route("/draft/<int:draft_id>/reject", methods=["POST"])
@admin_required
def reject(draft_id):
    conn = db()
    conn.execute(
        "UPDATE intake_drafts SET status = 'rejected', reviewed_by = ?, "
        "reviewed_at = ? WHERE id = ? AND status = 'pending'",
        (current_user_id(), datetime.now().isoformat(timespec="seconds"),
         draft_id))
    conn.commit()
    flash("Draft discarded.", "info")
    return redirect(url_for("intake.index"))
