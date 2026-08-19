"""Closed deals and the commission they earn."""
import json

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   url_for)

from auth import admin_required, can_edit, login_required, sees_all, sees_finance
from commission import (AGENT_ROLES, BASES, ON_CHOICES, check_shares,
                        commission_amount, split_amounts)
from db import (DEAL_STATUS, LISTING_TYPES, execute, get_setting, local_now,
                log, next_ref, notify, now, paginate, query, to_utc)

bp = Blueprint("deals", __name__, url_prefix="/deals")


def _scope(prefix="d"):
    """Agents see deals they are on, not only ones they lead. Accountants
    see every deal too — they hold no share of any of them, so without this
    they'd see none at all, defeating the point of the role."""
    if sees_finance():
        return "", []
    return (f" AND ({prefix}.agent_id = ? OR EXISTS (SELECT 1 FROM deal_agents da"
            f" WHERE da.deal_id = {prefix}.id AND da.user_id = ?))",
            [g.user["id"], g.user["id"]])


def _people(did):
    return query("SELECT da.*, u.name FROM deal_agents da"
                 " JOIN users u ON u.id = da.user_id"
                 " WHERE da.deal_id = ? ORDER BY da.share_pct DESC", (did,))


@bp.route("/")
@login_required
def index():
    where, args = _scope()
    status = request.args.get("status", "")
    if status:
        where += " AND d.status = ?"
        args.append(status)

    base = ("SELECT d.*, p.title AS prop_title, p.ref AS prop_ref,"
            " p.price AS asking_price,"
            " l.full_name AS lead_name, u.name AS agent_name,"
            " (SELECT GROUP_CONCAT(u2.name, ', ') FROM deal_agents da"
            "    JOIN users u2 ON u2.id = da.user_id"
            "    WHERE da.deal_id = d.id) AS agent_names,"
            " (SELECT COUNT(*) FROM deal_agents da WHERE da.deal_id = d.id)"
            "    AS agent_count"
            " FROM deals d"
            " LEFT JOIN properties p ON p.id = d.property_id"
            " LEFT JOIN leads l ON l.id = d.lead_id"
            " LEFT JOIN users u ON u.id = d.agent_id"
            " WHERE 1=1" + where)
    pager = paginate(base + " ORDER BY d.id DESC", args,
                     request.args.get("page", 1))
    rows = pager["rows"]

    # totals cover every matching deal, not just the page on screen
    sums = query("SELECT COALESCE(SUM(d.value),0) v,"
                 " COALESCE(SUM(d.commission_amt),0) e,"
                 " COALESCE(SUM(CASE WHEN d.status='Collected'"
                 "   THEN d.commission_amt ELSE 0 END),0) c"
                 " FROM deals d WHERE d.status != 'Cancelled'" + where,
                 args, one=True)
    totals = {"value": sums["v"], "earned": sums["e"], "collected": sums["c"]}
    totals["pending"] = totals["earned"] - totals["collected"]

    # What this person personally earned, when they aren't seeing everything.
    mine = None
    if not sees_finance():
        row = query("SELECT COALESCE(SUM(da.amount),0) a,"
                    " COALESCE(SUM(CASE WHEN d.status='Collected'"
                    "   THEN da.amount ELSE 0 END),0) c"
                    " FROM deal_agents da JOIN deals d ON d.id = da.deal_id"
                    " WHERE da.user_id = ? AND d.status != 'Cancelled'",
                    (g.user["id"],), one=True)
        mine = {"earned": row["a"], "collected": row["c"]}

    return render_template("deals/index.html", rows=rows, totals=totals,
                           mine=mine, status=status, deal_status=DEAL_STATUS,
                           pager=pager,
                           args={"status": status} if status else {})


