"""Owners and partners directories, plus the admin area: users, settings, exports."""
import csv
import io
import os
import uuid
from datetime import datetime

from flask import (Blueprint, Response, current_app, flash, g, redirect,
                   render_template, request, send_file, url_for)
import backups
import excel_export
import performance
import whatsapp
from werkzeug.security import generate_password_hash

from auth import admin_required, create_user, login_required, manager_required
from db import (DEPARTMENTS, EMPLOYMENT, PARTNER_TYPES, execute, get_setting,
                log, now, query, set_setting)

contacts = Blueprint("contacts", __name__, url_prefix="/directory")
admin = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------- owners
@contacts.route("/owners")
@login_required
def owners():
    q = request.args.get("q", "").strip()
    sql = ("SELECT o.*, (SELECT COUNT(*) FROM properties p WHERE p.owner_id = o.id)"
           " AS prop_count FROM owners o")
    args = []
    if q:
        sql += " WHERE o.name LIKE ? OR o.phone LIKE ? OR o.company LIKE ?"
        args = [f"%{q}%"] * 3
    sql += " ORDER BY o.name"
    return render_template("contacts/owners.html", rows=query(sql, args), q=q)


@contacts.route("/owners/save", methods=("POST",))
@login_required
def save_owner():
    d = request.form
    oid = d.get("id")
    vals = (d.get("name", "").strip(), d.get("phone", "").strip(),
            d.get("email", "").strip(), d.get("company", "").strip(),
            d.get("notes", "").strip())
    if not vals[0]:
        flash("An owner needs a name.", "error")
    elif oid:
        execute("UPDATE owners SET name=?,phone=?,email=?,company=?,notes=? WHERE id=?",
                vals + (oid,))
        log(g.user["id"], "Updated owner", "owner", int(oid), vals[0])
        flash("Owner updated.", "ok")
    else:
        new_id = execute("INSERT INTO owners (name,phone,email,company,notes,created_at)"
                         " VALUES (?,?,?,?,?,?)", vals + (now(),))
        log(g.user["id"], "Added owner", "owner", new_id, vals[0])
        flash("Owner added.", "ok")
    return redirect(url_for("contacts.owners"))


@contacts.route("/owners/<int:oid>/delete", methods=("POST",))
@admin_required
def delete_owner(oid):
    execute("DELETE FROM owners WHERE id = ?", (oid,))
    log(g.user["id"], "Deleted owner", "owner", oid)
    return redirect(url_for("contacts.owners"))


# -------------------------------------------------------------- partners
@contacts.route("/partners")
@login_required
def partners():
    ptype = request.args.get("partner_type", "")
    sql, args = "SELECT * FROM partners", []
    if ptype:
        sql += " WHERE partner_type = ?"
        args.append(ptype)
    sql += " ORDER BY name"
    return render_template("contacts/partners.html", rows=query(sql, args),
                           partner_types=PARTNER_TYPES, ptype=ptype)


@contacts.route("/partners/save", methods=("POST",))
@login_required
def save_partner():
    d = request.form
    pid = d.get("id")
    vals = (d.get("name", "").strip(), d.get("partner_type"), d.get("phone", "").strip(),
            d.get("email", "").strip(), d.get("notes", "").strip())
    if not vals[0]:
        flash("A partner needs a name.", "error")
    elif pid:
        execute("UPDATE partners SET name=?,partner_type=?,phone=?,email=?,notes=?"
                " WHERE id=?", vals + (pid,))
        log(g.user["id"], "Updated partner", "partner", int(pid), vals[0])
        flash("Partner updated.", "ok")
    else:
        new_id = execute("INSERT INTO partners (name,partner_type,phone,email,notes,"
                         "created_at) VALUES (?,?,?,?,?,?)", vals + (now(),))
        log(g.user["id"], "Added partner", "partner", new_id, vals[0])
        flash("Partner added.", "ok")
    return redirect(url_for("contacts.partners"))


@contacts.route("/partners/<int:pid>/delete", methods=("POST",))
@admin_required
def delete_partner(pid):
    execute("DELETE FROM partners WHERE id = ?", (pid,))
    log(g.user["id"], "Deleted partner", "partner", pid)
    return redirect(url_for("contacts.partners"))


