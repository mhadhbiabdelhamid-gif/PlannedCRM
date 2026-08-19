"""Planned Real Estate — internal CRM.

Run locally:   python app.py
Run in prod:   gunicorn "app:create_app()"
"""
import os
import secrets
import sqlite3
import uuid
from datetime import timedelta

import auth
import backups
import mailer
import views_admin
import views_deals
import views_imports
import views_leads
import views_main
import views_properties
import views_reports
import whatsapp
from db import (LEAD_STAGES, TZ_OFFSET, close_db, get_setting, init_db,
                local_now, query, to_local)
from flask import Flask, g, redirect, request, session, url_for
from markupsafe import Markup, escape
from i18n import LANGS, current_lang, is_rtl, t
from ai_intake import intake_bp
from ai_social import social_bp
from restore import restore_bp

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


LOGO_EXTS = ("png", "jpg", "jpeg", "webp", "svg", "gif")

# The value this used to fall back to. Anyone who knows it can forge a signed
# session cookie and sign in as an admin, so it is never used as a real key.
PLACEHOLDER_KEY = "change-me-before-you-deploy"


def resolve_secret_key(data_dir):
    """The key that signs session cookies.

    SECRET_KEY from the environment wins. Failing that, a key kept beside the
    database is used, and generated the first time if it isn't there yet.

    The point is that there is no path back to a known constant: forgetting to
    set SECRET_KEY on a deployed copy used to leave every session cookie
    forgeable, and nothing on screen said so.
    """
    from_env = (os.environ.get("SECRET_KEY") or "").strip()
    if from_env and from_env != PLACEHOLDER_KEY:
        return from_env

    key_path = os.path.join(data_dir, "secret_key")
    try:
        with open(key_path, "r", encoding="utf-8") as fh:
            stored = fh.read().strip()
        if stored:
            return stored
    except OSError:
        pass

    generated = secrets.token_hex(32)
    try:
        os.makedirs(data_dir, exist_ok=True)
        # Created exclusively: the server runs several workers, and each one
        # calls this at start-up. Without O_EXCL they would each write their
        # own key, and a cookie signed by one worker would be rejected by the
        # next — an endless bounce back to the sign-in page.
        fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(generated)
        print(f"  SECRET_KEY was not set, so one was generated and saved to "
              f"{key_path}.\n"
              f"  Everyone will need to sign in again. Set SECRET_KEY in the "
              f"environment to control it yourself.")
        return generated
    except FileExistsError:
        # Another worker got there first; its key is the one that counts.
        try:
            with open(key_path, "r", encoding="utf-8") as fh:
                stored = fh.read().strip()
            if stored:
                return stored
        except OSError:
            pass
    except OSError as exc:
        # A read-only disk still must not fall back to a shared constant. A
        # key that lasts only until restart costs convenience, not safety.
        print(f"  SECRET_KEY was not set and could not be saved ({exc}). "
              f"Using a temporary key — everyone will be signed out whenever "
              f"the server restarts.")
    return generated


