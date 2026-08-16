"""Closed deals and the commission they earn."""
from flask import (Blueprint, flash, g, redirect, render_template, request,
                   url_for)

from auth import admin_required, can_edit, login_required, sees_all
from db import (DEAL_STATUS, LISTING_TYPES, execute, get_setting, local_now,
                log, next_ref, notify, now, paginate, query, to_utc)

bp = Blueprint("deals", __name__, url_prefix="/deals")


def _scope(prefix="d"):
    if sees_all():
        return "", []
    return f" AND {prefix}.agent_id = ?", [g.user["id"]]


@bp.route("/")
@login_required
def index():
    where, args = _scope()
    status = request.args.get("status", "")
    if status:
        where += " AND d.status = ?"
        args.append(status)

    base = ("SELECT d.*, p.title AS prop_title, p.ref AS prop_ref,"
            " l.full_name AS lead_name, u.name AS agent_name FROM deals d"
            " LEFT JOIN properties p ON p.id = d.property_id"
            " LEFT JOIN leads l ON l.id = d.lead_id"
            " LEFT JOIN users u ON u.id = d.agent_id"
            " WHERE 1=1" + where)
    pager = paginate(base + " ORDER BY d.id DESC", args, request.args.get("page", 1))
    rows = pager["rows"]

    # totals cover every matching deal, not just the page on screen
    sums = query("SELECT COALESCE(SUM(d.value),0) v,"
                 " COALESCE(SUM(d.commission_amt),0) e,"
                 " COALESCE(SUM(CASE WHEN d.status='Collected'"
                 "   THEN d.commission_amt ELSE 0 END),0) c"
                 " FROM deals d WHERE d.status != 'Cancelled'"
                 + where.replace("d.status = ?", "d.status = ?"), args, one=True)
    totals = {"value": sums["v"], "earned": sums["e"], "collected": sums["c"]}
    totals["pending"] = totals["earned"] - totals["collected"]

    return render_template("deals/index.html", rows=rows, totals=totals,
                           status=status, deal_status=DEAL_STATUS, pager=pager,
                           args={"status": status} if status else {})


@bp.route("/new", methods=("GET", "POST"))
@bp.route("/<int:did>/edit", methods=("GET", "POST"))
@login_required
def form(did=None):
    d = query("SELECT * FROM deals WHERE id = ?", (did,), one=True) if did else None
    if did and d is None:
        flash("That deal no longer exists.", "error")
        return redirect(url_for("deals.index"))
    if d is not None and not can_edit(d):
        flash("That deal belongs to another agent.", "error")
        return redirect(url_for("deals.index"))

    if request.method == "POST":
        f = request.form
        value = float(f.get("value") or 0)
        pct = float(f.get("commission_pct") or 0)
        # A typed amount always wins; otherwise it's worked out from the percentage.
        amt_raw = f.get("commission_amt", "").strip()
        amt = float(amt_raw) if amt_raw else round(value * pct / 100, 2)

        vals = (int(f["property_id"]) if f.get("property_id") else None,
                int(f["lead_id"]) if f.get("lead_id") else None,
                int(f["agent_id"]) if f.get("agent_id") else None,
                f.get("deal_type", "Sale"), value, pct, amt,
                f.get("status", "Agreed"), to_utc(f.get("closed_at")),
                f.get("notes", "").strip())

        if d is None:
            ref = next_ref("PRE-D", "deals")
            did = execute(
                "INSERT INTO deals (property_id,lead_id,agent_id,deal_type,value,"
                "commission_pct,commission_amt,status,closed_at,notes,ref,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                vals + (ref, now(), now()))
            log(g.user["id"], "Recorded deal", "deal", did,
                f"{ref} — {value:,.0f} at {pct}%")
            if vals[2] and vals[2] != g.user["id"]:
                notify(vals[2], f"A deal was recorded under your name ({ref})",
                       url_for("deals.index"))
            flash("Deal recorded.", "ok")
        else:
            execute("UPDATE deals SET property_id=?,lead_id=?,agent_id=?,deal_type=?,"
                    "value=?,commission_pct=?,commission_amt=?,status=?,closed_at=?,"
                    "notes=?,updated_at=? WHERE id=?", vals + (now(), did))
            detail = (f"status {d['status']} → {vals[7]}"
                      if d["status"] != vals[7] else "details edited")
            log(g.user["id"], "Updated deal", "deal", did, detail)
            flash("Deal saved.", "ok")
        return redirect(url_for("deals.index"))

    agents = query("SELECT id, name FROM users WHERE is_active = 1 ORDER BY name")
    props = query("SELECT id, ref, title, price, listing_type FROM properties"
                  " ORDER BY id DESC LIMIT 300")
    leads = query("SELECT id, ref, full_name FROM leads ORDER BY id DESC LIMIT 300")

    prefill = {}
    if d is None:
        lead_id = request.args.get("lead")
        if lead_id:
            lead = query("SELECT l.*, p.price, p.listing_type FROM leads l"
                         " LEFT JOIN properties p ON p.id = l.property_id"
                         " WHERE l.id = ?", (lead_id,), one=True)
            if lead:
                prefill = {
                    "lead_id": lead["id"],
                    "property_id": lead["property_id"],
                    "agent_id": lead["agent_id"] or g.user["id"],
                    "value": lead["price"] or lead["budget"] or 0,
                    "deal_type": lead["listing_type"] or "Sale",
                }

    return render_template("deals/form.html", d=d, agents=agents, props=props,
                           leads=leads, deal_status=DEAL_STATUS,
                           listing_types=LISTING_TYPES, prefill=prefill,
                           default_pct=get_setting("commission_pct", "2.5"),
                           today=local_now().strftime("%Y-%m-%dT%H:%M"))


@bp.route("/<int:did>/collect", methods=("POST",))
@login_required
def collect(did):
    execute("UPDATE deals SET status='Collected', updated_at=? WHERE id=?", (now(), did))
    log(g.user["id"], "Marked commission collected", "deal", did)
    flash("Commission marked as collected.", "ok")
    return redirect(url_for("deals.index"))


@bp.route("/<int:did>/delete", methods=("POST",))
@admin_required
def delete(did):
    execute("DELETE FROM deals WHERE id = ?", (did,))
    log(g.user["id"], "Deleted deal", "deal", did)
    flash("Deal deleted.", "ok")
    return redirect(url_for("deals.index"))