# ----------------------------------------------------------------- users
@admin.route("/users")
@login_required
def users():
    """Everyone can see who is who; only admins see the manage controls."""
    period = request.args.get("period", "this_month")
    if period not in dict(performance.RANGES):
        period = "this_month"
    rows = performance.team_stats(period)
    managers = query("SELECT id, name FROM users WHERE is_active = 1 ORDER BY name")
    return render_template("admin/users.html", rows=rows, period=period,
                           ranges=performance.RANGES, managers=managers,
                           employment=EMPLOYMENT, departments=DEPARTMENTS)


@admin.route("/users/<int:uid>")
@login_required
def profile(uid):
    person = query("SELECT u.*, m.name AS manager_name FROM users u"
                   " LEFT JOIN users m ON m.id = u.manager_id"
                   " WHERE u.id = ?", (uid,), one=True)
    if person is None:
        flash("That team member no longer exists.", "error")
        return redirect(url_for("admin.users"))

    period = request.args.get("period", "this_month")
    if period not in dict(performance.RANGES):
        period = "this_month"

    # figures are for the person themselves and for management only
    may_see_numbers = g.user["role"] in ("admin", "manager") or g.user["id"] == uid
    stats = performance.stats_for(uid, period) if may_see_numbers else None

    recent_deals = query(
        "SELECT d.*, p.title AS prop_title FROM deals d"
        " LEFT JOIN properties p ON p.id = d.property_id"
        " WHERE d.agent_id = ? AND d.status != 'Cancelled'"
        " ORDER BY COALESCE(d.closed_at, d.created_at) DESC LIMIT 8",
        (uid,)) if may_see_numbers else []

    listings = query(
        "SELECT id, ref, title, status, price, listing_type FROM properties"
        " WHERE agent_id = ? AND status IN ('Available','Reserved')"
        " ORDER BY id DESC LIMIT 8", (uid,))

    managers = query("SELECT id, name FROM users WHERE is_active = 1 AND id != ?"
                     " ORDER BY name", (uid,))

    return render_template("admin/profile.html", person=person, stats=stats,
                           period=period, ranges=performance.RANGES,
                           recent_deals=recent_deals, listings=listings,
                           managers=managers, employment=EMPLOYMENT,
                           departments=DEPARTMENTS,
                           can_edit=(g.user["role"] == "admin" or g.user["id"] == uid),
                           may_see_numbers=may_see_numbers)


@admin.route("/users/<int:uid>/photo", methods=("POST",))
@login_required
def user_photo(uid):
    """Admins can set anyone's photo; everyone else only their own."""
    if g.user["role"] != "admin" and g.user["id"] != uid:
        flash("You can only change your own photo.", "error")
        return redirect(url_for("admin.profile", uid=uid))

    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "avatars")
    os.makedirs(folder, exist_ok=True)
    old = query("SELECT photo FROM users WHERE id = ?", (uid,), one=True)

    if request.form.get("remove"):
        execute("UPDATE users SET photo = NULL WHERE id = ?", (uid,))
        if old and old["photo"]:
            try:
                os.remove(os.path.join(folder, old["photo"]))
            except OSError:
                pass
        log(g.user["id"], "Removed a profile photo", "user", uid)
        flash("Photo removed.", "ok")
        return redirect(url_for("admin.profile", uid=uid))

    fs = request.files.get("photo")
    if not fs or not fs.filename:
        flash("Choose an image first.", "error")
        return redirect(url_for("admin.profile", uid=uid))
    ext = fs.filename.rsplit(".", 1)[-1].lower() if "." in fs.filename else ""
    if ext not in ("png", "jpg", "jpeg", "webp"):
        flash("Use a PNG, JPG or WEBP image.", "error")
        return redirect(url_for("admin.profile", uid=uid))

    name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(folder, name)
    fs.save(path)

    # square it off and shrink, so a 5 MB phone photo does not become an avatar
    try:
        from PIL import Image
        img = Image.open(path)
        img = img.convert("RGB") if ext in ("jpg", "jpeg") else img.convert("RGBA")
        side = min(img.size)
        left, top = (img.width - side) // 2, (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize((320, 320))
        img.save(path)
    except Exception:
        pass                      # an unresized photo still works

    execute("UPDATE users SET photo = ? WHERE id = ?", (name, uid))
    if old and old["photo"]:
        try:
            os.remove(os.path.join(folder, old["photo"]))
        except OSError:
            pass
    log(g.user["id"], "Updated a profile photo", "user", uid)
    flash("Photo updated.", "ok")
    return redirect(url_for("admin.profile", uid=uid))


