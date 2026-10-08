"""Dashboard, global search, notifications and the activity trail."""
import os
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, g, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)

from auth import admin_required, login_required, published_only, sees_all
from db import execute, local_now, local_today, paginate, query, utc_day_bounds

bp = Blueprint("main", __name__)

@bp.route("/")
@login_required
def dashboard():
    mine = "" if sees_all() else " AND agent_id = %d" % g.user["id"]
    day_start, day_end = utc_day_bounds(local_today())

    # Counts describe the stock we can actually offer, so anything still
    # waiting to be published is left out.
    live = published_only("p")
    total_listings = query(
        "SELECT COUNT(*) c FROM properties p WHERE 1=1" + live, one=True)["c"]
    available = query(
        "SELECT COUNT(*) c FROM properties p WHERE p.status='Available'" + live,
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
    open_stages = " AND l.status NOT IN ('Won','Lost') AND l.archived_at IS NULL"

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

    todo = _todo_lists()
    own = _own_available(request.args.get("own", "all"))

    return render_template(
        "dashboard.html", total_listings=total_listings, available=available,
        new_leads=new_leads, pending_deals=pending_deals, upcoming=upcoming,
        pipeline=pipeline, leads_week=leads_week,
        commission=commission, viewings_count=len(upcoming),
        overdue=overdue, due_today=due_today, no_followup=no_followup,
        todo=todo, own=own, sees_all=sees_all())


# How many rows each to-do group shows before pointing to its full page.
TODO_LIMIT = 5


def _todo_lists():
    """Everything waiting on this person apart from calls, in one place.

    Each of these already has its own screen (Waiting for approval, Stale
    listings, Leases ending); until now the only sign of them on the way in was
    a small counter in the menu. The dashboard lists the first few of each so
    nobody has to go looking, and links to the full screen for the rest. The
    queries mirror those screens exactly, so the counts always agree.
    """
    import leases
    from auth import can_publish
    from db import STALE_DAYS, days_ago

    # Listings waiting for approval. An admin sees what they have to publish;
    # anyone else sees what they sent, including anything sent back to them.
    publisher = can_publish()
    wsql = ("SELECT p.*, s.name AS submitted_name FROM properties p"
            " LEFT JOIN users s ON s.id = p.submitted_by WHERE ")
    wargs = []
    if publisher:
        wsql += "COALESCE(p.approval, 'approved') = 'pending'"
    else:
        wsql += ("COALESCE(p.approval, 'approved') IN ('pending', 'rejected')"
                 " AND p.submitted_by = ?")
        wargs.append(g.user["id"])
    waiting = query(wsql + " ORDER BY p.id LIMIT %d" % TODO_LIMIT, wargs)
    waiting_total = query(wsql.replace("SELECT p.*, s.name AS submitted_name",
                                       "SELECT COUNT(*) c"), wargs, one=True)["c"]

    # Listings nobody has confirmed are still on the market — same rule as /stale.
    ssql = (" FROM properties p WHERE p.status IN ('Available','Reserved')"
            + published_only("p") +
            " AND (p.last_verified IS NULL OR p.last_verified < ?)")
    sargs = [days_ago(STALE_DAYS)]
    if not sees_all():
        ssql += " AND (p.agent_id = ? OR p.agent_id IS NULL)"
        sargs.append(g.user["id"])
    stale = query("SELECT p.*" + ssql +
                  " ORDER BY (p.last_verified IS NULL) DESC, p.last_verified, p.id"
                  " LIMIT %d" % TODO_LIMIT, sargs)
    stale_total = query("SELECT COUNT(*) c" + ssql, sargs, one=True)["c"]

    # Tenancies on our own units that have run out or are about to.
    lease_rows = leases.ending()
    if not sees_all():
        lease_rows = [r for r in lease_rows
                      if g.user["id"] in (r["prop_agent_id"], r["agent_id"])]
    today = local_today()
    lease_items = [{"deal": r, "left": leases.days_left(r["lease_end"], today)}
                   for r in lease_rows[:TODO_LIMIT]]

    return {
        "publisher": publisher,
        "waiting": waiting, "waiting_total": waiting_total,
        "stale": [dict(r, days=_days_since(r["last_verified"])) for r in stale],
        "stale_total": stale_total,
        "leases": lease_items, "leases_total": len(lease_rows),
    }


def _days_since(stamp):
    """Whole days from a stored UTC timestamp to now; None if never set."""
    if not stamp:
        return None
    try:
        then = datetime.strptime(str(stamp)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return max(0, (datetime.utcnow() - then).days)


def _own_available(kind):
    """Planned Real Estate's own stock that is still on the market.

    Three conditions, all required: the "Owned by <company>" box is ticked
    (partner and third-party listings are left out), the listing has been
    approved (a listing waiting for approval is also 'Available', so without
    this it would slip in), and its status is Available. Shown to everyone,
    whoever the agent is: these are the whole office's priority.

    Oldest first, because the unit that has sat longest is the one to push.
    """
    from urllib.parse import quote
    from db import get_setting

    base = (" FROM properties p WHERE COALESCE(p.is_own, 0) = 1"
            " AND p.status = 'Available'" + published_only("p"))
    counts = {r["listing_type"]: r["c"] for r in query(
        "SELECT p.listing_type, COUNT(*) c" + base + " GROUP BY p.listing_type")}
    kind = kind if kind in ("sale", "rent") else "all"
    where, args = base, []
    if kind != "all":
        where += " AND p.listing_type = ?"
        args.append(kind.capitalize())

    rows = query(
        "SELECT p.*, u.name AS agent_name,"
        " (SELECT filename FROM property_images i WHERE i.property_id = p.id"
        "   ORDER BY is_cover DESC, id LIMIT 1) AS cover,"
        " (SELECT COUNT(*) FROM viewings v WHERE v.property_id = p.id) AS viewings"
        + where.replace(" FROM properties p",
                        " FROM properties p LEFT JOIN users u ON u.id = p.agent_id")
        + " ORDER BY p.created_at, p.id LIMIT 10", args)

    company = get_setting("company_name", "Planned Real Estate")
    currency = get_setting("currency", "QAR")
    items = []
    for r in rows:
        specs = [r["prop_type"]]
        if r["bedrooms"]:
            specs.append(f"{r['bedrooms']} bed")
        if r["size_sqm"]:
            specs.append(f"{r['size_sqm']:,.0f} m²")
        if r["area"]:
            specs.append(r["area"])
        price = f"{currency} {r['price']:,.0f}" if r["price"] else ""
        if price and r["listing_type"] == "Rent":
            price += " / month"
        # A WhatsApp link with no number opens WhatsApp's own contact picker,
        # so the agent chooses who to send it to.
        message = "\n".join(x for x in [
            r["title"], " · ".join(specs), price,
            f"Ref {r['ref']}" if r["ref"] else "", company] if x)
        items.append(dict(
            r, specs=" · ".join(specs[:3]),
            on_market=_days_since(r["created_at"]),
            checked=_days_since(r["last_verified"]),
            share_url="https://wa.me/?text=" + quote(message)))

    return {
        "rows": items, "kind": kind,
        "total": sum(counts.values()),
        "sale": counts.get("Sale", 0), "rent": counts.get("Rent", 0),
    }


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    props, leads, owners = [], [], []
    if q:
        like = f"%{q}%"
        props = query(
            "SELECT * FROM properties p WHERE (p.title LIKE ? OR p.address LIKE ?"
            " OR p.area LIKE ? OR p.ref LIKE ? OR p.building_no LIKE ?"
            " OR p.unit_no LIKE ?)" + published_only("p") +
            " ORDER BY p.id DESC LIMIT 25",
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
@admin_required
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
