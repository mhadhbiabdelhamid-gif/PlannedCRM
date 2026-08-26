"""Agent reports: day / week / month / year drill-down into one person's
tasks, day-to-day work and deals.

Complements performance.py, which gives the whole team's month/quarter/year
figures on one screen. This module answers a narrower question for one
agent at a time — "what did they actually do in this specific period?" —
at any granularity down to a single day, built entirely from records the
team already creates (deals, leads, follow-ups, viewings, comments) so
nothing has to be logged twice.

The audit trail is intentionally left out. It is a separate, admin-only
screen answering a different question, and putting its counts on a person's
report made the page read as surveillance rather than as a record of the
clients they hold and the deals they closed. The one exception is inside a
client's own timeline, where a stage change is part of that client's story.
"""
from datetime import date, datetime, timedelta

from db import LOST_REASON_LABELS, local_now, now, query, utc_day_bounds
from i18n import t

PERIOD_TYPES = [
    ("day", "Day"),
    ("week", "Week"),
    ("month", "Month"),
    ("year", "Year"),
]

# strftime's %a/%b/%B come out in whatever locale the server process happens
# to have (usually English, regardless of the CRM's own language setting), so
# the period label is built from these fixed English tokens and translated
# through t() instead — that way an Arabic export gets an Arabic month name,
# not just an Arabic screen wrapped around an English one.
_MONTHS_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTHS_FULL = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
_WEEKDAYS_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_ref(ref):
    """A local calendar date to build the period around. Defaults to today
    when the value is missing or unparseable, so a bad querystring can never
    500 the page."""
    try:
        return datetime.strptime(ref, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return local_now().date()


def period_range(period_type, ref):
    """Local start (inclusive) / end (exclusive) calendar dates for one period."""
    d = _parse_ref(ref)
    if period_type == "day":
        start = d
        end = start + timedelta(days=1)
    elif period_type == "week":
        start = d - timedelta(days=d.weekday())      # Monday
        end = start + timedelta(days=7)
    elif period_type == "year":
        start = date(d.year, 1, 1)
        end = date(d.year + 1, 1, 1)
    else:
        period_type = "month"
        start = date(d.year, d.month, 1)
        end = (date(d.year, d.month + 1, 1) if d.month < 12
               else date(d.year + 1, 1, 1))
    return start, end


def period_label(period_type, start, end):
    if period_type == "day":
        return (f"{t(_WEEKDAYS_ABBR[start.weekday()])}, "
                f"{start.day:02d} {t(_MONTHS_ABBR[start.month - 1])} {start.year}")
    if period_type == "week":
        last = end - timedelta(days=1)
        if start.month == last.month:
            return f"{start.day}–{last.day} {t(_MONTHS_ABBR[start.month - 1])} {start.year}"
        return (f"{start.day:02d} {t(_MONTHS_ABBR[start.month - 1])} – "
                f"{last.day:02d} {t(_MONTHS_ABBR[last.month - 1])} {last.year}")
    if period_type == "year":
        return str(start.year)
    return f"{t(_MONTHS_FULL[start.month - 1])} {start.year}"


def period_nav(period_type, ref):
    """Everything a template needs to show one period and step to the next.

    prev/next are just any date that falls inside the neighbouring period —
    the day before the period starts, and the (exclusive) day it ends on —
    which works uniformly whether the period is a day, a week, a calendar
    month or a calendar year.
    """
    start, end = period_range(period_type, ref)
    today = local_now().date()
    u_start, _ = utc_day_bounds(start.isoformat())
    u_end, _ = utc_day_bounds(end.isoformat())
    return {
        "type": period_type,
        "start": start,
        "end": end,
        "label": period_label(period_type, start, end),
        "prev": (start - timedelta(days=1)).isoformat(),
        "next": end.isoformat(),
        "is_current": start <= today < end,
        "utc_start": u_start,
        "utc_end": u_end,
    }


def _n(sql, args):
    return query(sql, args, one=True)["n"]


def agent_report(user_id, period_type, ref):
    """One agent's tasks, work and deals for one period."""
    period = period_nav(period_type, ref)
    u_start, u_end = period["utc_start"], period["utc_end"]

    # --------------------------------------------------------------- deals
    # Closed in this period — the figure the business cares about. "Opened"
    # (created here, whenever it closes) is kept alongside it so a period
    # with nothing collected yet doesn't look like an empty one.
    deal_rows = query(
        "SELECT d.*, p.title AS prop_title, l.full_name AS lead_name FROM deals d"
        " LEFT JOIN properties p ON p.id = d.property_id"
        " LEFT JOIN leads l ON l.id = d.lead_id"
        " WHERE d.agent_id = ? AND d.status != 'Cancelled'"
        "   AND d.closed_at >= ? AND d.closed_at < ?"
        " ORDER BY d.closed_at DESC", (user_id, u_start, u_end))
    totals = query(
        "SELECT COUNT(*) n, COALESCE(SUM(d.value),0) value,"
        " COALESCE(SUM(d.commission_amt),0) commission FROM deals d"
        " WHERE d.agent_id = ? AND d.status != 'Cancelled'"
        "   AND d.closed_at >= ? AND d.closed_at < ?",
        (user_id, u_start, u_end), one=True)
    collected = query(
        "SELECT COALESCE(SUM(d.commission_amt),0) c FROM deals d"
        " WHERE d.agent_id = ? AND d.status = 'Collected'"
        "   AND d.closed_at >= ? AND d.closed_at < ?",
        (user_id, u_start, u_end), one=True)["c"]

    deals = {
        "rows": deal_rows,
        "count": totals["n"],
        "value": totals["value"] or 0,
        "commission": totals["commission"] or 0,
        "collected": collected or 0,
        "opened": _n(
            "SELECT COUNT(*) n FROM deals WHERE agent_id = ?"
            " AND created_at >= ? AND created_at < ?", (user_id, u_start, u_end)),
    }

    # --------------------------------------------------------------- tasks
    # "Due" is a fact recorded at the time (the follow-up date on the lead),
    # so it stays true for past periods. "Handled" is a proxy for completion:
    # a contact this agent logged on that same lead inside the period.
    tasks = {
        "due_followups": _n(
            "SELECT COUNT(*) n FROM leads WHERE agent_id = ?"
            " AND next_follow_up >= ? AND next_follow_up < ?",
            (user_id, u_start, u_end)),
        "handled_followups": _n(
            "SELECT COUNT(DISTINCT l.id) n FROM leads l"
            " WHERE l.agent_id = ? AND l.next_follow_up >= ? AND l.next_follow_up < ?"
            "   AND EXISTS (SELECT 1 FROM comments c WHERE c.entity_type = 'lead'"
            "     AND c.entity_id = l.id AND c.user_id = ?"
            "     AND c.created_at >= ? AND c.created_at < ?)",
            (user_id, u_start, u_end, user_id, u_start, u_end)),
        "viewings_scheduled": _n(
            "SELECT COUNT(*) n FROM viewings WHERE agent_id = ?"
            " AND scheduled_at >= ? AND scheduled_at < ?", (user_id, u_start, u_end)),
        "viewings_done": _n(
            "SELECT COUNT(*) n FROM viewings WHERE agent_id = ? AND done = 1"
            " AND scheduled_at >= ? AND scheduled_at < ?", (user_id, u_start, u_end)),
    }
    if period["is_current"]:
        # A live backlog snapshot only makes sense for the period covering
        # today — leads.status is the *current* state, not what it was back
        # when a past period ended, so showing this for history would lie.
        tasks["overdue_now"] = _n(
            "SELECT COUNT(*) n FROM leads WHERE agent_id = ?"
            " AND next_follow_up IS NOT NULL AND next_follow_up < ?"
            " AND status NOT IN ('Won','Lost')", (user_id, now()))

    # ---------------------------------------------------------------- work
    work = {
        "new_leads": _n(
            "SELECT COUNT(*) n FROM leads WHERE agent_id = ?"
            " AND created_at >= ? AND created_at < ?", (user_id, u_start, u_end)),
        "contacts_logged": _n(
            "SELECT COUNT(*) n FROM comments WHERE user_id = ? AND entity_type = 'lead'"
            " AND created_at >= ? AND created_at < ?", (user_id, u_start, u_end)),
        "new_listings": _n(
            "SELECT COUNT(*) n FROM properties WHERE agent_id = ?"
            " AND created_at >= ? AND created_at < ?", (user_id, u_start, u_end)),
        "won": _n(
            "SELECT COUNT(*) n FROM leads WHERE agent_id = ? AND status = 'Won'"
            " AND updated_at >= ? AND updated_at < ?", (user_id, u_start, u_end)),
        "lost": _n(
            "SELECT COUNT(*) n FROM leads WHERE agent_id = ? AND status = 'Lost'"
            " AND updated_at >= ? AND updated_at < ?", (user_id, u_start, u_end)),
    }
    # The audit trail is deliberately not part of this report. It answers a
    # different question ("who touched what"), it lives at /activity for
    # admins only, and counting it here made the report look like a
    # surveillance sheet rather than a record of clients and deals.

    # --------------------------------------------------------------- lost
    # Every client closed as Lost in this period, with the reason the agent
    # had to give. lost_at is only set from the day the reason became
    # compulsory, so older losses fall back to updated_at rather than
    # disappearing from history.
    lost_rows = query(
        "SELECT l.*, p.title AS prop_title, p.ref AS prop_ref FROM leads l"
        " LEFT JOIN properties p ON p.id = l.property_id"
        " WHERE l.agent_id = ? AND l.status = 'Lost'"
        "   AND COALESCE(l.lost_at, l.updated_at) >= ?"
        "   AND COALESCE(l.lost_at, l.updated_at) < ?"
        " ORDER BY COALESCE(l.lost_at, l.updated_at) DESC",
        (user_id, u_start, u_end))

    by_reason = {}
    for row in lost_rows:
        label = LOST_REASON_LABELS.get(row["lost_reason"])
        if label:
            by_reason[label] = by_reason.get(label, 0) + 1

    lost = {
        "rows": [{
            "lead": row,
            "label": LOST_REASON_LABELS.get(row["lost_reason"]),
            "at": row["lost_at"] or row["updated_at"],
        } for row in lost_rows],
        "count": len(lost_rows),
        # commonest cause first, so the pattern shows before the detail
        "by_reason": sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0])),
        "unexplained": sum(1 for r in lost_rows if not r["lost_reason"]),
    }

    return {"period": period, "deals": deals, "tasks": tasks, "work": work,
            "lost": lost}


