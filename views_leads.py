"""Leads: drag-and-drop pipeline board, list view, detail file and viewings."""
from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from auth import can_edit, is_admin, login_required, sees_all
from datetime import timedelta

from db import (CLOSED_STAGES, LEAD_SOURCES, LEAD_STAGES, execute, local_now,
                local_today, log, next_ref, notify, now, paginate, query,
                to_utc, utc_day_bounds)

bp = Blueprint("leads", __name__, url_prefix="/leads")


def follow_up_clause(kind, prefix="l"):
    """SQL for the three states an agent actually cares about."""
    closed = " AND {p}.status NOT IN ('Won','Lost')".format(p=prefix)
    day_start, day_end = utc_day_bounds(local_today())
    if kind == "overdue":
        return (f" AND {prefix}.next_follow_up IS NOT NULL"
                f" AND {prefix}.next_follow_up < ?" + closed, [day_start])
    if kind == "today":
        return (f" AND {prefix}.next_follow_up >= ? AND {prefix}.next_follow_up < ?"
                + closed, [day_start, day_end])
    if kind == "week":
        week_end = (local_now() + timedelta(days=7)).strftime("%Y-%m-%d")
        _s, end = utc_day_bounds(week_end)
        return (f" AND {prefix}.next_follow_up >= ? AND {prefix}.next_follow_up < ?"
                + closed, [day_start, end])
    if kind == "none":
        return (f" AND ({prefix}.next_follow_up IS NULL OR"
                f" TRIM({prefix}.next_follow_up) = '')" + closed, [])
    return "", []


def _scope(prefix="l"):
    """Agents see their own leads plus the unassigned pool. Admins see all."""
    if sees_all():
        return "", []
    return f" AND ({prefix}.agent_id = ? OR {prefix}.agent_id IS NULL)", [g.user["id"]]


@bp.route("/")
@login_required
def board():
    mine = request.args.get("mine", "")
    source = request.args.get("source", "")
    where, args = _scope()
    if mine == "1":
        where += " AND l.agent_id = ?"
        args.append(g.user["id"])
    if source:
        where += " AND l.source = ?"
        args.append(source)
    due = request.args.get("due", "")
    clause, extra = follow_up_clause(due)
    where += clause
    args += extra

    rows = query("SELECT l.*, u.name AS agent_name, p.title AS prop_title"
                 " FROM leads l LEFT JOIN users u ON u.id = l.agent_id"
                 " LEFT JOIN properties p ON p.id = l.property_id"
                 " WHERE 1=1" + where + " ORDER BY l.updated_at DESC", args)
    columns = {s: [r for r in rows if r["status"] == s] for s in LEAD_STAGES}
    return render_template("leads/board.html", columns=columns, stages=LEAD_STAGES,
                           mine=mine, source=source, sources=LEAD_SOURCES,
                           total=len(rows), due=due,
                           day_start=utc_day_bounds(local_today())[0],
                           day_end=utc_day_bounds(local_today())[1])


@bp.route("/list")
@login_required
def index():
    where, args = _scope()
    q = request.args.get("q", "").strip()
    if q:
        where += " AND (l.full_name LIKE ? OR l.phone LIKE ? OR l.email LIKE ?)"
        args += [f"%{q}%"] * 3
    due = request.args.get("due", "")
    clause, extra = follow_up_clause(due)
    where += clause
    args += extra

    order = ("l.next_follow_up IS NULL, l.next_follow_up" if due
             else "l.id DESC")
    pager = paginate("SELECT l.*, u.name AS agent_name, p.title AS prop_title"
                     " FROM leads l LEFT JOIN users u ON u.id = l.agent_id"
                     " LEFT JOIN properties p ON p.id = l.property_id"
                     " WHERE 1=1" + where + " ORDER BY " + order,
                     args, request.args.get("page", 1))
    rows = pager["rows"]

    counts = {}
    for kind in ("overdue", "today", "week", "none"):
        c, a = follow_up_clause(kind)
        base, base_args = _scope()
        counts[kind] = query("SELECT COUNT(*) n FROM leads l WHERE 1=1"
                             + base + c, base_args + a, one=True)["n"]

    return render_template("leads/index.html", rows=rows, q=q, due=due,
                           counts=counts, today=local_today(), pager=pager,
                           args={k: v for k, v in (("q", q), ("due", due)) if v})


