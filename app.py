"""Planned Real Estate — internal CRM.

Run locally:   python app.py
Run in prod:   gunicorn "app:create_app()"
"""
import os
from datetime import timedelta

import auth
import backups
import views_admin
import views_deals
import views_imports
import views_leads
import views_main
import views_properties
import whatsapp
from db import (LEAD_STAGES, TZ_OFFSET, close_db, get_setting, init_db,
                local_now, query, to_local)
from flask import Flask, g, redirect, request, session, url_for
from markupsafe import Markup, escape
from i18n import LANGS, current_lang, is_rtl, t

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


LOGO_EXTS = ("png", "jpg", "jpeg", "webp", "svg", "gif")


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
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "change-me-before-you-deploy"),
        DATABASE=os.environ.get("DATABASE_PATH",
                                os.path.join(BASE_DIR, "instance", "crm.sqlite3")),
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
        return dict(
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
            lang=current_lang(),
            langs=LANGS,
            rtl=is_rtl(),
            wa=wa_button,
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
            pw = os.environ.get("ADMIN_PASSWORD", "Planned@2026")
            auth.create_user("Administrator", email, pw, "admin")
            print(f"  Created first admin: {email} / {pw}  (change it after signing in)")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
