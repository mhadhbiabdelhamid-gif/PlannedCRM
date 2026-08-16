"""Agent performance figures.

Everything here is derived from records the team already creates — deals, leads,
follow-ups, listings — so nothing has to be entered twice and no figure can be
inflated by hand.
"""
from datetime import timedelta

from db import local_now, local_today, query, utc_day_bounds

RANGES = [
    ("this_month", "This month"),
    ("last_month", "Last month"),
    ("this_quarter", "This quarter"),
    ("this_year", "This year"),
    ("all", "All time"),
]


def range_bounds(key):
    """UTC start and end for a named period, plus a label for the screen."""
    today = local_now()
    if key == "this_month":
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
    elif key == "last_month":
        first = today.replace(day=1)
        end = first
        start = (first - timedelta(days=1)).replace(day=1)
    elif key == "this_quarter":
        q_first_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=q_first_month, day=1)
        end = (start + timedelta(days=100)).replace(day=1)
    elif key == "this_year":
        start = today.replace(month=1, day=1)
        end = today.replace(year=today.year + 1, month=1, day=1)
    else:
        return None, None

    return (utc_day_bounds(start.strftime("%Y-%m-%d"))[0],
            utc_day_bounds(end.strftime("%Y-%m-%d"))[0])


def _window(column, start, end, args):
    if start is None:
        return "", args
    args = args + [start, end]
    return f" AND COALESCE({column}, '') >= ? AND COALESCE({column}, '') < ?", args


def stats_for(user_id, range_key="this_month"):
    """One agent's figures for a period. Counts that describe a current state —
    active leads, live listings, overdue follow-ups — ignore the period, because
    'active listings last month' is not a useful number."""
    start, end = range_bounds(range_key)

    deal_where, deal_args = _window("d.closed_at", start, end, [user_id])
    deals = query(
        "SELECT COUNT(*) n, COALESCE(SUM(d.value),0) value,"
        " COALESCE(SUM(d.commission_amt),0) commission FROM deals d"
        " WHERE d.agent_id = ? AND d.status != 'Cancelled'" + deal_where,
        deal_args, one=True)

    collected = query(
        "SELECT COALESCE(SUM(d.commission_amt),0) c FROM deals d"
        " WHERE d.agent_id = ? AND d.status = 'Collected'" + deal_where,
        deal_args, one=True)["c"]

    lead_where, lead_args = _window("l.created_at", start, end, [user_id])
    assigned = query("SELECT COUNT(*) n FROM leads l WHERE l.agent_id = ?" + lead_where,
                     lead_args, one=True)["n"]

    won_where, won_args = _window("l.updated_at", start, end, [user_id])
    won = query("SELECT COUNT(*) n FROM leads l"
                " WHERE l.agent_id = ? AND l.status = 'Won'" + won_where,
                won_args, one=True)["n"]
    lost = query("SELECT COUNT(*) n FROM leads l"
                 " WHERE l.agent_id = ? AND l.status = 'Lost'" + won_where,
                 won_args, one=True)["n"]

    # current state, not period-bound
    active_leads = query(
        "SELECT COUNT(*) n FROM leads WHERE agent_id = ?"
        " AND status NOT IN ('Won','Lost')", (user_id,), one=True)["n"]
    listings = query(
        "SELECT COUNT(*) n FROM properties WHERE agent_id = ?"
        " AND status IN ('Available','Reserved')", (user_id,), one=True)["n"]

    day_start, day_end = utc_day_bounds(local_today())
    overdue = query(
        "SELECT COUNT(*) n FROM leads WHERE agent_id = ?"
        " AND next_follow_up IS NOT NULL AND next_follow_up < ?"
        " AND status NOT IN ('Won','Lost')", (user_id, day_start), one=True)["n"]
    due_today = query(
        "SELECT COUNT(*) n FROM leads WHERE agent_id = ?"
        " AND next_follow_up >= ? AND next_follow_up < ?"
        " AND status NOT IN ('Won','Lost')",
        (user_id, day_start, day_end), one=True)["n"]

    contacts = query(
        "SELECT COUNT(*) n FROM comments c WHERE c.user_id = ?"
        " AND c.entity_type = 'lead'" + (
            " AND c.created_at >= ? AND c.created_at < ?" if start else ""),
        ([user_id, start, end] if start else [user_id]), one=True)["n"]

    settled = won + lost
    return {
        "deals": deals["n"],
        "value": deals["value"] or 0,
        "commission": deals["commission"] or 0,
        "collected": collected or 0,
        "assigned": assigned,
        "won": won,
        "lost": lost,
        "conversion": round(won * 100.0 / settled, 1) if settled else None,
        "active_leads": active_leads,
        "listings": listings,
        "overdue": overdue,
        "due_today": due_today,
        "contacts": contacts,
    }


def team_stats(range_key="this_month"):
    """Every active member, ordered by what they closed."""
    people = query("SELECT * FROM users WHERE is_active = 1 ORDER BY name")
    rows = []
    for person in people:
        s = stats_for(person["id"], range_key)
        rows.append({"user": person, "stats": s})
    rows.sort(key=lambda r: (-r["stats"]["value"], r["user"]["name"]))
    return rows
