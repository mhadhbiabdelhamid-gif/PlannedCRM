"""Dashboard, global search, notifications and the activity trail."""
import os
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, g, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)

from auth import admin_required, is_admin, login_required, published_only, sees_all
from db import execute, local_now, local_today, log, paginate, query, utc_day_bounds

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

    # Approvals and ending leases are followed by the admin, who keeps the
    # team updated; staff see only what they act on themselves.
    if not is_admin():
        waiting, waiting_total, lease_rows, lease_items = [], 0, [], []

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


OWN_SELECT = (
    "SELECT p.*, u.name AS agent_name,"
    " (SELECT filename FROM property_images i WHERE i.property_id = p.id"
    "   ORDER BY is_cover DESC, id LIMIT 1) AS cover,"
    " (SELECT COUNT(*) FROM viewings v WHERE v.property_id = p.id) AS viewings"
    " FROM properties p LEFT JOIN users u ON u.id = p.agent_id")


def _own_item(r, company, currency):
    """One of our listings, ready for the dashboard or the printed sheet."""
    from urllib.parse import quote
    import leases
    free = leases.free_from(r)
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
        f"Available from {free}" if free else "",
        f"Ref {r['ref']}" if r["ref"] else "", company] if x)
    return dict(
        r, specs=" · ".join(specs[:3]), all_specs=" · ".join(specs),
        on_market=_days_since(r["created_at"]),
        checked=_days_since(r["last_verified"]),
        free_from=free,
        share_url="https://wa.me/?text=" + quote(message))


def _own_lists(kind="all", limit=None):
    """Planned Real Estate's own stock: what is on the market now, and the
    rented units coming free within a month.

    "Our own" means the "Owned by <company>" box is ticked (partner and
    third-party listings are left out) and the listing has been approved (a
    listing waiting for approval is also 'Available', so without this it
    would slip in). Shown to everyone, whoever the agent is: these are the
    whole office's priority.

    Available units oldest first, because the unit that has sat longest is
    the one to push; coming-free units soonest first.
    """
    from db import get_setting
    import leases

    own = " WHERE COALESCE(p.is_own, 0) = 1" + published_only("p")
    avail = own + " AND p.status = 'Available'"
    soon = (own + " AND p.status = 'Rented' AND p.rent_end IS NOT NULL"
            " AND p.rent_end <= ?")
    horizon = leases.soon_horizon()

    counts = {r["listing_type"]: r["c"] for r in query(
        "SELECT p.listing_type, COUNT(*) c FROM properties p" + avail
        + " GROUP BY p.listing_type")}
    kind = kind if kind in ("sale", "rent") else "all"
    extra, args = "", []
    if kind != "all":
        extra = " AND p.listing_type = ?"
        args.append(kind.capitalize())
    cap = f" LIMIT {int(limit)}" if limit else ""

    rows = query(OWN_SELECT + avail + extra + " ORDER BY p.created_at, p.id" + cap, args)
    soon_rows = query(OWN_SELECT + soon + extra + " ORDER BY p.rent_end, p.id",
                      [horizon] + args)

    company = get_setting("company_name", "Planned Real Estate")
    currency = get_setting("currency", "QAR")
    return {
        "rows": [_own_item(r, company, currency) for r in rows],
        "soon": [_own_item(r, company, currency) for r in soon_rows],
        "kind": kind,
        "total": sum(counts.values()),
        "sale": counts.get("Sale", 0), "rent": counts.get("Rent", 0),
    }


def _own_available(kind):
    return _own_lists(kind, limit=10)


def _price_totals(rows):
    """Monthly rent and sale value added up separately: a month's rent and a
    sale price summed together would be a meaningless number."""
    rent = [r for r in rows if r["listing_type"] == "Rent"]
    sale = [r for r in rows if r["listing_type"] != "Rent"]
    return {"rent_total": sum(r["price"] or 0 for r in rent), "rent_n": len(rent),
            "sale_total": sum(r["price"] or 0 for r in sale), "sale_n": len(sale)}