@bp.route("/<int:lid>")
@login_required
def detail(lid):
    l = query("SELECT l.*, u.name AS agent_name, p.title AS prop_title"
              " FROM leads l LEFT JOIN users u ON u.id = l.agent_id"
              " LEFT JOIN properties p ON p.id = l.property_id"
              " WHERE l.id = ?", (lid,), one=True)
    if l is None:
        flash("That lead no longer exists.", "error")
        return redirect(url_for("leads.board"))
    comments = query("SELECT c.*, u.name AS user_name, u.photo AS user_photo FROM comments c"
                     " LEFT JOIN users u ON u.id = c.user_id"
                     " WHERE c.entity_type='lead' AND c.entity_id = ?"
                     " ORDER BY c.id DESC", (lid,))
    trail = query("SELECT a.*, u.name AS user_name FROM activity a"
                  " LEFT JOIN users u ON u.id = a.user_id"
                  " WHERE a.entity_type='lead' AND a.entity_id = ?"
                  " ORDER BY a.id DESC LIMIT 50", (lid,))
    viewings = query("SELECT v.*, p.title AS prop_title FROM viewings v"
                     " LEFT JOIN properties p ON p.id = v.property_id"
                     " WHERE v.lead_id = ? ORDER BY v.scheduled_at DESC", (lid,))
    props = query("SELECT id, title, ref FROM properties ORDER BY id DESC LIMIT 200")
    day_start, day_end = utc_day_bounds(local_today())
    nf = l["next_follow_up"]
    return render_template("leads/detail.html", l=l, comments=comments, trail=trail,
                           viewings=viewings, props=props, stages=LEAD_STAGES,
                           editable=can_edit(l),
                           overdue=bool(nf and nf < day_start),
                           due_today=bool(nf and day_start <= nf < day_end))


