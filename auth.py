"""Session-based sign-in and role guards."""
import functools
import json

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from db import execute, log, now, query

bp = Blueprint("auth", __name__)


def load_current_user():
    """Runs before every request; puts the signed-in user on `g`."""
    uid = session.get("user_id")
    g.user = None
    g.unread = 0
    if uid:
        g.user = query("SELECT * FROM users WHERE id = ? AND is_active = 1",
                       (uid,), one=True)
        if g.user is None:
            session.clear()
        else:
            row = query("SELECT COUNT(*) AS c FROM notifications"
                        " WHERE user_id = ? AND is_read = 0", (uid,), one=True)
            g.unread = row["c"]


# --------------------------------------------------------------- permissions
#
# A role sets what someone can normally do. An admin can then grant or remove
# one of these for a single person without changing their role — the common
# case being one senior agent who is trusted with imports while the rest are
# not. Overrides live in users.permissions as JSON; anything not mentioned
# there falls back to the role.
#
# key: (label shown to an admin, what it lets someone do, {role: allowed})
CAPABILITIES = {
    "import": (
        "Import listings from Excel",
        "Upload a spreadsheet to add or update many listings at once, "
        "including replacing a partner's entire list.",
        {"admin": True, "manager": False, "agent": False},
    ),
    "publish": (
        "Publish listings",
        "Approve listings other people have sent, so they appear for "
        "everyone.",
        {"admin": True, "manager": False, "agent": False},
    ),
    "delete": (
        "Delete listings",
        "Remove listings for good, one at a time or in bulk.",
        {"admin": True, "manager": False, "agent": False},
    ),
    "export": (
        "Export data",
        "Download the listings, leads and deals as Excel or CSV.",
        {"admin": True, "manager": True, "agent": False},
    ),
}


def _overrides(user):
    if user is None:
        return {}
    keys = user.keys()
    raw = (user["permissions"] if "permissions" in keys else None) or ""
    try:
        loaded = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def can(capability, user=None):
    """Whether this person may do one specific thing.

    An admin always may. Locking an admin out of the very screens used to
    fix permissions is the kind of mistake that needs a database editor to
    undo, so it simply isn't possible.
    """
    person = user if user is not None else g.get("user")
    if person is None:
        return False
    role = person["role"]
    if role == "admin":
        return True
    spec = CAPABILITIES.get(capability)
    if spec is None:
        return False
    override = _overrides(person).get(capability)
    if isinstance(override, bool):
        return override
    return spec[2].get(role, False)


def effective_permissions(user):
    """Every capability for one person: (allowed, whether it was overridden)."""
    over = _overrides(user)
    out = {}
    for key, spec in CAPABILITIES.items():
        by_role = spec[2].get(user["role"], False)
        override = over.get(key)
        if user["role"] == "admin":
            out[key] = (True, False)
        elif isinstance(override, bool):
            out[key] = (override, override != by_role)
        else:
            out[key] = (by_role, False)
    return out


def requires(capability):
    """Guard a route with one capability."""
    def wrap(view):
        @functools.wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                return redirect(url_for("auth.login", next=request.path))
            if not can(capability):
                flash("You don't have access to that. An admin can change it "
                      "under Team members.", "error")
                return redirect(url_for("main.dashboard"))
            return view(*a, **kw)
        return wrapped
    return wrap


def login_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*a, **kw)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if g.user["role"] != "admin":
            flash("That area is limited to admins.", "error")
            return redirect(url_for("main.dashboard"))
        return view(*a, **kw)
    return wrapped


def is_admin():
    """Full control: team, settings, deletions, import rollback."""
    return g.user is not None and g.user["role"] == "admin"


def sees_all():
    """Whether this person sees the whole business rather than only their own
    records. Managers do; agents see their own work plus the shared pool."""
    return g.user is not None and g.user["role"] in ("admin", "manager")


