"""SQLite data layer for the Planned Real Estate CRM.

Uses the standard library only, so deployment needs nothing beyond Flask.
"""
import os
import sqlite3
from datetime import datetime, timedelta

from flask import current_app, g

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    phone         TEXT,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'agent',   -- admin | manager | agent
    is_active     INTEGER NOT NULL DEFAULT 1,
    photo         TEXT,
    job_title     TEXT,
    department    TEXT,
    joined_year   TEXT,
    employment    TEXT,
    languages     TEXT,
    areas_covered TEXT,
    bio           TEXT,
    manager_id    INTEGER,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owners (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    photo      TEXT,
    phone      TEXT,
    email      TEXT,
    company    TEXT,
    notes      TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS partners (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    photo        TEXT,
    partner_type TEXT NOT NULL DEFAULT 'Developer',
    phone        TEXT,
    email        TEXT,
    notes        TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS properties (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ref          TEXT UNIQUE,
    title        TEXT NOT NULL,
    address      TEXT,
    area         TEXT,
    building_no  TEXT,
    floor_no     TEXT,
    unit_no      TEXT,
    extras       TEXT,
    map_url      TEXT,
    is_own       INTEGER NOT NULL DEFAULT 0,
    import_source TEXT,
    imported_at   TEXT,
    prop_type    TEXT NOT NULL DEFAULT 'Apartment', -- Villa|Apartment|Commercial|Office|Land
    listing_type TEXT NOT NULL DEFAULT 'Sale',      -- Sale | Rent
    status       TEXT NOT NULL DEFAULT 'Available', -- Available|Reserved|Sold|Rented
    price        REAL DEFAULT 0,
    size_sqm     REAL,
    bedrooms     INTEGER,
    bathrooms    INTEGER,
    description  TEXT,
    features     TEXT,
    owner_id     INTEGER REFERENCES owners(id) ON DELETE SET NULL,
    partner_id   INTEGER REFERENCES partners(id) ON DELETE SET NULL,
    agent_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS property_images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    is_cover    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id   INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    original_name TEXT NOT NULL,
    label         TEXT,
    uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE,
    full_name   TEXT NOT NULL,
    email       TEXT,
    phone       TEXT,
    source      TEXT DEFAULT 'Walk-in',
    status      TEXT NOT NULL DEFAULT 'New',
    budget      REAL,
    notes       TEXT,
    next_follow_up  TEXT,
    last_contact_at TEXT,
    agent_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS viewings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id      INTEGER REFERENCES leads(id) ON DELETE CASCADE,
    property_id  INTEGER REFERENCES properties(id) ON DELETE SET NULL,
    agent_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    scheduled_at TEXT NOT NULL,
    notes        TEXT,
    done         INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ref           TEXT UNIQUE,
    property_id   INTEGER REFERENCES properties(id) ON DELETE SET NULL,
    lead_id       INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    agent_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    deal_type     TEXT NOT NULL DEFAULT 'Sale',      -- Sale | Rent
    value         REAL NOT NULL DEFAULT 0,
    commission_pct   REAL NOT NULL DEFAULT 0,
    commission_amt   REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'Agreed',    -- Agreed|Signed|Collected|Cancelled
    closed_at     TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Who shares a deal's commission. One row per participating agent.
-- Defined without foreign keys to match what migrate_deals.py created on
-- machines that ran it by hand, so both end up with the same schema.
CREATE TABLE IF NOT EXISTS deal_agents (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    role      TEXT    NOT NULL DEFAULT 'lead',
    share_pct REAL    NOT NULL DEFAULT 100,
    amount    REAL,
    UNIQUE (deal_id, user_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,      -- property | lead
    entity_id   INTEGER NOT NULL,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    entity_type TEXT,
    entity_id   INTEGER,
    action      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message    TEXT NOT NULL,
    link       TEXT,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    filename    TEXT,
    source      TEXT,
    mode        TEXT NOT NULL,          -- preview|add|update|replace
    sheets      TEXT,
    rows_read   INTEGER NOT NULL DEFAULT 0,
    added       INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    removed     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'complete',
    undo_data   TEXT,                   -- JSON snapshot for rollback
    undone_at   TEXT,
    undone_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_deal_status ON deals(status);
CREATE INDEX IF NOT EXISTS idx_deal_agent  ON deals(agent_id);
CREATE INDEX IF NOT EXISTS idx_prop_status ON properties(status);
CREATE INDEX IF NOT EXISTS idx_prop_agent  ON properties(agent_id);
CREATE INDEX IF NOT EXISTS idx_lead_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_lead_follow  ON leads(next_follow_up);
CREATE INDEX IF NOT EXISTS idx_import_when ON imports(created_at);
CREATE INDEX IF NOT EXISTS idx_lead_agent  ON leads(agent_id);
CREATE INDEX IF NOT EXISTS idx_comment_ent ON comments(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_notif_user  ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_deal_agents_user ON deal_agents(user_id);
CREATE INDEX IF NOT EXISTS idx_deal_agents_payout ON deal_agents(payout_status);
"""

LEAD_STAGES = ["New", "Contacted", "Qualified", "Viewing", "Offer",
               "Negotiation", "Won", "Lost"]
# Stages that mean the lead is finished, so it stops appearing in follow-up counts.
CLOSED_STAGES = ("Won", "Lost")
PROP_TYPES = ["Villa", "Apartment", "Commercial", "Office", "Land"]
PROP_STATUS = ["Available", "Reserved", "Sold", "Rented"]
LISTING_TYPES = ["Sale", "Rent"]
# Whether a listing is published. Separate from status, which is about the
# market (a listing waiting for approval still has a status of Available).
APPROVAL_STATES = ["pending", "approved", "rejected"]
# Why a client was lost. Recorded as a code so losses stay countable across the
# team ("six on price this month"); the agent's own words go in leads.lost_note
# beside it, because the code alone never explains the particular client.
LOST_REASONS = [
    ("price",        "Price too high"),
    ("competitor",   "Found somewhere else"),
    ("timing",       "Changed their mind / timing"),
    ("unresponsive", "Not serious / unreachable"),
    ("unavailable",  "Property no longer available"),
    ("requirements", "Requirements we can't match"),
    ("other",        "Other"),
]
LOST_REASON_LABELS = dict(LOST_REASONS)

LEAD_SOURCES = ["Walk-in", "Website", "Property Finder", "Referral",
                "Instagram", "WhatsApp", "Phone", "Other"]
PARTNER_TYPES = ["Developer", "Legal", "Maintenance", "Bank", "Marketing", "Other"]
DEAL_STATUS = ["Agreed", "Signed", "Collected", "Cancelled"]
PAYOUT_STATUS = [
    ("unpaid", "Unpaid"),
    ("partial", "Partially paid"),
    ("paid", "Paid"),
]
# admin/manager/agent already existed as free-text role values; accountant is
# the fourth, added for the Financial section (see auth.sees_finance()).
ROLES = ["agent", "manager", "accountant", "admin"]

# Qatar has no daylight saving, so a fixed offset is correct all year.
TZ_OFFSET = timedelta(hours=int(os.environ.get("TZ_OFFSET_HOURS", "3")))

STAMP = "%Y-%m-%d %H:%M:%S"


def now():
    """Current UTC time. Everything is stored in UTC and shown in local time."""
    return datetime.utcnow().strftime(STAMP)


def parse(value):
    """Read a stored timestamp, tolerating the shorter form used by date inputs."""
    if not value:
        return None
    s = str(value).replace("T", " ")
    for fmt in (STAMP, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None


def to_local(value):
    """UTC timestamp -> local (Doha) datetime, for display."""
    dt = parse(value)
    return dt + TZ_OFFSET if dt else None


def to_utc(value):
    """Local time typed by a user -> UTC, for storage."""
    dt = parse(value)
    return (dt - TZ_OFFSET).strftime(STAMP) if dt else None


def local_now():
    return datetime.utcnow() + TZ_OFFSET


def local_today():
    return local_now().strftime("%Y-%m-%d")


def days_ago(n):
    """UTC timestamp for 'n days before now', for stale/overdue cutoffs."""
    return (datetime.utcnow() - timedelta(days=n)).strftime(STAMP)


def utc_day_bounds(local_date):
    """The UTC range covering one local calendar day."""
    start = datetime.strptime(local_date, "%Y-%m-%d") - TZ_OFFSET
    return start.strftime(STAMP), (start + timedelta(days=1)).strftime(STAMP)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"],
                               detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    lastrow = cur.lastrowid
    cur.close()
    return lastrow


MIGRATIONS = [
    ("users", "lang", "TEXT DEFAULT 'en'"),
    # Per-person exceptions to what their role normally allows, as JSON.
    ("users", "permissions", "TEXT"),
    ("properties", "building_no", "TEXT"),
    ("properties", "unit_no", "TEXT"),
    ("properties", "map_url", "TEXT"),
    ("properties", "is_own", "INTEGER NOT NULL DEFAULT 0"),
    ("properties", "import_source", "TEXT"),
    ("properties", "imported_at", "TEXT"),
    ("leads", "next_follow_up", "TEXT"),
    ("leads", "last_contact_at", "TEXT"),
    ("users", "photo", "TEXT"),
    ("users", "job_title", "TEXT"),
    ("users", "department", "TEXT"),
    ("users", "joined_year", "TEXT"),
    ("users", "employment", "TEXT"),
    ("users", "languages", "TEXT"),
    ("users", "areas_covered", "TEXT"),
    ("users", "bio", "TEXT"),
    ("users", "manager_id", "INTEGER"),
    ("owners", "photo", "TEXT"),
    ("owners", "address", "TEXT"),
    ("partners", "photo", "TEXT"),
    ("partners", "company", "TEXT"),
    ("partners", "address", "TEXT"),
    ("properties", "partner_id", "INTEGER"),
    ("properties", "floor_no", "TEXT"),
    ("properties", "extras", "TEXT"),
    ("properties", "last_verified", "TEXT"),
    # Listings added by an agent or manager wait for an admin to publish them.
    # Everything already in the table predates this and is therefore live, so
    # the default has to be 'approved' or 171 listings would vanish at once.
    ("properties", "approval", "TEXT NOT NULL DEFAULT 'approved'"),
    ("properties", "submitted_by", "INTEGER"),
    ("properties", "reviewed_by", "INTEGER"),
    ("properties", "reviewed_at", "TEXT"),
    ("properties", "review_note", "TEXT"),
    # Flexible commission — these were previously applied by running
    # migrate_deals.py by hand, which is easy to forget on a deployed copy and
    # leaves every Deals page erroring on the missing columns.
    ("deals", "term_months", "INTEGER"),
    ("deals", "free_months", "REAL"),
    ("deals", "commission_basis", "TEXT"),
    ("deals", "commission_on", "TEXT"),
    # Commission payouts — what's actually been paid to each agent on a deal,
    # as opposed to deal_agents.amount, which is what they earned. Tracked
    # per deal_agents row so a split deal pays out per person, not per deal.
    ("deal_agents", "payout_status", "TEXT NOT NULL DEFAULT 'unpaid'"),
    ("deal_agents", "paid_amount", "REAL NOT NULL DEFAULT 0"),
    ("deal_agents", "paid_at", "TEXT"),
    ("deal_agents", "payout_note", "TEXT"),
    ("deal_agents", "recorded_by", "INTEGER"),
    # A lead cannot be closed as Lost without a reason and a written note.
    # Kept on the lead itself rather than only as a comment, so the report can
    # group losses by cause instead of re-reading everyone's prose.
    ("leads", "lost_reason", "TEXT"),
    ("leads", "lost_note", "TEXT"),
    ("leads", "lost_at", "TEXT"),
]

# How long a listing can go without someone confirming it's still on the
# market before it counts as "stale" on the office-admin screen.
STALE_DAYS = 30

# Rooms a flat may have besides bedrooms. Kept apart from Key features, which
# is about the building and the view rather than the rooms themselves.
EXTRA_ROOMS = ["Office", "Study", "Maid's room", "Driver's room", "Majlis",
               "Balcony", "Terrace", "Garden", "Roof", "Store", "Laundry",
               "Pantry", "Basement", "Parking"]

IMPORT_MODES = [
    ("preview", "Preview only — check the file, change nothing"),
    ("add", "Add new units only — leave anything we already have untouched"),
    ("update", "Update what we have and add anything new"),
    ("replace", "Replace this partner's listings entirely"),
]

EMPLOYMENT = ["Full-time", "Part-time", "Contract", "Freelance"]
DEPARTMENTS = ["Sales", "Leasing", "Property Management", "Administration",
               "Marketing", "Finance"]


def backfill_deals(con):
    """Give older deals the commission description the newer code expects.

    Written so running it twice changes nothing: COALESCE only fills columns
    that are still empty, and the split rows are inserted only where none
    exist. No existing commission amount is recalculated — historic deals keep
    exactly the figure they were recorded with.
    """
    con.execute(
        "UPDATE deals SET term_months = COALESCE(term_months, 12),"
        " free_months = COALESCE(free_months, 0),"
        " commission_on = COALESCE(commission_on, 'contract'),"
        " commission_basis = COALESCE(commission_basis,"
        "   CASE WHEN lower(COALESCE(deal_type,'')) LIKE 'rent%'"
        "        THEN 'monthly_rent' ELSE 'sale_price' END)")

    # Each existing deal's agent becomes its sole 100% participant.
    con.execute(
        "INSERT INTO deal_agents (deal_id, user_id, role, share_pct, amount)"
        " SELECT d.id, d.agent_id, 'lead', 100, d.commission_amt FROM deals d"
        " WHERE d.agent_id IS NOT NULL"
        "   AND NOT EXISTS (SELECT 1 FROM deal_agents da WHERE da.deal_id = d.id)")


def init_db(app):
    """Tables, then migrations, then indexes — in that order.

    An index on a column added by a migration cannot be created before the
    migration has run, which is why indexes are kept out of the table script.
    """
    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    con = sqlite3.connect(app.config["DATABASE"])
    con.executescript(SCHEMA)

    for table, column, spec in MIGRATIONS:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
    con.commit()

    con.executescript(INDEXES)
    con.commit()

    backfill_deals(con)
    con.commit()
    con.close()


PER_PAGE = 50


def paginate(sql, args, page, per_page=PER_PAGE):
    """Run a query one page at a time.

    Counting through a subquery keeps this working with any WHERE and ORDER BY
    the caller has already built, so filters and sorting stay honest across
    pages instead of only sorting whatever happened to load.
    """
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1

    total = query(f"SELECT COUNT(*) AS c FROM ({sql})", args, one=True)["c"]
    pages = max(1, -(-total // per_page))        # ceiling division
    page = min(page, pages)
    offset = (page - 1) * per_page
    rows = query(f"{sql} LIMIT ? OFFSET ?", list(args) + [per_page, offset])

    return {
        "rows": rows, "total": total, "page": page, "pages": pages,
        "per_page": per_page,
        "first": offset + 1 if total else 0,
        "last": min(offset + per_page, total),
        "has_prev": page > 1, "has_next": page < pages,
        "prev": page - 1, "next": page + 1,
        # a short window of page numbers, so 40 pages don't fill the screen
        "window": [p for p in range(max(1, page - 2), min(pages, page + 2) + 1)],
    }


def next_ref(prefix, table):
    """The next unused reference number for a table, formatted PREFIX-0001.

    Based on the highest number actually in use for this prefix, not a row
    count — a row count breaks the moment anything with that prefix is ever
    deleted (a rejected lead, a removed listing, ...): the count then
    undershoots and reissues a number that's still on an existing row,
    which fails the column's UNIQUE constraint with a 500 on save.
    """
    row = query(
        f"SELECT MAX(CAST(SUBSTR(ref, LENGTH(?) + 2) AS INTEGER)) AS n"
        f" FROM {table} WHERE ref LIKE ?",
        (prefix, f"{prefix}-%"), one=True)
    n = (row["n"] or 0) + 1
    ref = f"{prefix}-{n:04d}"
    # Belt and suspenders: two people finishing a form in the same instant
    # could still land on the same number before either has saved.
    while query(f"SELECT 1 FROM {table} WHERE ref = ?", (ref,), one=True):
        n += 1
        ref = f"{prefix}-{n:04d}"
    return ref


def log(user_id, action, entity_type=None, entity_id=None, detail=None):
    """Write an audit-trail entry. Called on every meaningful change."""
    execute(
        "INSERT INTO activity (user_id, entity_type, entity_id, action, detail, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (user_id, entity_type, entity_id, action, detail, now()),
    )


def notify(user_id, message, link=None):
    if not user_id:
        return
    execute(
        "INSERT INTO notifications (user_id, message, link, is_read, created_at)"
        " VALUES (?,?,?,0,?)",
        (user_id, message, link, now()),
    )


def get_setting(key, default=""):
    """Always returns text. A row saved with a NULL value used to come back as
    None, which then broke anything calling .strip() on it."""
    row = query("SELECT value FROM settings WHERE key = ?", (key,), one=True)
    if row is None or row["value"] is None:
        return default
    return row["value"]


def set_setting(key, value):
    execute("INSERT INTO settings (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