# Activity actions that represent a change to a lead's own record (creation,
# a pipeline move, a field edit) as opposed to a contact being logged — those
# already show up as a "note" timeline entry via `comments`, so including
# them here too would show the same call or message twice.
_LEAD_STAGE_ACTIONS = ("Captured lead", "Moved lead", "Updated lead")


def _lead_timelines(lead_ids):
    """Notes, stage moves and deal records for a set of leads, merged into one
    chronological (newest-first) timeline per lead.

    Built entirely from records the team already creates elsewhere —
    `comments` (every call/message/note logged on the lead), `activity`
    (pipeline moves) and `deals` (what was actually closed) — so the employee
    report needs nothing logged twice. Queried in three batched IN(...)
    calls rather than once per lead, so a roster of a hundred clients still
    costs three queries, not hundreds.
    """
    if not lead_ids:
        return {}
    placeholders = ",".join("?" * len(lead_ids))
    timeline = {lid: [] for lid in lead_ids}

    for c in query(
        f"SELECT c.*, u.name AS user_name FROM comments c"
        f" LEFT JOIN users u ON u.id = c.user_id"
        f" WHERE c.entity_type = 'lead' AND c.entity_id IN ({placeholders})",
        lead_ids):
        timeline[c["entity_id"]].append({
            "kind": "note", "at": c["created_at"],
            "who": c["user_name"], "text": c["body"],
        })

    for a in query(
        f"SELECT * FROM activity WHERE entity_type = 'lead'"
        f"   AND entity_id IN ({placeholders})"
        f"   AND action IN ({','.join('?' * len(_LEAD_STAGE_ACTIONS))})",
        lead_ids + list(_LEAD_STAGE_ACTIONS)):
        timeline[a["entity_id"]].append({
            "kind": "stage", "at": a["created_at"],
            "text": t(a["action"]), "detail": a["detail"],
        })

    for d in query(
        f"SELECT * FROM deals WHERE lead_id IN ({placeholders})", lead_ids):
        timeline[d["lead_id"]].append({
            "kind": "deal", "at": d["created_at"], "ref": d["ref"],
            "id": d["id"], "value": d["value"], "status": d["status"],
            "text": f"{t(d['deal_type'])} · {t(d['status'])}",
        })

    for items in timeline.values():
        items.sort(key=lambda item: item["at"] or "", reverse=True)
    return timeline


def agent_overview(user_id):
    """All-time roster for one agent, independent of any period filter:
    total clients and properties ever assigned to them, and every one of
    their leads with its linked property, current pipeline stage, and a
    merged notes + stage-change + deal history for the employee report page.
    """
    totals = {
        "leads": _n("SELECT COUNT(*) n FROM leads WHERE agent_id = ?", (user_id,)),
        "properties": _n(
            "SELECT COUNT(*) n FROM properties WHERE agent_id = ?", (user_id,)),
    }

    lead_rows = query(
        "SELECT l.*, p.title AS prop_title, p.ref AS prop_ref FROM leads l"
        " LEFT JOIN properties p ON p.id = l.property_id"
        " WHERE l.agent_id = ? ORDER BY l.updated_at DESC", (user_id,))

    timelines = _lead_timelines([l["id"] for l in lead_rows])
    leads = [{"lead": l, "timeline": timelines.get(l["id"], [])} for l in lead_rows]

    return {"totals": totals, "leads": leads}
