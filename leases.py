"""Tenancies that are about to end, and the reminders that go with them.

Scoped to the company's own properties (properties.is_own), because those are
the ones Planned Real Estate lets out itself and therefore has to re-let. A
lease in a partner's building is the partner's problem.

A rental deal already records how long the lease runs; what the office never
had was anything watching the clock. This module answers one question — which
of our units are coming free — and makes sure somebody is told before it
happens rather than after.

Nothing here changes a property's status on its own. The reminder exists so a
person decides, which is the whole point: only they know whether the tenant is
renewing, moving out, or already gone.
"""
from datetime import date, datetime, timedelta

from db import (LEASE_NOTICE_DAYS, execute, local_today, log, notify, query)

# Which reminder has already been sent for a lease. 'done' means someone has
# acted on it and it should stop appearing anywhere.
ALERT_SOON = "soon"
ALERT_ENDED = "ended"
ALERT_DONE = "done"


def _d(value):
    """A stored YYYY-MM-DD into a date, tolerating a full timestamp."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def lease_end_for(term_months, start):
    """The day a tenancy runs out, given its start and length in months.

    Done with SQLite's own date arithmetic elsewhere; this is the Python side
    for the deal form. Month arithmetic clamps to the end of a short month, so
    a lease starting 31 January for one month ends 28 February rather than
    rolling into March.
    """
    d = _d(start)
    if d is None:
        return None
    months = int(float(term_months or 12))
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    last = [31, 29 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, last)).isoformat()


BASE_SQL = """
SELECT d.*, p.title AS prop_title, p.ref AS prop_ref, p.status AS prop_status,
       p.area AS prop_area, p.agent_id AS prop_agent_id,
       l.full_name AS tenant_name, l.phone AS tenant_phone,
       u.name AS agent_name
  FROM deals d
  LEFT JOIN properties p ON p.id = d.property_id
  LEFT JOIN leads l ON l.id = d.lead_id
  LEFT JOIN users u ON u.id = d.agent_id
 WHERE lower(COALESCE(d.deal_type,'')) LIKE 'rent%'
   AND d.status != 'Cancelled'
   -- Only the company's own stock. A tenancy in a partner's building is their
   -- business to track; this exists so nobody forgets a unit WE let out.
   AND COALESCE(p.is_own, 0) = 1
   AND d.lease_end IS NOT NULL
   AND COALESCE(d.lease_alert, '') != 'done'
   -- a renewal supersedes the lease it replaces, so the old one drops out
   -- instead of nagging about a unit that is let again
   AND NOT EXISTS (
        SELECT 1 FROM deals n
         WHERE n.property_id = d.property_id
           AND n.id != d.id
           AND n.status != 'Cancelled'
           AND lower(COALESCE(n.deal_type,'')) LIKE 'rent%'
           AND n.lease_end > d.lease_end)
"""


def ending(within_days=LEASE_NOTICE_DAYS, today=None):
    """Leases already finished, or finishing inside the notice window.

    Soonest first, so whatever is already overdue sits at the top.
    """
    today = today or local_today()
    horizon = (_d(today) + timedelta(days=within_days)).isoformat()
    return query(BASE_SQL + " AND d.lease_end <= ? ORDER BY d.lease_end, d.id",
                 (horizon,))


def count_ending(within_days=LEASE_NOTICE_DAYS, today=None):
    today = today or local_today()
    horizon = (_d(today) + timedelta(days=within_days)).isoformat()
    row = query(f"SELECT COUNT(*) c FROM ({BASE_SQL} AND d.lease_end <= ?)",
                (horizon,), one=True)
    return row["c"]


def days_left(lease_end, today=None):
    """Negative once the lease has run out."""
    end, now = _d(lease_end), _d(today or local_today())
    return None if (end is None or now is None) else (end - now).days


def resolve(deal_id):
    """Stop reminding anyone about this lease."""
    execute("UPDATE deals SET lease_alert = ? WHERE id = ?", (ALERT_DONE, deal_id))


# --------------------------------------------------------------- reminders

def _recipients(row):
    """Admins, plus whoever holds the listing.

    The admin changes the status; the agent on the listing is the one who has
    to find the next tenant, so an empty unit is their problem first.
    """
    ids = {r["id"] for r in
           query("SELECT id FROM users WHERE role = 'admin' AND is_active = 1")}
    for key in ("prop_agent_id", "agent_id"):
        if row[key]:
            ids.add(row[key])
    return ids


def run_alerts(today=None):
    """Send whatever reminders are due, once each. Safe to call repeatedly.

    Each lease moves NULL -> 'soon' -> 'ended', and the column is written in
    the same breath as the notification, so a second run on the same day sends
    nothing. That matters because several server workers each run this.
    """
    today = today or local_today()
    sent = 0
    for row in ending(LEASE_NOTICE_DAYS, today):
        left = days_left(row["lease_end"], today)
        state = row["lease_alert"] or ""
        where = row["prop_ref"] or row["prop_title"] or f"deal {row['ref']}"

        if left is not None and left <= 0 and state != ALERT_ENDED:
            message = f"{where} is now free — the lease ended {row['lease_end']}"
            new_state = ALERT_ENDED
        elif left is not None and 0 < left <= LEASE_NOTICE_DAYS and not state:
            message = (f"{where} is free in {left} days — "
                       f"the lease ends {row['lease_end']}")
            new_state = ALERT_SOON
        else:
            continue

        link = f"/properties/{row['property_id']}" if row["property_id"] else None
        for uid in _recipients(row):
            notify(uid, message, link)
        execute("UPDATE deals SET lease_alert = ? WHERE id = ?",
                (new_state, row["id"]))
        sent += 1

    return sent


# --------------------------------------------------------------- scheduler

def _claim_today(today):
    """Take today's run, or report that another worker already has it.

    Render runs several gunicorn workers and each starts its own thread. A
    plain "have we run today?" check has them all answering no in the same
    instant and every admin gets the reminder three times. The claim is one
    conditional write, which SQLite serialises, so exactly one worker wins —
    the same trick backups.py uses for its off-site copy.
    """
    from db import get_db
    db = get_db()
    cur = db.execute(
        "INSERT INTO settings (key, value) VALUES ('lease_alerts_on', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        "   WHERE settings.value IS NULL OR settings.value = ''"
        "      OR settings.value < ?", (today, today))
    db.commit()
    claimed = cur.rowcount > 0
    cur.close()
    return claimed


def check_now(app):
    """One pass, inside an app context. Returns how many reminders went out."""
    import threading
    with app.app_context():
        today = local_today()
        if not _claim_today(today):
            return 0
        return run_alerts(today)


def start_scheduler(app):
    """Look for tenancies ending, a few times a day.

    Half-hourly ticks with a daily claim rather than a real cron: the machine
    may be asleep or redeploying at any given hour, and this way the check
    simply happens the next time the server is up.
    """
    import threading
    import time

    def loop():
        time.sleep(90)                       # let start-up settle
        while True:
            try:
                n = check_now(app)
                if n:
                    print(f"  Lease reminders sent: {n}")
            except Exception as exc:         # never let this kill the CRM
                print(f"  Lease check failed: {exc}")
            time.sleep(1800)

    thread = threading.Thread(target=loop, daemon=True, name="crm-leases")
    thread.start()
    return thread