@admin.route("/users/<int:uid>/profile", methods=("POST",))
@login_required
def save_profile(uid):
    if g.user["role"] != "admin" and g.user["id"] != uid:
        flash("You can only edit your own profile.", "error")
        return redirect(url_for("admin.profile", uid=uid))
    d = request.form
    execute("UPDATE users SET job_title=?, department=?, joined_year=?, employment=?,"
            " languages=?, areas_covered=?, bio=?, phone=? WHERE id=?",
            (d.get("job_title", "").strip(), d.get("department", "").strip(),
             d.get("joined_year", "").strip(), d.get("employment", "").strip(),
             d.get("languages", "").strip(), d.get("areas_covered", "").strip(),
             d.get("bio", "").strip(), d.get("phone", "").strip(), uid))
    if g.user["role"] == "admin":
        mid = d.get("manager_id")
        execute("UPDATE users SET manager_id = ? WHERE id = ?",
                (int(mid) if mid and int(mid) != uid else None, uid))
    log(g.user["id"], "Updated a profile", "user", uid)
    flash("Profile saved.", "ok")
    return redirect(url_for("admin.profile", uid=uid))


@admin.route("/users/save", methods=("POST",))
@admin_required
def save_user():
    d = request.form
    uid = d.get("id")
    name, email = d.get("name", "").strip(), d.get("email", "").strip().lower()
    role = d.get("role", "agent")
    phone = d.get("phone", "").strip()
    password = d.get("password", "")
    if not name or not email:
        flash("Name and email are both required.", "error")
        return redirect(url_for("admin.users"))
    clash = query("SELECT id FROM users WHERE lower(email) = ? AND id != ?",
                  (email, uid or 0), one=True)
    if clash:
        flash("Another account already uses that email.", "error")
        return redirect(url_for("admin.users"))
    if uid:
        execute("UPDATE users SET name=?,email=?,phone=?,role=? WHERE id=?",
                (name, email, phone, role, uid))
        if password:
            execute("UPDATE users SET password_hash=? WHERE id=?",
                    (generate_password_hash(password), uid))
        log(g.user["id"], "Updated team member", "user", int(uid), name)
        flash("Team member updated.", "ok")
    else:
        if len(password) < 8:
            flash("Set a starting password of at least 8 characters.", "error")
            return redirect(url_for("admin.users"))
        new_id = create_user(name, email, password, role, phone)
        log(g.user["id"], "Added team member", "user", new_id, f"{name} ({role})")
        flash("Team member added.", "ok")
    return redirect(url_for("admin.users"))


@admin.route("/users/<int:uid>/toggle", methods=("POST",))
@admin_required
def toggle_user(uid):
    if uid == g.user["id"]:
        flash("You can't switch off your own account.", "error")
        return redirect(url_for("admin.users"))
    u = query("SELECT * FROM users WHERE id = ?", (uid,), one=True)
    execute("UPDATE users SET is_active = ? WHERE id = ?", (0 if u["is_active"] else 1, uid))
    log(g.user["id"], "Disabled account" if u["is_active"] else "Re-enabled account",
        "user", uid, u["name"])
    return redirect(url_for("admin.users"))


# -------------------------------------------------------------- settings
SETTING_KEYS = ("company_name", "tagline", "currency", "phone", "phone2", "email",
                "address", "po_box", "cr_number", "vat_percent", "commission_pct")


@admin.route("/settings", methods=("GET", "POST"))
@admin_required
def settings():
    if request.method == "POST":
        for k in SETTING_KEYS:
            if k in request.form:
                set_setting(k, request.form.get(k, "").strip())
        log(g.user["id"], "Updated company settings")
        flash("Settings saved.", "ok")
        return redirect(url_for("admin.settings"))

    s = {k: get_setting(k) for k in SETTING_KEYS}
    s.setdefault("commission_pct", "")
    templates = {k: get_setting(k) or v for k, v in whatsapp.DEFAULTS.items()}
    return render_template("admin/settings.html", s=s, templates=templates,
                           labels=whatsapp.LABELS,
                           placeholders=whatsapp.PLACEHOLDERS,
                           backup_list=backups.list_backups(current_app)[:10],
                           backup_age=backups.last_backup_age_hours(current_app),
                           backup_folder=backups.backup_folder(current_app),
                           backup_keep=backups.KEEP,
                           backup_custom=get_setting('backup_folder', ''),
                           backup_default=backups.default_folder(current_app))


