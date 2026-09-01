"""Leads: drag-and-drop pipeline board, list view, detail file and viewings."""
import json

from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from auth import can_edit, is_admin, login_required, sees_all
from datetime import timedelta

from db import (CLOSED_STAGES, LEAD_SOURCES, LEAD_STAGES, LOST_REASONS,
                LOST_REASON_LABELS, execute, local_now, local_today, log,
                next_ref, notify, now, paginate, query, to_local, to_utc,
                utc_day_bounds)

bp = Blueprint("leads", __name__, url_prefix="/leads")

# ---------------------------------------------------------- AI prioritiser
# Reuses ai_intake's provider-agnostic ask_model() rather than talking to an
# API directly — one place decides which provider/key is in use, and this
# stays a thin consumer of it. Never writes to the leads table: it only ever
# reorders what an agent already sees, the same "advisory, not authoritative"
# rule ai_intake.py and ai_social.py both follow.
PRIORITY_SYSTEM_PROMPT = """You help a real estate agent in Doha, Qatar decide \
who to contact today, from a list of their open leads (clients not yet won or \
lost).

You will receive a JSON array of leads, each with: id, name, stage (pipeline \
status), source, budget (QAR, may be null), property (their property of \
interest, may be null), days_since_created, days_since_contact (null if never \
contacted), follow_up ("overdue" / "today" / "upcoming" / "none"), and agent \
(who holds the lead — only meaningful if more than one name appears).

Pick up to 8 leads worth contacting today, ranked most urgent first. Weigh:
- an overdue or due-today follow-up is the strongest signal
- a lead never contacted, or not contacted in a while, especially if new
- higher budget deals are worth more attention, all else being equal
- a lead with no signal of urgency at all should not be forced onto the list \
just to fill it — fewer, well-justified picks beat a padded list

For each pick, give one short, specific reason a busy agent can read in two \
seconds, referencing the actual signal given (e.g. "Follow-up was due 3 days \
ago" or "New lead, budget 12,000 QAR, not yet contacted"). Never invent a \
detail that isn't in the data.

Return ONLY a JSON object, no preamble and no markdown fences:
{"picks": [{"id": int, "reason": string}], "note": string}

"note" is one short sentence of overall context, e.g. "3 follow-ups are \
overdue" or "Nothing urgent today — the pipeline is current." Use "" if there \
is nothing worth saying beyond the picks themselves."""


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
                           lost_reasons=LOST_REASONS,
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


@bp.route("/priority")
@login_required
def priority():
    from ai_intake import api_key_present
    return render_template("leads/priority.html", result=None,
                           key_missing=not api_key_present())


