"""Session-based sign-in and role guards."""
import functools
import hashlib
import json
import os
import time

from flask import (Blueprint, current_app, flash, g, redirect,
                   render_template, request, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
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
    "ads_leads": (
        "Import leads from ads",
        "Pull new leads from the connected Facebook/Instagram ad account, "
        "and import a TikTok leads export file.",
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
        new_email = request.form.get("email", "").strip().lower()
        new_pw = request.form.get("new_password", "")
        current_pw = request.form.get("current_password", "")
        old_email = g.user["email"]
        email_changing = bool(new_email) and new_email != old_email.lower()

        if not name:
            flash("Your name can't be empty.", "error")
            return redirect(url_for("auth.account"))

        # Changing the address you sign in with, or the password, both need
        # the current password, so a laptop left signed in can't be used to
        # take the account over.
        if email_changing or new_pw:
            if not check_password_hash(g.user["password_hash"], current_pw):
                flash("Current password is wrong, so your email and password "
                      "weren't changed.", "error")
                return redirect(url_for("auth.account"))

        if email_changing:
            import mailer
            if not mailer.valid_address(new_email):
                flash("That doesn't look like an email address.", "error")
                return redirect(url_for("auth.account"))
            taken = query("SELECT id FROM users WHERE lower(email) = ? AND id != ?",
                          (new_email, g.user["id"]), one=True)
            if taken:
                flash("Another account already uses that email.", "error")
                return redirect(url_for("auth.account"))

        if new_pw and len(new_pw) < 8:
            flash("Use at least 8 characters for a password.", "error")
            return redirect(url_for("auth.account"))

        execute("UPDATE users SET name = ?, phone = ? WHERE id = ?",
                (name, phone, g.user["id"]))
        if email_changing:
            execute("UPDATE users SET email = ? WHERE id = ?",
                    (new_email, g.user["id"]))
            log(g.user["id"], "Changed own email", detail=f"{old_email} → {new_email}")
            _notify(old_email, "Your CRM sign-in email was changed",
                    f"Hello {name},\n\nThe email you use to sign in to the CRM "
                    f"was changed from {old_email} to {new_email}.\n\nIf you "
                    f"didn't do this, tell an admin straight away.")
        if new_pw:
            execute("UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new_pw), g.user["id"]))
            log(g.user["id"], "Changed own password")
            _notify(new_email if email_changing else old_email,
                    "Your CRM password was changed",
                    f"Hello {name},\n\nYour CRM password was just changed.\n\n"
                    f"If you didn't do this, tell an admin straight away.")
        flash("Account updated.", "ok")
        return redirect(url_for("auth.account"))
    return render_template("account.html")


# ------------------------------------------------------------ password reset
#
# A reset link carries a signed token rather than a row in the database. It
# expires after an hour, and it includes a fingerprint of the current
# password hash and email, so it stops working the moment it has been used
# (the hash changes) or the email on the account changes.

RESET_MAX_AGE = 60 * 60          # one hour
_RESET_SALT = "planned-crm-password-reset"
_last_sent = {}                  # email -> time of last reset mail, per worker


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt=_RESET_SALT)


def _fingerprint(user):
    raw = f"{user['password_hash']}|{user['email'].lower()}".encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def make_reset_token(user):
    return _serializer().dumps({"id": user["id"], "f": _fingerprint(user)})


def user_from_reset_token(token):
    """The account a token belongs to, or None if it's bad, used or expired."""
    try:
        data = _serializer().loads(token, max_age=RESET_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return None
    if not isinstance(data, dict):
        return None
    user = query("SELECT * FROM users WHERE id = ?", (data.get("id"),), one=True)
    if user is None or data.get("f") != _fingerprint(user):
        return None
    return user


def _site_url():
    """Base address for links in emails. PUBLIC_URL wins if set; otherwise the
    address the request came in on, honouring Render's HTTPS proxy."""
    fixed = (os.environ.get("PUBLIC_URL") or "").strip().rstrip("/")
    if fixed:
        return fixed
    root = request.host_url.rstrip("/")
    if request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https":
        root = "https://" + root.split("://", 1)[-1]
    return root


def _notify(to, subject, body):
    """Best-effort security notice. Never blocks the change if mail is off."""
    try:
        import mailer
        if mailer.is_configured():
            mailer.send(to, subject, body)
    except Exception:
        pass


@bp.route("/forgot-password", methods=("GET", "POST"))
def forgot_password():
    import mailer
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = query("SELECT * FROM users WHERE lower(email) = ?", (email,), one=True)
        if not mailer.is_configured():
            flash("Email isn't set up in this CRM yet, so a reset link can't "
                  "be sent. Ask an admin to reset your password.", "error")
            return redirect(url_for("auth.forgot_password"))

        recently = time.time() - _last_sent.get(email, 0) < 120
        if user is not None and user["is_active"] and not recently:
            link = _site_url() + url_for("auth.reset_password",
                                         token=make_reset_token(user))
            ok, detail = mailer.send(
                user["email"], "Reset your CRM password",
                f"Hello {user['name']},\n\n"
                f"Someone asked to reset the password for your CRM account. "
                f"To choose a new password, open this link:\n\n{link}\n\n"
                f"The link works once and expires in 1 hour.\n\n"
                f"If you didn't ask for this, you can ignore this email; your "
                f"password stays the same.")
            if ok:
                _last_sent[email] = time.time()
                log(user["id"], "Requested a password reset link")
            else:
                current_app.logger.warning("Reset mail failed: %s", detail)
        # Same answer whether or not the email exists, so this page can't be
        # used to find out who has an account.
        flash("If that email belongs to an account, a reset link is on its "
              "way. Check your inbox and spam folder.", "ok")
        return redirect(url_for("auth.login"))
    return render_template("forgot_password.html", mail_ready=mailer.is_configured())


@bp.route("/reset-password/<token>", methods=("GET", "POST"))
def reset_password(token):
    user = user_from_reset_token(token)
    if user is None:
        flash("That reset link has expired or was already used. Ask for a "
              "new one.", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        if len(pw) < 8:
            flash("Use at least 8 characters for a password.", "error")
            return redirect(request.path)
        if pw != request.form.get("confirm", ""):
            flash("The two passwords don't match.", "error")
            return redirect(request.path)
        execute("UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(pw), user["id"]))
        log(user["id"], "Reset password from email link")
        session.clear()
        flash("Password changed. Sign in with your new password.", "ok")
        return redirect(url_for("auth.login"))
    return render_template("reset_password.html", person=user)


def create_user(name, email, password, role="agent", phone=""):
    return execute(
        "INSERT INTO users (name, email, phone, password_hash, role, is_active, created_at)"
        " VALUES (?,?,?,?,?,1,?)",
        (name, email.lower(), phone, generate_password_hash(password), role, now()),
    )