@admin.route("/backup/folder", methods=("POST",))
@admin_required
def backup_folder_save():
    raw = request.form.get("backup_folder", "").strip()
    if not raw:
        set_setting("backup_folder", "")
        log(g.user["id"], "Reset the backup folder")
        flash("Backups will be kept in the instance folder on this computer.", "ok")
        return redirect(url_for("admin.settings") + "#backups")

    ok, result = backups.check_folder(raw)
    if not ok:
        flash(result, "error")
        return redirect(url_for("admin.settings") + "#backups")

    set_setting("backup_folder", result)
    path = backups.make_backup(current_app, "manual")
    log(g.user["id"], "Changed the backup folder", detail=result)
    if path:
        flash(f"Backups will now be saved to {result}. A test copy is there already.", "ok")
    else:
        flash(f"Backups will now be saved to {result}.", "ok")
    return redirect(url_for("admin.settings") + "#backups")


@admin.route("/backup/now", methods=("POST",))
@admin_required
def backup_now():
    path = backups.make_backup(current_app, "manual")
    if path:
        log(g.user["id"], "Made a backup", detail=os.path.basename(path))
        flash("Backup saved. Download it below and keep a copy off this computer.", "ok")
    else:
        flash("There was no database to back up.", "error")
    return redirect(url_for("admin.settings") + "#backups")


@admin.route("/backup/download")
@admin_required
def backup_download():
    """Everything, as one zip: the database plus all photos and documents."""
    full = request.args.get("uploads", "1") != "0"
    buf = backups.build_archive(current_app, include_uploads=full)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    kind = "full" if full else "data-only"
    log(g.user["id"], "Downloaded a backup", detail=kind)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"planned-crm-backup-{stamp}-{kind}.zip")


@admin.route("/backup/file/<path:name>")
@admin_required
def backup_file(name):
    """Download one of the automatic copies."""
    if "/" in name or "\\" in name or not name.endswith(".sqlite3"):
        flash("That backup couldn't be found.", "error")
        return redirect(url_for("admin.settings") + "#backups")
    folder = backups.backup_folder(current_app)
    path = os.path.realpath(os.path.join(folder, name))
    # second line of defence: the resolved path must sit inside the folder
    if os.path.commonpath([path, os.path.realpath(folder)]) != os.path.realpath(folder):
        flash("That backup couldn't be found.", "error")
        return redirect(url_for("admin.settings") + "#backups")
    if not os.path.exists(path):
        flash("That backup couldn't be found.", "error")
        return redirect(url_for("admin.settings") + "#backups")
    return send_file(path, as_attachment=True, download_name=name)


@admin.route("/settings/logo", methods=("POST",))
@admin_required
def upload_logo():
    """Replace the company logo from the browser, no file copying needed."""
    fs = request.files.get("logo")
    if not fs or not fs.filename:
        flash("Pick an image file first.", "error")
        return redirect(url_for("admin.settings"))

    ext = fs.filename.rsplit(".", 1)[-1].lower() if "." in fs.filename else ""
    if ext not in ("png", "jpg", "jpeg", "webp", "svg"):
        flash("Use a PNG, JPG, WEBP or SVG image for the logo.", "error")
        return redirect(url_for("admin.settings"))

    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img")
    os.makedirs(folder, exist_ok=True)
    # clear out any previous logo so only one can ever match
    for entry in os.listdir(folder):
        if entry.rsplit(".", 1)[0].lower() == "logo":
            os.remove(os.path.join(folder, entry))
    fs.save(os.path.join(folder, f"logo.{ext}"))

    log(g.user["id"], "Updated company logo", detail=fs.filename)
    flash("Logo updated. Refresh the page if you still see the old one.", "ok")
    return redirect(url_for("admin.settings"))


@admin.route("/settings/templates", methods=("POST",))
@admin_required
def save_templates():
    for key in whatsapp.DEFAULTS:
        set_setting(key, request.form.get(key, "").strip())
    log(g.user["id"], "Updated WhatsApp templates")
    flash("Message templates saved.", "ok")
    return redirect(url_for("admin.settings") + "#templates")