@bp.route("/priority/generate", methods=("POST",))
@login_required
def priority_generate():
    """Renders the result straight from this POST rather than storing it
    somewhere and redirecting: the picks are only ever a few KB, but a
    Flask session is a signed browser cookie with roughly a 4KB ceiling, and
    this app has no server-side session store to spill into instead. A
    reload loses the picks and needs a fresh "Generate" — a fair trade for
    not depending on a schema change or risking a silently-dropped cookie."""
    from ai_intake import api_key_present, ask_model

    where, args = _scope()
    rows = query(
        "SELECT l.*, p.title AS prop_title, u.name AS agent_name FROM leads l"
        " LEFT JOIN properties p ON p.id = l.property_id"
        " LEFT JOIN users u ON u.id = l.agent_id"
        " WHERE l.status NOT IN ('Won','Lost')" + where +
        " ORDER BY l.updated_at DESC LIMIT 120", args)
    if not rows:
        flash("No open leads to prioritise.", "info")
        return redirect(url_for("leads.priority"))

    key_missing = not api_key_present()
    today = local_now().date()

    def _days_since(value):
        dt = to_local(value)
        return (today - dt.date()).days if dt else None

    payload = []
    for l in rows:
        follow_up = to_local(l["next_follow_up"])
        fu_state = "none"
        if follow_up:
            fu_state = ("overdue" if follow_up.date() < today
                        else "today" if follow_up.date() == today else "upcoming")
        payload.append({
            "id": l["id"], "name": l["full_name"], "stage": l["status"],
            "source": l["source"], "budget": l["budget"],
            "property": l["prop_title"],
            "days_since_created": _days_since(l["created_at"]),
            "days_since_contact": _days_since(l["last_contact_at"]),
            "follow_up": fu_state,
            "agent": l["agent_name"],
        })

    try:
        result = ask_model(PRIORITY_SYSTEM_PROMPT,
                           json.dumps(payload, ensure_ascii=False))
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("leads.priority"))

    by_id = {l["id"]: l for l in rows}
    picks = []
    for p in (result.get("picks") or [])[:8]:
        lead = by_id.get(p.get("id"))
        if lead is None:
            continue
        picks.append({
            "lead_id": lead["id"], "name": lead["full_name"],
            "phone": lead["phone"], "stage": lead["status"],
            "agent_name": lead["agent_name"],
            "reason": (p.get("reason") or "").strip(),
        })

    log(g.user["id"], "Generated lead priorities", detail=f"{len(picks)} picks")
    return render_template(
        "leads/priority.html", key_missing=key_missing,
        result={"picks": picks, "note": (result.get("note") or "").strip(),
               "generated_at": now(), "considered": len(rows)})


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
                           lost_reasons=LOST_REASONS,
                           lost_label=LOST_REASON_LABELS.get(
                               l["lost_reason"] if "lost_reason" in l.keys() else None),
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

        # Lost is a workflow decision that has to carry a reason, and this form
        # has nowhere to ask for one — so it is not a way in. Anyone already
        # marked lost keeps that status when their details are edited.
        if vals[4] == "Lost" and (l is None or l["status"] != "Lost"):
            flash("To mark a client lost, open their page and use the Lost "
                  "button — it asks for the reason.", "error")
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
    # Both a JSON fetch (the board) and an ordinary form post (the stage chips
    # and the Lost dialog) arrive here. `request.json` cannot be used to cover
    # both: on a form post current Werkzeug *raises* 415 rather than returning
    # None, which is what made every stage chip fail with "Unsupported Media
    # Type". silent=True is what actually gives the fall-through this needs.
    data = request.get_json(silent=True) or request.form
    stage = data.get("stage")
    if stage not in LEAD_STAGES:
        return jsonify(ok=False, error="Unknown stage"), 400
    l = query("SELECT * FROM leads WHERE id = ?", (lid,), one=True)
    if l is None:
        return jsonify(ok=False, error="Lead not found"), 404
    if not can_edit(l):
        return jsonify(ok=False, error="That lead belongs to another agent"), 403
    # Losing a client is the one move that has to be explained. Without a
    # reason and a note the record says only that someone gave up, which is
    # exactly the information the office needs and never has.
    reason = note = None
    if stage == "Lost":
        reason = (data.get("lost_reason") or "").strip()
        note = (data.get("lost_note") or "").strip()
        problem = None
        if reason not in LOST_REASON_LABELS:
            problem = "Choose a reason before marking this client lost."
        elif len(note) < 10:
            problem = ("Write a line about what happened — at least a few "
                       "words, so the reason is useful later.")
        if problem:
            if request.is_json:
                return jsonify(ok=False, error=problem), 400
            flash(problem, "error")
            return redirect(url_for("leads.detail", lid=lid))

    if l["status"] != stage:
        if stage == "Lost":
            execute("UPDATE leads SET status = ?, lost_reason = ?, lost_note = ?,"
                    " lost_at = ?, updated_at = ? WHERE id = ?",
                    (stage, reason, note, now(), now(), lid))
            # Also written into the client's own timeline, so someone reading
            # the file sees the ending in place rather than only in a report.
            execute("INSERT INTO comments (entity_type, entity_id, user_id, body,"
                    " created_at) VALUES ('lead',?,?,?,?)",
                    (lid, g.user["id"],
                     f"{LOST_REASON_LABELS[reason]} — {note}", now()))
            log(g.user["id"], "Moved lead", "lead", lid,
                f"{l['status']} → Lost ({LOST_REASON_LABELS[reason]})")
        else:
            # Coming back out of Lost: the old reason no longer describes this
            # client, and leaving it behind would show a stale cause on a live
            # lead the next time they are lost.
            execute("UPDATE leads SET status = ?, lost_reason = NULL,"
                    " lost_note = NULL, lost_at = NULL, updated_at = ?"
                    " WHERE id = ?", (stage, now(), lid))
            log(g.user["id"], "Moved lead", "lead", lid, f"{l['status']} → {stage}")
        if l["agent_id"] and l["agent_id"] != g.user["id"]:
            notify(l["agent_id"], f"{l['full_name']} moved to {stage}",
                   url_for("leads.detail", lid=lid))

    if request.is_json:
        return jsonify(ok=True, stage=stage)
    # The board sends people back to the board; everywhere else lands on the
    # client's own page.
    nxt = request.form.get("next", "")
    if nxt.startswith("/"):
        return redirect(nxt)
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


@bp.route("/<int:lid>/email", methods=("POST",))
@login_required
def send_email(lid):
    import mailer
    l = query("SELECT l.*, p.title AS prop_title, p.price, p.building_no, p.unit_no,"
              " p.area FROM leads l LEFT JOIN properties p ON p.id = l.property_id"
              " WHERE l.id = ?", (lid,), one=True)
    if l is None or not can_edit(l):
        flash("That lead belongs to another agent.", "error")
        return redirect(url_for("leads.board"))

    to = request.form.get("to", "").strip() or (l["email"] or "")
    subject = request.form.get("subject", "").strip()
    body = request.form.get("body", "").strip()
    if not body:
        flash("Write something before sending.", "error")
        return redirect(url_for("leads.detail", lid=lid))

    ok, message = mailer.send(to, subject, body,
                              from_name=g.user["name"], reply_to=g.user["email"])
    if ok:
        stamp = now()
        execute("INSERT INTO comments (entity_type, entity_id, user_id, body, created_at)"
                " VALUES ('lead',?,?,?,?)",
                (lid, g.user["id"], f"Email — {subject}\n\n{body}", stamp))
        execute("UPDATE leads SET last_contact_at = ?, updated_at = ? WHERE id = ?",
                (stamp, stamp, lid))
        if l["status"] == "New":
            execute("UPDATE leads SET status = 'Contacted' WHERE id = ?", (lid,))
        log(g.user["id"], "Sent an email", "lead", lid, f"{to}: {subject}")
    flash(message, "ok" if ok else "error")
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