def find_logo():
    """Look for logo.<anything sensible>, case-insensitively.

    Returns (static_filename, version) or None. The version is the file's
    modification time, appended as a query string so a replaced logo shows up
    immediately instead of being served from the browser's cache.
    """
    folder = os.path.join(BASE_DIR, "static", "img")
    if not os.path.isdir(folder):
        return None
    for entry in sorted(os.listdir(folder)):
        stem, _, ext = entry.rpartition(".")
        if stem.lower() == "logo" and ext.lower() in LOGO_EXTS:
            full = os.path.join(folder, entry)
            return f"img/{entry}", int(os.path.getmtime(full))
    return None


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    database = os.environ.get("DATABASE_PATH",
                              os.path.join(BASE_DIR, "instance", "crm.sqlite3"))
    app.config.from_mapping(
        SECRET_KEY=resolve_secret_key(os.path.dirname(database)),
        DATABASE=database,
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER",
                                     os.path.join(BASE_DIR, "instance", "uploads")),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,       # 25 MB per upload
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if os.environ.get("HTTPS_ONLY", "").lower() in ("1", "true", "yes"):
        app.config["SESSION_COOKIE_SECURE"] = True

    for sub in ("images", "docs"):
        os.makedirs(os.path.join(app.config["UPLOAD_FOLDER"], sub), exist_ok=True)

    init_db(app)
    app.teardown_appcontext(close_db)
    app.before_request(auth.load_current_user)

    app.register_blueprint(auth.bp)
    app.register_blueprint(views_main.bp)
    app.register_blueprint(views_properties.bp)
    app.register_blueprint(views_leads.bp)
    app.register_blueprint(views_deals.bp)
    app.register_blueprint(views_imports.bp)
    app.register_blueprint(views_admin.contacts)
    app.register_blueprint(views_admin.admin)
    app.register_blueprint(views_reports.bp)
    app.register_blueprint(intake_bp)
    app.register_blueprint(social_bp)
    app.register_blueprint(restore_bp)

    # ------------------------------------------------------- language switch
    @app.route("/language/<code>")
    def set_language(code):
        if code in LANGS:
            session["lang"] = code
            if g.get("user") is not None:
                from db import execute
                execute("UPDATE users SET lang = ? WHERE id = ?", (code, g.user["id"]))
        target = request.referrer
        return redirect(target if target and target.startswith(request.host_url)
                        else url_for("main.dashboard"))

    # ------------------------------------------------------- template helpers
    @app.template_filter("money")
    def money(value):
        """Thousand separators, with decimals shown only when they carry meaning."""
        try:
            num = float(value or 0)
        except (TypeError, ValueError):
            return "0"
        if abs(num - round(num)) < 0.005:
            return f"{num:,.0f}"
        return f"{num:,.2f}".rstrip("0").rstrip(".")

    @app.template_filter("nice_date")
    def nice_date(value, fmt="%d %b %Y"):
        dt = to_local(value)
        return dt.strftime(fmt) if dt else "—"

    @app.template_filter("nice_time")
    def nice_time(value):
        dt = to_local(value)
        return dt.strftime("%d %b %Y, %H:%M") if dt else "—"

    @app.template_filter("local_input")
    def local_input(value):
        """Format a stored timestamp for a datetime-local field."""
        dt = to_local(value)
        return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""

    @app.template_filter("ago")
    def ago(value):
        dt = to_local(value)
        if dt is None:
            return str(value or "")
        secs = (local_now() - dt).total_seconds()
        if secs < 0:
            return dt.strftime("%d %b, %H:%M")
        if secs < 60:
            return t("just now")
        if secs < 3600:
            return f"{int(secs // 60)}m"
        if secs < 86400:
            return f"{int(secs // 3600)}h"
        if secs < 604800:
            return f"{int(secs // 86400)}d"
        return dt.strftime("%d %b")

    def avatar(person, size=32):
        """One avatar everywhere: the photo if there is one, initials if not."""
        if person is None:
            return ""
        get = (person.get if isinstance(person, dict)
               else lambda k, d=None: (person[k] if k in person.keys() else d))
        photo = get("photo")
        name = get("name") or get("agent_name") or get("user_name") or "?"
        style = (f"width:{size}px;height:{size}px;flex:0 0 {size}px;"
                 f"font-size:{max(9, int(size * 0.36))}px")
        if photo:
            src = url_for("main.uploaded_file", kind="avatars", filename=photo)
            return Markup(
                f'<span class="avatar has-photo" style="{style}">'
                f'<img src="{escape(src)}" alt="{escape(name)}" loading="lazy"></span>')
        parts = [p for p in str(name).split() if p]
        letters = "".join(p[0] for p in parts[:2]).upper() or "?"
        return Markup(f'<span class="avatar" style="{style}">{escape(letters)}</span>')

    @app.template_filter("initials")
    def initials(name):
        parts = [p for p in str(name or "?").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def wa_button(phone, key, **values):
        """Build a WhatsApp deep link from one of the stored templates."""
        template = get_setting(key, "") or whatsapp.DEFAULTS.get(key, "")
        values.setdefault("company", get_setting("company_name", "Planned Real Estate"))
        values.setdefault("currency", get_setting("currency", "QAR"))
        values.setdefault("agent", g.user["name"] if g.get("user") else "")
        return whatsapp.link(phone, whatsapp.fill(template, **values))

    @app.context_processor
    def inject():
        offset_hours = int(TZ_OFFSET.total_seconds() // 3600)
        logo = find_logo()
        stale_count = 0
        waiting_count = 0
        if g.get("user") is not None:
            from db import STALE_DAYS, days_ago
            from auth import published_only, sees_all
            cutoff = days_ago(STALE_DAYS)
            sql = ("SELECT COUNT(*) c FROM properties p"
                   " WHERE p.status IN ('Available','Reserved')"
                   + published_only("p") +
                   " AND (p.last_verified IS NULL OR p.last_verified < ?)")
            args = [cutoff]
            if not sees_all():
                sql += " AND (p.agent_id = ? OR p.agent_id IS NULL)"
                args.append(g.user["id"])
            stale_count = query(sql, args, one=True)["c"]

            wsql = ("SELECT COUNT(*) c FROM properties p WHERE"
                    " COALESCE(p.approval, 'approved') IN ('pending', 'rejected')")
            wargs = []
            if not sees_all():
                wsql += " AND p.submitted_by = ?"
                wargs.append(g.user["id"])
            waiting_count = query(wsql, wargs, one=True)["c"]
        return dict(
            stale_count=stale_count,
            waiting_count=waiting_count,
            logo_exists=logo is not None,
            logo_url=(f"{url_for('static', filename=logo[0])}?v={logo[1]}"
                      if logo else None),
            company=get_setting("company_name", "Planned Real Estate"),
            currency=get_setting("currency", "QAR"),
            stages=LEAD_STAGES,
            user=g.get("user"),
            unread=g.get("unread", 0),
            year=local_now().year,
            t=t,
            can=__import__('auth').can,
            lang=current_lang(),
            langs=LANGS,
            rtl=is_rtl(),
            wa=wa_button,
            mail_ready=mailer.is_configured(),
            avatar=avatar,
            wa_labels=whatsapp.LABELS,
            tz_label=f"UTC{'+' if offset_hours >= 0 else ''}{offset_hours}",
        )

    @app.errorhandler(404)
    def not_found(_e):
        from flask import render_template
        if g.get("user") is None:
            return redirect(url_for("auth.login"))
        return render_template("error.html", code="404",
                               msg=t("That page isn't part of the CRM.")), 404

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def server_error(err):
        """Write the real cause to instance/server.log and show something useful.

        A bare 'Internal Server Error' tells the person nothing and tells whoever
        has to fix it even less, so every failure gets a short reference code
        that appears both on screen and in the log.
        """
        from werkzeug.exceptions import HTTPException
        if isinstance(err, HTTPException) and err.code != 500:
            return err

        import traceback
        from flask import render_template
        ref = uuid.uuid4().hex[:8].upper()
        when = local_now().strftime("%Y-%m-%d %H:%M:%S")
        who = g.user["email"] if g.get("user") else "not signed in"

        report = (f"\n{'=' * 70}\n"
                  f"ERROR {ref}   {when}\n"
                  f"  page   : {request.method} {request.path}\n"
                  f"  user   : {who}\n"
                  f"{'-' * 70}\n"
                  f"{traceback.format_exc()}"
                  f"{'=' * 70}\n")
        print(report)
        try:
            log_path = os.path.join(os.path.dirname(app.config["DATABASE"]),
                                    "server.log")
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(report)
        except OSError:
            pass

        try:
            return render_template("error.html", code="500", ref=ref, msg=(
                "Something went wrong at our end and your request was not "
                "completed. Nothing you were working on has been lost.")), 500
        except Exception:
            # the error page itself needs a working layout, so fall back to plain text
            return (f"<h1>Something went wrong</h1><p>Reference {ref}. "
                    f"The details are in instance/server.log.</p>"), 500

    @app.errorhandler(413)
    def too_large(_e):
        from flask import flash
        flash("That file is over the 25 MB limit. Compress it and try again.", "error")
        return redirect(request.referrer or "/"), 302

    if os.environ.get("BACKUP_DISABLED", "").lower() not in ("1", "true", "yes"):
        backups.start_scheduler(app)

    # ---------------------------------------------------------- first-run admin
    with app.app_context():
        if query("SELECT COUNT(*) c FROM users", one=True)["c"] == 0:
            email = os.environ.get("ADMIN_EMAIL", "admin@plannedrealestate.qa")
            pw = os.environ.get("ADMIN_PASSWORD", "")
            fallback = not pw
            pw = pw or secrets.token_urlsafe(12)
            try:
                auth.create_user("Administrator", email, pw, "admin")
            except sqlite3.IntegrityError:
                # Several workers start together and all see an empty table.
                # Whichever one got there first has made the account; the rest
                # would otherwise die here and take the server down with them.
                pass
            else:
                print(f"  Created first admin: {email} / {pw}")
                if fallback:
                    print("  That password was generated and is shown only "
                          "here. Set ADMIN_PASSWORD in the environment, or "
                          "sign in and change it now.")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
