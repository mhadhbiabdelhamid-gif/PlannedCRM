"""Agent reports: day / week / month / year drill-down into one person's
tasks, day-to-day work and deals.

Complements performance.py, which gives the whole team's month/quarter/year
figures on one screen. This module answers a narrower question for one
agent at a time — "what did they actually do in this specific period?" —
at any granularity down to a single day, built entirely from records the
team already creates (deals, leads, follow-ups, viewings, comments, the
activity log) so nothing has to be logged twice.
"""
from datetime import date, datetime, timedelta

from db import local_now, now, query, utc_day_bounds

PERIOD_TYPES = [
    ("day", "Day"),
    ("week", "Week"),
    ("month", "Month"),
    ("year", "Year"),
]


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
        return start.strftime("%a, %d %b %Y")
    if period_type == "week":
        last = end - timedelta(days=1)
        if start.month == last.month:
            return f"{start.day}–{last.day} {start.strftime('%b %Y')}"
        return f"{start.strftime('%d %b')} – {last.strftime('%d %b %Y')}"
    if period_type == "year":
        return str(start.year)
    return start.strftime("%B %Y")


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
    work["activity"] = query(
        "SELECT * FROM activity WHERE user_id = ?"
        " AND created_at >= ? AND created_at < ? ORDER BY id DESC LIMIT 30",
        (user_id, u_start, u_end))
    work["activity_total"] = _n(
        "SELECT COUNT(*) n FROM activity WHERE user_id = ?"
        " AND created_at >= ? AND created_at < ?", (user_id, u_start, u_end))

    return {"period": period, "deals": deals, "tasks": tasks, "work": work}