# --------------------------------------------------------------- exports
def _csv(rows, headers, filename):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow([r[h] if h in r.keys() else "" for h in headers])
    log(g.user["id"], "Exported data", detail=filename)
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@admin.route("/export/workbook.xlsx")
@manager_required
def export_workbook():
    """The full branded workbook: our listings, other listings, leads, deals."""
    properties = query(
        "SELECT p.*, o.name AS owner_name, u.name AS agent_name FROM properties p"
        " LEFT JOIN owners o ON o.id = p.owner_id"
        " LEFT JOIN users u ON u.id = p.agent_id"
        " ORDER BY CASE WHEN COALESCE(TRIM(p.building_no), '') = '' THEN 1 ELSE 0 END,"
        " p.area, p.building_no, CAST(COALESCE(p.unit_no, '') AS INTEGER),"
        " LENGTH(COALESCE(p.unit_no, '')), p.unit_no, p.id")
    leads = query(
        "SELECT l.*, u.name AS agent_name, pr.title AS prop_title FROM leads l"
        " LEFT JOIN users u ON u.id = l.agent_id"
        " LEFT JOIN properties pr ON pr.id = l.property_id ORDER BY l.id")
    deals = query(
        "SELECT d.*, pr.title AS prop_title, le.full_name AS lead_name,"
        " u.name AS agent_name FROM deals d"
        " LEFT JOIN properties pr ON pr.id = d.property_id"
        " LEFT JOIN leads le ON le.id = d.lead_id"
        " LEFT JOIN users u ON u.id = d.agent_id ORDER BY d.id")

    buf = excel_export.build_workbook(properties, leads, deals, g.user["name"])
    stamp = datetime.now().strftime("%Y-%m-%d")
    company = get_setting("company_name", "Planned Real Estate").replace(" ", "-").lower()
    log(g.user["id"], "Exported the Excel workbook",
        detail=f"{len(properties)} listings, {len(leads)} leads, {len(deals)} deals")
    return send_file(
        buf, as_attachment=True, download_name=f"{company}-export-{stamp}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@admin.route("/export/leads.csv")
@manager_required
def export_leads():
    rows = query("SELECT l.ref, l.full_name, l.phone, l.email, l.source, l.status,"
                 " l.budget, u.name AS agent, p.title AS property_of_interest,"
                 " l.notes, l.created_at, l.updated_at FROM leads l"
                 " LEFT JOIN users u ON u.id = l.agent_id"
                 " LEFT JOIN properties p ON p.id = l.property_id ORDER BY l.id")
    return _csv(rows, ["ref", "full_name", "phone", "email", "source", "status", "budget",
                       "agent", "property_of_interest", "notes", "created_at",
                       "updated_at"], "planned-leads.csv")


@admin.route("/export/properties.csv")
@manager_required
def export_properties():
    rows = query("SELECT p.ref, p.title, p.address, p.building_no, p.unit_no, p.area,"
                 " p.prop_type, p.listing_type,"
                 " p.status, p.price, p.size_sqm, p.bedrooms, p.bathrooms,"
                 " o.name AS owner, u.name AS agent, p.created_at FROM properties p"
                 " LEFT JOIN owners o ON o.id = p.owner_id"
                 " LEFT JOIN users u ON u.id = p.agent_id ORDER BY p.id")
    return _csv(rows, ["ref", "title", "address", "building_no", "unit_no", "area",
                       "prop_type", "listing_type",
                       "status", "price", "size_sqm", "bedrooms", "bathrooms", "owner",
                       "agent", "created_at"], "planned-properties.csv")


@admin.route("/export/deals.csv")
@manager_required
def export_deals():
    rows = query("SELECT d.ref, d.deal_type, d.status, d.value, d.commission_pct,"
                 " d.commission_amt, p.title AS property, l.full_name AS client,"
                 " u.name AS agent, d.closed_at, d.notes, d.created_at FROM deals d"
                 " LEFT JOIN properties p ON p.id = d.property_id"
                 " LEFT JOIN leads l ON l.id = d.lead_id"
                 " LEFT JOIN users u ON u.id = d.agent_id ORDER BY d.id")
    return _csv(rows, ["ref", "deal_type", "status", "value", "commission_pct",
                       "commission_amt", "property", "client", "agent", "closed_at",
                       "notes", "created_at"], "planned-deals.csv")


@admin.route("/export/owners.csv")
@manager_required
def export_owners():
    rows = query("SELECT name, phone, email, company, notes, created_at FROM owners"
                 " ORDER BY name")
    return _csv(rows, ["name", "phone", "email", "company", "notes", "created_at"],
                "planned-owners.csv")
