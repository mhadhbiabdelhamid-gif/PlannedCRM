"""Leads an agent doesn't move are marked Lost.

The rule: an open lead assigned to an agent (role "agent") that stays in the
same pipeline stage for STALE_LEAD_DAYS days (3) is moved to Lost with the
reason "Agent didn't follow up" and a note saying which stage it sat in and
since when. A day before, the agent gets a notification so they can act.

"Moving" means changing the lead's stage on the board, the lead page, in bulk,
or anything else that changes its status (marking it contacted, booking a
viewing...). The date is kept by a database trigger (see db.init_db), so it is
right wherever in the code the stage changes. Notes and calls alone don't
count: the lead has to move forward in the pipeline.

Leads assigned to managers or admins, unassigned leads and archived leads are
left alone. Leads that already existed when the rule was switched on got
their 3 days counted from that day.

Runs on the live server only (Render sets RENDER), like the social schedule;
LEAD_RULES=1 or 0 overrides. Each change is a conditional UPDATE, so with
several server workers only one of them makes it (and sends the note).
"""
import os
import threading
import time
from datetime import datetime, timedelta

from db import (AUTO_LOST_REASON, STALE_LEAD_DAYS, STAMP, TZ_OFFSET, execute,
                get_db, log, notify, now, query)

CHECK_MINUTES = 15
WARN_BEFORE_HOURS = 24


def enabled():
    flag = os.environ.get("LEAD_RULES", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    return bool(os.environ.get("RENDER"))


def _stamp(dt):
    return dt.strftime(STAMP)


def _local(stamp):
    try:
        return datetime.strptime(stamp[:19], STAMP) + TZ_OFFSET
    except (TypeError, ValueError):
        return None


def deadline(stage_changed_at):
    """When a lead in its stage since `stage_changed_at` (UTC) goes to Lost."""
    try:
        return datetime.strptime(stage_changed_at[:19], STAMP) + \
            timedelta(days=STALE_LEAD_DAYS)
    except (TypeError, ValueError):
        return None


_OPEN_AGENT_LEADS = (
    "SELECT l.*, u.name AS agent_name FROM leads l"
    " JOIN users u ON u.id = l.agent_id"
    " WHERE u.role = 'agent' AND l.status NOT IN ('Won','Lost')"
    " AND l.archived_at IS NULL AND l.stage_changed_at IS NOT NULL"
    " AND l.stage_changed_at <= ?")


def run_once():
    """One pass. Returns (warned, moved_to_lost)."""
    utc_now = datetime.utcnow()
    db = get_db()
    warned = moved = 0

    # 1. A day's warning.
    warn_from = _stamp(utc_now - timedelta(days=STALE_LEAD_DAYS)
                       + timedelta(hours=WARN_BEFORE_HOURS))
    cutoff = _stamp(utc_now - timedelta(days=STALE_LEAD_DAYS))
    for l in query(_OPEN_AGENT_LEADS + " AND l.stage_changed_at > ?"
                   " AND (l.stale_warned_at IS NULL"
                   " OR l.stale_warned_at < l.stage_changed_at)",
                   (warn_from, cutoff)):
        got = db.execute(
            "UPDATE leads SET stale_warned_at = ? WHERE id = ? AND status = ?"
            " AND stage_changed_at = ? AND (stale_warned_at IS NULL"
            " OR stale_warned_at < stage_changed_at)",
            (now(), l["id"], l["status"], l["stage_changed_at"])).rowcount
        db.commit()
        if got:
            when = _local(_stamp(deadline(l["stage_changed_at"])))
            notify(l["agent_id"],
                   f"{l['full_name']} has been in {l['status']} since "
                   f"{_local(l['stage_changed_at']):%d %b}. Move them forward "
                   f"before {when:%a %d %b %H:%M} or they'll be marked Lost "
                   "(no follow-up).", f"/leads/{l['id']}")
            warned += 1

    # 2. Past the limit: Lost.
    reason, label = AUTO_LOST_REASON
    lost_names = []
    for l in query(_OPEN_AGENT_LEADS, (cutoff,)):
        since = _local(l["stage_changed_at"])
        note = (f"Agent does not follow up: {l['agent_name']} left this lead in "
                f"{l['status']} from {since:%d %b %Y %H:%M} for "
                f"{STALE_LEAD_DAYS} days without moving it in the pipeline.")
        stamp = now()
        got = db.execute(
            "UPDATE leads SET status = 'Lost', lost_reason = ?, lost_note = ?,"
            " lost_at = ?, closed_at = ?, updated_at = ? WHERE id = ?"
            " AND status = ? AND stage_changed_at = ?",
            (reason, note, stamp, stamp, stamp, l["id"], l["status"],
             l["stage_changed_at"])).rowcount
        db.commit()
        if not got:
            continue                      # moved meanwhile, or another worker
        execute("INSERT INTO comments (entity_type, entity_id, user_id, body,"
                " created_at) VALUES ('lead',?,NULL,?,?)",
                (l["id"], f"{label} — {note}", stamp))
        log(None, "Moved lead", "lead", l["id"],
            f"{l['status']} → Lost ({label}, automatic)")
        notify(l["agent_id"],
               f"{l['full_name']} was marked Lost: no follow-up for "
               f"{STALE_LEAD_DAYS} days in {l['status']}.", f"/leads/{l['id']}")
        lost_names.append(f"{l['full_name']} ({l['agent_name']})")
        moved += 1

    # 3. One summary for the people who manage the team.
    if lost_names:
        shown = ", ".join(lost_names[:5]) + (
            f" and {len(lost_names) - 5} more" if len(lost_names) > 5 else "")
        for m in query("SELECT id FROM users WHERE role IN ('admin','manager')"
                       " AND is_active = 1"):
            notify(m["id"], f"{moved} lead{'s' if moved > 1 else ''} marked Lost "
                   f"for no follow-up: {shown}.", "/leads/archive?status=Lost")
    return warned, moved


def start_scheduler(app):
    if not enabled():
        return None

    def loop():
        time.sleep(45)                    # let start-up settle
        while True:
            try:
                with app.app_context():
                    warned, moved = run_once()
                    if warned or moved:
                        print(f"  Lead follow-up rule: {warned} warned, "
                              f"{moved} marked Lost")
            except Exception as exc:      # never let this stop the CRM
                print(f"  Lead follow-up rule failed: {exc}")
            time.sleep(CHECK_MINUTES * 60)

    t = threading.Thread(target=loop, daemon=True, name="lead-rules")
    t.start()
    return t