def manager_required(view):
    """Reporting and exports: managers as well as admins."""
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if g.user["role"] not in ("admin", "manager"):
            flash("That area is limited to managers and admins.", "error")
            return redirect(url_for("main.dashboard"))
        return view(*a, **kw)
    return wrapped


def sees_finance():
    """Whether this person may see commission payouts and every deal,
    regardless of who's on it: admins and managers (who already see
    everything, see sees_all()) plus the accountant role, which exists
    specifically to follow deals and record payouts without the rest of
    the CRM's management screens."""
    return g.user is not None and g.user["role"] in ("admin", "manager", "accountant")


def finance_required(view):
    """Guard for the Financial section."""
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if not sees_finance():
            flash("That area is limited to admins, managers and accountants.", "error")
            return redirect(url_for("main.dashboard"))
        return view(*a, **kw)
    return wrapped


def can_publish():
    """Whether this person may turn a waiting listing into a live one."""
    return can("publish")


def published_only(alias="p"):
    """SQL fragment keeping unpublished listings out of a query.

    Used by the main list, the search and the exports. A listing waiting for
    approval is not part of the company's stock yet, so it should not appear
    where people go to find something to sell — and that includes the admin's
    own browsing, or the list stops meaning "what we can offer today".

    Listings that predate this feature have no approval value at all, hence
    the COALESCE: they are live and must stay live.
    """
    return f" AND COALESCE({alias}.approval, 'approved') = 'approved'"


def can_see_listing(record):
    """Whether this person may open one listing's own page.

    Wider than the browsing list: the agent who submitted something needs to
    follow it while it waits, and admins and managers need to review it.
    """
    if g.user is None or record is None:
        return False
    keys = record.keys()
    state = (record["approval"] if "approval" in keys else None) or "approved"
    if state == "approved":
        return True
    if sees_all():
        return True
    submitted = record["submitted_by"] if "submitted_by" in keys else None
    agent = record["agent_id"] if "agent_id" in keys else None
    return g.user["id"] in (submitted, agent)


def can_edit(record):
    """Admins and managers edit anything. Agents edit what's theirs or
    unclaimed."""
    if sees_all():
        return True
    if record is None:
        return False
    agent_id = record["agent_id"] if "agent_id" in record.keys() else None
    return agent_id in (None, g.user["id"])


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = query("SELECT * FROM users WHERE lower(email) = ?", (email,), one=True)
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Email or password doesn't match an account.", "error")
        elif not user["is_active"]:
            flash("That account has been switched off. Ask an admin to re-enable it.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            log(user["id"], "Signed in")
            nxt = request.args.get("next")
            return redirect(nxt if nxt and nxt.startswith("/") else url_for("main.dashboard"))
    return render_template("login.html")


@bp.route("/logout")
def logout():
    if g.user:
        log(g.user["id"], "Signed out")
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=("GET", "POST"))
@login_required
def account():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        execute("UPDATE users SET name = ?, phone = ? WHERE id = ?",
                (name, phone, g.user["id"]))
        new_pw = request.form.get("new_password", "")
        if new_pw:
            if len(new_pw) < 8:
                flash("Use at least 8 characters for a password.", "error")
                return redirect(url_for("auth.account"))
            if not check_password_hash(g.user["password_hash"],
                                       request.form.get("current_password", "")):
                flash("Current password is wrong, so the password wasn't changed.", "error")
                return redirect(url_for("auth.account"))
            execute("UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new_pw), g.user["id"]))
            log(g.user["id"], "Changed own password")
        flash("Account updated.", "ok")
        return redirect(url_for("auth.account"))
    return render_template("account.html")


def create_user(name, email, password, role="agent", phone=""):
    return execute(
        "INSERT INTO users (name, email, phone, password_hash, role, is_active, created_at)"
        " VALUES (?,?,?,?,?,1,?)",
        (name, email.lower(), phone, generate_password_hash(password), role, now()),
    )
