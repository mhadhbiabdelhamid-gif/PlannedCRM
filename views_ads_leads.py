"""Leads > Import from ads: pulling Facebook/Instagram lead-form submissions
in live via Windsor.ai, and reading in a TikTok Leads Center CSV export by
hand. See ads_leads.py for the actual fetching/parsing/inserting logic —
this module is just the routes and file-upload handling around it, kept in
the same shape as views_imports.py's upload -> review -> commit flow.
"""
import os
import uuid

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, url_for)

import ads_leads
from auth import requires
from db import query

bp = Blueprint("ads_leads", __name__, url_prefix="/leads/import")

MAX_PREVIEW = 12


def _folder():
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "lead_imports")
    os.makedirs(folder, exist_ok=True)
    return folder


def _path(token):
    """Tokens are generated here, never taken from the browser unchecked."""
    safe = os.path.basename(token)
    if not safe.endswith(".csv"):
        return None
    path = os.path.join(_folder(), safe)
    return path if os.path.exists(path) else None


@bp.route("/")
@requires("ads_leads")
def index():
    recent = query(
        "SELECT a.*, u.name AS user_name FROM activity a"
        " LEFT JOIN users u ON u.id = a.user_id"
        " WHERE a.action = 'Imported ad leads' ORDER BY a.id DESC LIMIT 8")
    return render_template(
        "leads/import.html", recent=recent, ranges=ads_leads.RANGES,
        facebook_ready=ads_leads.facebook_ready())


@bp.route("/facebook", methods=("POST",))
@requires("ads_leads")
def facebook():
    range_key = request.form.get("range", "last_30d")
    if range_key not in dict(ads_leads.RANGES):
        range_key = "last_30d"
    if not ads_leads.facebook_ready():
        flash("No Windsor.ai key is set up yet. An admin can add one under "
              "Settings > Marketing.", "error")
        return redirect(url_for("ads_leads.index"))

    date_from, date_to = ads_leads.range_dates(range_key)
    result = ads_leads.import_facebook_leads(date_from, date_to, g.user["id"])
    if not result["ok"]:
        flash(f"Couldn't reach Facebook/Instagram leads: {result['error']}", "error")
    elif result["added"] == 0:
        flash(f"No new leads in that window ({result['fetched']} checked, "
              f"all already in the CRM).", "info")
    else:
        flash(f"Imported {result['added']} new lead(s) from Facebook/Instagram "
              f"as New — {result['skipped']} already in the CRM were skipped.", "ok")
    return redirect(url_for("ads_leads.index"))


@bp.route("/tiktok", methods=("POST",))
@requires("ads_leads")
def tiktok_upload():
    fs = request.files.get("csv_file")
    if not fs or not fs.filename:
        flash("Choose the CSV file exported from TikTok's Leads Center.", "error")
        return redirect(url_for("ads_leads.index"))
    if not fs.filename.lower().endswith(".csv"):
        flash("That needs to be a .csv file — TikTok's Leads Center download "
              "button gives you one directly.", "error")
        return redirect(url_for("ads_leads.index"))

    token = f"{uuid.uuid4().hex}.csv"
    fs.save(os.path.join(_folder(), token))
    return redirect(url_for("ads_leads.tiktok_review", token=token,
                            name=fs.filename))


@bp.route("/tiktok/review/<token>")
@requires("ads_leads")
def tiktok_review(token):
    path = _path(token)
    if not path:
        flash("That upload has expired. Please upload the file again.", "error")
        return redirect(url_for("ads_leads.index"))

    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        headers, mapping, rows, total = ads_leads.preview_tiktok_csv(raw, limit=MAX_PREVIEW)
    except ValueError as exc:
        os.remove(path)
        flash(str(exc), "error")
        return redirect(url_for("ads_leads.index"))

    usable = sum(1 for r in rows if r["usable"])
    return render_template(
        "leads/import_tiktok_review.html", token=token,
        filename=request.args.get("name", "leads.csv"),
        headers=headers, mapping=mapping, rows=rows, total=total,
        matched=list(mapping.keys()))


@bp.route("/tiktok/commit/<token>", methods=("POST",))
@requires("ads_leads")
def tiktok_commit(token):
    path = _path(token)
    if not path:
        flash("That upload has expired. Please upload the file again.", "error")
        return redirect(url_for("ads_leads.index"))

    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        result = ads_leads.import_tiktok_csv(raw, g.user["id"])
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("ads_leads.tiktok_review", token=token))

    try:
        os.remove(path)
    except OSError:
        pass

    if result["added"] == 0:
        flash(f"Nothing new to add — all {result['skipped']} row(s) were "
              f"already in the CRM or unusable.", "info")
    else:
        flash(f"Imported {result['added']} new lead(s) from TikTok as New"
              + (f" — {result['skipped']} skipped." if result["skipped"] else "."),
              "ok")
    return redirect(url_for("leads.board"))


@bp.route("/tiktok/discard/<token>", methods=("POST",))
@requires("ads_leads")
def tiktok_discard(token):
    path = _path(token)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    flash("Upload discarded. Nothing was saved.", "ok")
    return redirect(url_for("ads_leads.index"))
