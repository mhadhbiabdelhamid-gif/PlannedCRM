"""
Adds flexible commission and multi-agent support to the deals table.

Run once, from the project folder:

    python migrate_deals.py

Safe to run twice - it checks before changing anything. It does NOT recalculate
any existing commission amount. Historic deals keep exactly the figure they were
recorded with; the new fields only describe how future ones are worked out.
"""
import os
import sqlite3
import sys
from datetime import datetime

DB = os.environ.get("CRM_DB", os.path.join("instance", "crm.sqlite3"))

NEW_COLUMNS = [
    ("term_months",      "INTEGER"),
    ("free_months",      "REAL"),
    ("commission_basis", "TEXT"),
    ("commission_on",    "TEXT"),
]


def main():
    if not os.path.exists(DB):
        sys.exit(f"Database not found at {DB}. Run this from the project folder.")

    backup = os.path.join(
        "instance", "backups",
        f"crm-before-deals-{datetime.now():%Y%m%d-%H%M%S}.sqlite3")
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    src = sqlite3.connect(DB)
    dst = sqlite3.connect(backup)
    src.backup(dst)
    dst.close()
    print(f"Backed up to {backup}")

    src.row_factory = sqlite3.Row
    cur = src.cursor()

    existing = {r[1] for r in cur.execute("PRAGMA table_info(deals)")}
    for name, coltype in NEW_COLUMNS:
        if name in existing:
            print(f"  deals.{name} already there, left alone")
            continue
        cur.execute(f"ALTER TABLE deals ADD COLUMN {name} {coltype}")
        print(f"  added deals.{name}")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS deal_agents (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id   INTEGER NOT NULL,
            user_id   INTEGER NOT NULL,
            role      TEXT    NOT NULL DEFAULT 'lead',
            share_pct REAL    NOT NULL DEFAULT 100,
            amount    REAL,
            UNIQUE (deal_id, user_id)
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_deal_agents_user "
                "ON deal_agents (user_id)")
    print("  deal_agents ready")

    # Describe existing deals without changing their money.
    cur.execute(
        "UPDATE deals SET term_months = COALESCE(term_months, 12), "
        "free_months = COALESCE(free_months, 0), "
        "commission_on = COALESCE(commission_on, 'contract'), "
        "commission_basis = COALESCE(commission_basis, "
        "  CASE WHEN lower(COALESCE(deal_type,'')) LIKE 'rent%' "
        "       THEN 'monthly_rent' ELSE 'sale_price' END)")
    print(f"  described {cur.rowcount} existing deal(s)")

    # Every existing deal's agent becomes its sole 100% participant.
    moved = 0
    for row in cur.execute(
            "SELECT id, agent_id, commission_amt FROM deals "
            "WHERE agent_id IS NOT NULL").fetchall():
        already = cur.execute(
            "SELECT 1 FROM deal_agents WHERE deal_id = ?", (row["id"],)
        ).fetchone()
        if already:
            continue
        cur.execute(
            "INSERT INTO deal_agents (deal_id, user_id, role, share_pct, amount)"
            " VALUES (?, ?, 'lead', 100, ?)",
            (row["id"], row["agent_id"], row["commission_amt"]))
        moved += 1
    print(f"  carried {moved} deal(s) over to the split table")

    src.commit()
    src.close()
    print("\nDone. Existing commission amounts were not recalculated.")


if __name__ == "__main__":
    main()
