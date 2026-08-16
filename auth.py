"""Session-based sign-in and role guards."""
import functools

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