@bp.route("/new", methods=("GET", "POST"))
@bp.route("/<int:did>/edit", methods=("GET", "POST"))
@login_required
def form(did=None):
    if g.user["role"] == "accountant":
        # Accountants follow deals and record commission payouts, but
        # recording/changing the deal itself (value, split, property) is an
        # agent/manager job — see finance.py and views_finance.py for theirs.
        flash("Accountants record commission payouts from the Financial "
              "section, not deal details.", "error")
        return redirect(url_for("finance.index"))

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
        term = int(float(f.get("term_months") or 12))
        free = float(f.get("free_months") or 0)
        basis = f.get("commission_basis", "monthly_rent")
        if basis not in BASES:
            basis = "monthly_rent"
        on = f.get("commission_on", "contract")
        if on not in ON_CHOICES:
            on = "contract"

        # A typed amount always wins; otherwise it's worked out from the rate.
        amt_raw = f.get("commission_amt", "").strip()
        amt = (float(amt_raw) if amt_raw
               else commission_amount(value, basis, pct, term, free, on))

        # Who worked on it, and for what share.
        user_ids = f.getlist("agent_user_id")
        roles = f.getlist("agent_role")
        shares = f.getlist("agent_share")
        people = [(int(u), (roles[i] if i < len(roles) and roles[i] in AGENT_ROLES
                            else "lead"),
                   float(shares[i] or 0) if i < len(shares) else 0)
                  for i, u in enumerate(user_ids) if u]

        ok, msg = check_shares([p[2] for p in people])
        if not ok:
            flash(msg, "error")
            return redirect(url_for("deals.form", did=did) if did
                            else url_for("deals.form"))

        lead_agent = max(people, key=lambda p: p[2])[0]

        vals = (int(f["property_id"]) if f.get("property_id") else None,
                int(f["lead_id"]) if f.get("lead_id") else None,
                lead_agent, f.get("deal_type", "Sale"), value, pct, amt,
                f.get("status", "Agreed"), to_utc(f.get("closed_at")),
                f.get("notes", "").strip(), term, free, basis, on)

        if d is None:
            ref = next_ref("PRE-D", "deals")
            did = execute(
                "INSERT INTO deals (property_id,lead_id,agent_id,deal_type,value,"
                "commission_pct,commission_amt,status,closed_at,notes,"
                "term_months,free_months,commission_basis,commission_on,"
                "ref,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                vals + (ref, now(), now()))
            log(g.user["id"], "Recorded deal", "deal", did,
                f"{ref} — {value:,.0f}, commission {amt:,.0f}")
            flash("Deal recorded.", "ok")
        else:
            execute("UPDATE deals SET property_id=?,lead_id=?,agent_id=?,"
                    "deal_type=?,value=?,commission_pct=?,commission_amt=?,"
                    "status=?,closed_at=?,notes=?,term_months=?,free_months=?,"
                    "commission_basis=?,commission_on=?,updated_at=? WHERE id=?",
                    vals + (now(), did))
            detail = (f"status {d['status']} → {vals[7]}"
                      if d["status"] != vals[7] else "details edited")
            log(g.user["id"], "Updated deal", "deal", did, detail)
            ref = d["ref"]
            flash("Deal saved.", "ok")

        # Rewrite the split, and tell anyone newly added.
        before = {r["user_id"] for r in _people(did)}
        execute("DELETE FROM deal_agents WHERE deal_id = ?", (did,))
        amounts = split_amounts(amt, [p[2] for p in people])
        for (uid, role, share), earns in zip(people, amounts):
            execute("INSERT INTO deal_agents (deal_id,user_id,role,share_pct,"
                    "amount) VALUES (?,?,?,?,?)", (did, uid, role, share, earns))
            if uid != g.user["id"] and uid not in before:
                notify(uid, f"You were added to deal {ref} ({share:g}%)",
                       url_for("deals.index"))
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

    existing = [{"user_id": r["user_id"], "role": r["role"],
                 "share_pct": r["share_pct"]} for r in _people(did)] if did else []

    return render_template(
        "deals/form.html", d=d, agents=agents, props=props, leads=leads,
        deal_status=DEAL_STATUS, listing_types=LISTING_TYPES, prefill=prefill,
        default_pct=get_setting("commission_pct", "2.5"),
        default_rent_pct=get_setting("commission_pct_rent", "50"),
        agents_json=json.dumps([{"id": a["id"], "name": a["name"]} for a in agents]),
        deal_agents_json=json.dumps(existing),
        today=local_now().strftime("%Y-%m-%dT%H:%M"))


@bp.route("/<int:did>/collect", methods=("POST",))
@login_required
def collect(did):
    execute("UPDATE deals SET status='Collected', updated_at=? WHERE id=?",
            (now(), did))
    log(g.user["id"], "Marked commission collected", "deal", did)
    flash("Commission marked as collected.", "ok")
    return redirect(url_for("deals.index"))


@bp.route("/<int:did>/delete", methods=("POST",))
@admin_required
def delete(did):
    execute("DELETE FROM deal_agents WHERE deal_id = ?", (did,))
    execute("DELETE FROM deals WHERE id = ?", (did,))
    log(g.user["id"], "Deleted deal", "deal", did)
    flash("Deal deleted.", "ok")
    return redirect(url_for("deals.index"))