@bp.route("/agent-sheet")
@login_required
def agent_sheet():
    """The vacant list: one printable page to hand the agents with every one
    of our own units that is vacant now or comes free within a month, and
    for each the building, floor, flat, furnishing, price, a map link and a
    photo link. Printed, or saved as PDF from the browser's print dialog.

    Leases ending soon are left off on purpose: for our own units they are
    the same listings as "coming free", seen from the tenant's side, and
    that follow-up stays on the dashboard and the Leases ending page.
    """
    import media_share
    kind = request.args.get("kind", "all")
    own = _own_lists(kind)
    rows = [dict(r, vacant_now=True) for r in own["rows"]] + \
           [dict(r, vacant_now=False) for r in own["soon"]]

    def unit_key(r):
        unit = (r["unit_no"] or "").strip()
        return ((r["area"] or "~").lower(), (r["building_no"] or "~").lower(),
                (r["floor_no"] or ""), len(unit), unit)
    rows.sort(key=unit_key)

    for r in rows:
        r["photo_url"], r["photo_count"] = media_share.listing_link(r, g.user["id"])

    # Residential first, then offices and shops; land (rare) on its own.
    groups = [
        ("Residential", "res", ("Villa", "Apartment", "Studio")),
        ("Office & Commercial", "com", ("Office", "Commercial")),
        ("Land", "land", ("Land",)),
    ]
    known = {tp for _, _, types in groups for tp in types}
    sections = []
    for label, key, types in groups:
        part = [r for r in rows if r["prop_type"] in types
                or (key == "res" and r["prop_type"] not in known)]
        if part:
            sections.append({"label": label, "key": key, "rows": part,
                             **_price_totals(part)})
    grand = _price_totals(rows)

    log(g.user["id"], "Printed the vacant list",
        detail=f"{len(own['rows'])} vacant now, {len(own['soon'])} coming free")
    import areas
    return render_template("agent_sheet.html", rows=rows, own=own, kind=own["kind"],
                           sections=sections, grand=grand,
                           area_ar=areas.in_arabic,
                           printed_at=local_now().strftime("%d %b %Y, %H:%M"),
                           printed_day=local_now().strftime("%d %b %Y"))


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    props, leads, owners = [], [], []
    if q:
        like = f"%{q}%"
        # the same word-by-word reading as the Properties search box, so
        # "2 bed pearl" here finds what it finds there
        from views_properties import FILTER_FIELDS, _filter_clause
        f = {k: "" for k in FILTER_FIELDS}
        f["q"] = q
        clause, args = _filter_clause(f)
        props = query(
            "SELECT * FROM properties p WHERE 1=1" + clause + published_only("p") +
            " ORDER BY p.id DESC LIMIT 25", args)
        # archived clients are the admin's; staff search the live pipeline
        arch, arch_args = ("", [])
        if not is_admin():
            from views_leads import _not_archived
            arch, arch_args = _not_archived()
        leads = query(
            "SELECT l.*, u.name AS agent_name FROM leads l"
            " LEFT JOIN users u ON u.id = l.agent_id"
            " WHERE (l.full_name LIKE ? OR l.phone LIKE ? OR l.email LIKE ?"
            " OR l.ref LIKE ?)" + arch + " ORDER BY l.id DESC LIMIT 25",
            [like, like, like, like] + arch_args)
        if is_admin():
            owners = query(
                "SELECT * FROM owners WHERE name LIKE ? OR phone LIKE ? LIMIT 15",
                (like, like))
    return render_template("search.html", q=q, props=props, leads=leads, owners=owners)


@bp.route("/notifications")
@login_required
def notifications():
    # The team's activity feed is the admin's; staff work from their dashboard.
    if not is_admin():
        return redirect(url_for("main.dashboard"))
    items = query("SELECT * FROM notifications WHERE user_id = ?"
                  " ORDER BY id DESC LIMIT 100", (g.user["id"],))
    execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (g.user["id"],))
    return render_template("notifications.html", items=items)


@bp.route("/notifications/count")
@login_required
def notifications_count():
    if not is_admin():
        return jsonify(count=0)
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
    if kind not in ("images", "docs", "avatars", "contacts", "videos"):
        return redirect(url_for("main.dashboard"))
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], kind)
    return send_from_directory(folder, filename)
