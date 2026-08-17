"""Dashboard, global search, notifications and the activity trail."""
import os
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, g, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)

from auth import is_admin, login_required, sees_all
from db import execute, local_now, local_today, paginate, query, utc_day_bounds

bp = Blueprint("main", __name__)

@bp.route("/")
@login_required
def dashboard():
    mine = "" if sees_all() else " AND agent_id = %d" % g.user["id"]
    day_start, day_end = utc_day_bounds(local_today())

    total_listings = query("SELECT COUNT(*) c FROM properties", one=True)["c"]
    available = query("SELECT COUNT(*) c FROM properties WHERE status='Available'",
                      one=True)["c"]
    new_leads = query("SELECT COUNT(*) c FROM leads"
                      " WHERE created_at >= ? AND created_at < ?" + mine,
                      (day_start, day_end), one=True)["c"]
    pending_deals = query("SELECT COUNT(*) c FROM leads WHERE status IN ('Offer','Viewing')"
                          + mine, one=True)["c"]

    upcoming = query(
        "SELECT v.*, l.full_name, p.title AS prop_title, u.name AS agent_name"
        " FROM viewings v"
        " LEFT JOIN leads l ON l.id = v.lead_id"
        " LEFT JOIN properties p ON p.id = v.property_id"
        " LEFT JOIN users u ON u.id = v.agent_id"
        " WHERE v.done = 0 AND v.scheduled_at >= ?"
        + ("" if sees_all() else " AND v.agent_id = %d" % g.user["id"]) +
        " ORDER BY v.scheduled_at LIMIT 8", (day_start,))

    pipeline = {r["status"]: r["c"] for r in query(
        "SELECT status, COUNT(*) c FROM leads WHERE 1=1" + mine + " GROUP BY status")}

    # --- follow-ups: the thing an agent opens the CRM to find out
    scope = "" if sees_all() else " AND l.agent_id = %d" % g.user["id"]
    open_stages = " AND l.status NOT IN ('Won','Lost')"

    overdue = query(
        "SELECT l.*, u.name AS agent_name FROM leads l"
        " LEFT JOIN users u ON u.id = l.agent_id"
        " WHERE l.next_follow_up IS NOT NULL AND l.next_follow_up < ?"
        + open_stages + scope + " ORDER BY l.next_follow_up LIMIT 12",
        (day_start,))
    due_today = query(
        "SELECT l.*, u.name AS agent_name FROM leads l"
        " LEFT JOIN users u ON u.id = l.agent_id"
        " WHERE l.next_follow_up >= ? AND l.next_follow_up < ?"
        + open_stages + scope + " ORDER BY l.next_follow_up LIMIT 12",
        (day_start, day_end))
    no_followup = query(
        "SELECT COUNT(*) c FROM leads l"
        " WHERE (l.next_follow_up IS NULL OR TRIM(l.next_follow_up) = '')"
        + open_stages + scope, one=True)["c"]

    feed = query(
        "SELECT a.*, u.name AS user_name FROM activity a"
        " LEFT JOIN users u ON u.id = a.user_id"
        " ORDER BY a.id DESC LIMIT 12")

    week_start, _ = utc_day_bounds(
        (local_now() - timedelta(days=7)).strftime("%Y-%m-%d"))
    leads_week = query("SELECT COUNT(*) c FROM leads WHERE created_at >= ?" + mine,
                       (week_start,), one=True)["c"]

    month_start, _ = utc_day_bounds(local_now().strftime("%Y-%m-01"))
    deal_mine = "" if sees_all() else " AND agent_id = %d" % g.user["id"]
    commission = query(
        "SELECT COALESCE(SUM(commission_amt), 0) AS total FROM deals"
        " WHERE status != 'Cancelled' AND COALESCE(closed_at, created_at) >= ?"
        + deal_mine, (month_start,), one=True)["total"]

    return render_template(
        "dashboard.html", total_listings=total_listings, available=available,
        new_leads=new_leads, pending_deals=pending_deals, upcoming=upcoming,
        pipeline=pipeline, feed=feed, leads_week=leads_week,
        commission=commission, viewings_count=len(upcoming),
        overdue=overdue, due_today=due_today, no_followup=no_followup)


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    props, leads, owners = [], [], []
    if q:
        like = f"%{q}%"
        props = query(
            "SELECT * FROM properties WHERE title LIKE ? OR address LIKE ?"
            " OR area LIKE ? OR ref LIKE ? OR building_no LIKE ? OR unit_no LIKE ?"
            " ORDER BY id DESC LIMIT 25",
            (like,) * 6)
        leads = query(
            "SELECT l.*, u.name AS agent_name FROM leads l"
            " LEFT JOIN users u ON u.id = l.agent_id"
            " WHERE l.full_name LIKE ? OR l.phone LIKE ? OR l.email LIKE ?"
            " OR l.ref LIKE ? ORDER BY l.id DESC LIMIT 25",
            (like, like, like, like))
        owners = query(
            "SELECT * FROM owners WHERE name LIKE ? OR phone LIKE ? LIMIT 15",
            (like, like))
    return render_template("search.html", q=q, props=props, leads=leads, owners=owners)


@bp.route("/notifications")
@login_required
def notifications():
    items = query("SELECT * FROM notifications WHERE user_id = ?"
                  " ORDER BY id DESC LIMIT 100", (g.user["id"],))
    execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (g.user["id"],))
    return render_template("notifications.html", items=items)


@bp.route("/notifications/count")
@login_required
def notifications_count():
    row = query("SELECT COUNT(*) c FROM notifications WHERE user_id = ? AND is_read = 0",
                (g.user["id"],), one=True)
    return jsonify(count=row["c"])


@bp.route("/activity")
@login_required
def activity():
    pager = paginate(
        "SELECT a.*, u.name AS user_name, u.photo AS user_photo FROM activity a"
        " LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC",
        [], request.args.get("page", 1), per_page=60)
    return render_template("activity.html", rows=pager["rows"], pager=pager, args={})


@bp.route("/uploads/<path:kind>/<path:filename>")
@login_required
def uploaded_file(kind, filename):
    if kind not in ("images", "docs", "avatars", "contacts"):
        return redirect(url_for("main.dashboard"))
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], kind)
    return send_from_directory(folder, filename)