@bp.route("/new", methods=("GET", "POST"))
@bp.route("/<int:lid>/edit", methods=("GET", "POST"))
@login_required
def form(lid=None):
    l = query("SELECT * FROM leads WHERE id = ?", (lid,), one=True) if lid else None
    if lid and l is None:
        flash("That lead no longer exists.", "error")
        return redirect(url_for("leads.board"))
    if l is not None and not can_edit(l):
        flash("That lead belongs to another agent.", "error")
        return redirect(url_for("leads.board"))

    if request.method == "POST":
        d = request.form
        vals = (d.get("full_name", "").strip(), d.get("email", "").strip(),
                d.get("phone", "").strip(), d.get("source"), d.get("status"),
                float(d.get("budget") or 0) or None, d.get("notes", "").strip(),
                int(d["agent_id"]) if d.get("agent_id") else None,
                int(d["property_id"]) if d.get("property_id") else None,
                to_utc(d.get("next_follow_up", "")) or None)
        if not vals[0]:
            flash("A lead needs a name.", "error")
            return redirect(request.url)

        if l is None:
            ref = next_ref("PRE-L", "leads")
            lid = execute("INSERT INTO leads (full_name,email,phone,source,status,budget,"
                          "notes,agent_id,property_id,next_follow_up,ref,created_at,"
                          "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          vals + (ref, now(), now()))
            log(g.user["id"], "Captured lead", "lead", lid, f"{ref} — {vals[0]} via {vals[3]}")
            if vals[7] and vals[7] != g.user["id"]:
                notify(vals[7], f"New lead assigned to you: {vals[0]}",
                       url_for("leads.detail", lid=lid))
            flash("Lead captured.", "ok")
        else:
            changes = []
            if l["status"] != vals[4]:
                changes.append(f"stage {l['status']} → {vals[4]}")
            if l["agent_id"] != vals[7]:
                changes.append("agent reassigned")
            execute("UPDATE leads SET full_name=?,email=?,phone=?,source=?,status=?,"
                    "budget=?,notes=?,agent_id=?,property_id=?,next_follow_up=?,"
                    "updated_at=? WHERE id=?", vals + (now(), lid))
            log(g.user["id"], "Updated lead", "lead", lid,
                "; ".join(changes) or "details edited")
            if vals[7] and vals[7] != l["agent_id"] and vals[7] != g.user["id"]:
                notify(vals[7], f"Lead assigned to you: {vals[0]}",
                       url_for("leads.detail", lid=lid))
            flash("Lead saved.", "ok")
        return redirect(url_for("leads.detail", lid=lid))

    agents = query("SELECT id, name FROM users WHERE is_active = 1 ORDER BY name")
    props = query("SELECT id, title, ref FROM properties ORDER BY id DESC LIMIT 200")
    return render_template("leads/form.html", l=l, agents=agents, props=props,
                           stages=LEAD_STAGES, sources=LEAD_SOURCES)


@bp.route("/<int:lid>/stage", methods=("POST",))
@login_required
def move_stage(lid):
    """Called by the board's drag-and-drop, and by the stage buttons."""
    stage = (request.json or request.form).get("stage")
    if stage not in LEAD_STAGES:
        return jsonify(ok=False, error="Unknown stage"), 400
    l = query("SELECT * FROM leads WHERE id = ?", (lid,), one=True)
    if l is None:
        return jsonify(ok=False, error="Lead not found"), 404
    if not can_edit(l):
        return jsonify(ok=False, error="That lead belongs to another agent"), 403
    if l["status"] != stage:
        execute("UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
                (stage, now(), lid))
        log(g.user["id"], "Moved lead", "lead", lid, f"{l['status']} → {stage}")
        if l["agent_id"] and l["agent_id"] != g.user["id"]:
            notify(l["agent_id"], f"{l['full_name']} moved to {stage}",
                   url_for("leads.detail", lid=lid))
    if request.is_json:
        return jsonify(ok=True, stage=stage)
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/<int:lid>/followup", methods=("POST",))
@login_required
def set_followup(lid):
    """Set the next follow-up. Quick buttons send a number of days; the date
    field sends an exact moment."""
    l = query("SELECT * FROM leads WHERE id = ?", (lid,), one=True)
    if l is None or not can_edit(l):
        flash("That lead belongs to another agent.", "error")
        return redirect(url_for("leads.board"))

    days = request.form.get("in_days", "")
    exact = request.form.get("when", "").strip()

    if days == "clear":
        execute("UPDATE leads SET next_follow_up = NULL, updated_at = ? WHERE id = ?",
                (now(), lid))
        log(g.user["id"], "Cleared the follow-up", "lead", lid)
        flash("Follow-up cleared.", "ok")
        return redirect(url_for("leads.detail", lid=lid))

    if days.isdigit():
        # 09:00 local on the target day is when an agent actually starts calling
        target = (local_now() + timedelta(days=int(days))).strftime("%Y-%m-%d 09:00")
        when_utc = to_utc(target)
    elif exact:
        when_utc = to_utc(exact)
    else:
        flash("Pick when to follow up.", "error")
        return redirect(url_for("leads.detail", lid=lid))

    execute("UPDATE leads SET next_follow_up = ?, updated_at = ? WHERE id = ?",
            (when_utc, now(), lid))
    log(g.user["id"], "Scheduled a follow-up", "lead", lid, when_utc)
    if l["agent_id"] and l["agent_id"] != g.user["id"]:
        notify(l["agent_id"], f"Follow-up set for {l['full_name']}",
               url_for("leads.detail", lid=lid))
    flash("Follow-up scheduled.", "ok")
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/<int:lid>/contacted", methods=("POST",))
@login_required
def mark_contacted(lid):
    """Records a call or message and rolls the follow-up forward in one step."""
    l = query("SELECT * FROM leads WHERE id = ?", (lid,), one=True)
    if l is None or not can_edit(l):
        flash("That lead belongs to another agent.", "error")
        return redirect(url_for("leads.board"))

    note = request.form.get("body", "").strip()
    kind = request.form.get("kind", "Note")
    stamp = now()

    if note:
        execute("INSERT INTO comments (entity_type, entity_id, user_id, body, created_at)"
                " VALUES ('lead',?,?,?,?)", (lid, g.user["id"], f"{kind}: {note}", stamp))

    execute("UPDATE leads SET last_contact_at = ?, updated_at = ? WHERE id = ?",
            (stamp, stamp, lid))

    # a lead that has been spoken to is no longer simply New
    if l["status"] == "New":
        execute("UPDATE leads SET status = 'Contacted' WHERE id = ?", (lid,))

    days = request.form.get("next_in", "")
    if days.isdigit():
        target = (local_now() + timedelta(days=int(days))).strftime("%Y-%m-%d 09:00")
        execute("UPDATE leads SET next_follow_up = ? WHERE id = ?", (to_utc(target), lid))
    elif days == "done":
        execute("UPDATE leads SET next_follow_up = NULL WHERE id = ?", (lid,))

    log(g.user["id"], f"Logged a {kind.lower()}", "lead", lid, note[:110])
    flash("Contact recorded.", "ok")
    return redirect(url_for("leads.detail", lid=lid) + "#history")


@bp.route("/<int:lid>/claim", methods=("POST",))
@login_required
def claim(lid):
    execute("UPDATE leads SET agent_id = ?, updated_at = ? WHERE id = ?",
            (g.user["id"], now(), lid))
    log(g.user["id"], "Claimed lead", "lead", lid)
    flash("Lead is yours.", "ok")
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/<int:lid>/comment", methods=("POST",))
@login_required
def comment(lid):
    body = request.form.get("body", "").strip()
    if body:
        execute("INSERT INTO comments (entity_type, entity_id, user_id, body, created_at)"
                " VALUES ('lead',?,?,?,?)", (lid, g.user["id"], body, now()))
        l = query("SELECT full_name, agent_id FROM leads WHERE id = ?", (lid,), one=True)
        execute("UPDATE leads SET last_contact_at = ?, updated_at = ? WHERE id = ?",
                (now(), now(), lid))
        log(g.user["id"], "Logged interaction", "lead", lid, body[:120])
        if l and l["agent_id"] and l["agent_id"] != g.user["id"]:
            notify(l["agent_id"], f"{g.user['name']} added a note on {l['full_name']}",
                   url_for("leads.detail", lid=lid))
    return redirect(url_for("leads.detail", lid=lid) + "#history")


@bp.route("/<int:lid>/viewing", methods=("POST",))
@login_required
def add_viewing(lid):
    when = request.form.get("scheduled_at", "").strip()
    if not when:
        flash("Pick a date and time for the viewing.", "error")
        return redirect(url_for("leads.detail", lid=lid))
    l = query("SELECT * FROM leads WHERE id = ?", (lid,), one=True)
    pid = request.form.get("property_id") or l["property_id"]
    execute("INSERT INTO viewings (lead_id, property_id, agent_id, scheduled_at, notes,"
            " done, created_at) VALUES (?,?,?,?,?,0,?)",
            (lid, int(pid) if pid else None, l["agent_id"] or g.user["id"],
             to_utc(when), request.form.get("notes", "").strip(), now()))
    log(g.user["id"], "Scheduled viewing", "lead", lid, when)
    if l["status"] == "New" or l["status"] == "Qualified":
        execute("UPDATE leads SET status='Viewing', updated_at=? WHERE id=?", (now(), lid))
    flash("Viewing scheduled.", "ok")
    return redirect(url_for("leads.detail", lid=lid))


@bp.route("/viewings/<int:vid>/done", methods=("POST",))
@login_required
def viewing_done(vid):
    v = query("SELECT * FROM viewings WHERE id = ?", (vid,), one=True)
    execute("UPDATE viewings SET done = 1 WHERE id = ?", (vid,))
    log(g.user["id"], "Marked viewing complete", "lead", v["lead_id"] if v else None)
    return redirect(request.referrer or url_for("main.dashboard"))


@bp.route("/<int:lid>/delete", methods=("POST",))
@login_required
def delete(lid):
    if not is_admin():
        flash("Only admins can delete leads.", "error")
        return redirect(url_for("leads.detail", lid=lid))
    execute("DELETE FROM leads WHERE id = ?", (lid,))
    log(g.user["id"], "Deleted lead", "lead", lid)
    flash("Lead deleted.", "ok")
    return redirect(url_for("leads.board"))
