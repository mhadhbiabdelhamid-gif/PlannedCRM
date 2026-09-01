"""Property listings: filtered list, detail view, editing, media and documents."""
import os
import uuid

import areas
import maps

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, url_for)
from werkzeug.utils import secure_filename

from auth import (can, can_edit, can_publish, can_see_listing, is_admin,
                  login_required, published_only, sees_all)
from db import (LEASE_NOTICE_DAYS, LISTING_TYPES, PROP_STATUS, PROP_TYPES,
                STALE_DAYS, days_ago, execute, get_setting, local_today, log,
                next_ref, notify, now, paginate, query)

bp = Blueprint("properties", __name__, url_prefix="/properties")

IMG_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
DOC_EXT = {"pdf", "doc", "docx", "xls", "xlsx", "png", "jpg", "jpeg", "dwg", "txt"}


def _ext_ok(filename, allowed):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def _save(file_storage, kind):
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], kind)
    os.makedirs(folder, exist_ok=True)
    file_storage.save(os.path.join(folder, name))
    return name


@bp.route("/")
@login_required
def index():
    f = {k: request.args.get(k, "").strip() for k in
         ("q", "prop_type", "status", "listing_type", "area", "agent", "owner",
          "min_price", "max_price", "beds", "owned", "floor")}
    sql = ("SELECT p.*, u.name AS agent_name,"
           " o.name AS owner_name, o.photo AS owner_photo, o.company AS owner_company,"
           " pa.name AS partner_name, pa.photo AS partner_photo,"
           " (SELECT filename FROM property_images i WHERE i.property_id = p.id"
           "   ORDER BY is_cover DESC, id LIMIT 1) AS cover"
           " FROM properties p"
           " LEFT JOIN users u ON u.id = p.agent_id"
           " LEFT JOIN owners o ON o.id = p.owner_id"
           " LEFT JOIN partners pa ON pa.id = p.partner_id WHERE 1=1")
    sql += published_only("p")
    args = []
    if f["q"]:
        sql += (" AND (p.title LIKE ? OR p.address LIKE ? OR p.ref LIKE ?"
                " OR p.building_no LIKE ? OR p.unit_no LIKE ? OR p.floor_no LIKE ?"
                " OR p.extras LIKE ?)")
        args += [f"%{f['q']}%"] * 7
    for col in ("prop_type", "status", "listing_type"):
        if f[col]:
            sql += f" AND p.{col} = ?"
            args.append(f[col])
    if f["area"]:
        # A district shows up in listings under several spellings — English,
        # Arabic, or an alternate transliteration ("Lusail" / "لوسيل" /
        # "sadd"). A plain substring match only ever finds the one spelling
        # someone actually typed, so widen the search to every spelling on
        # record for that place. Unrecognised text (a custom location not in
        # areas.py) just falls back to the old plain substring match.
        spellings = areas.variants(f["area"]) or [f["area"]]
        sql += " AND (" + " OR ".join(["p.area LIKE ?"] * len(spellings)) + ")"
        args += [f"%{s}%" for s in spellings]
    if f["agent"]:
        sql += " AND p.agent_id = ?"
        args.append(f["agent"])
    if f["owner"]:
        sql += " AND p.owner_id = ?"
        args.append(f["owner"])
    if f["min_price"]:
        sql += " AND p.price >= ?"
        args.append(float(f["min_price"]))
    if f["max_price"]:
        sql += " AND p.price <= ?"
        args.append(float(f["max_price"]))
    if f["beds"]:
        # Exact match, so asking for 1 bedroom doesn't return 2- and 3-bed units.
        # "6+" is the one open-ended option, since large counts are rare.
        if f["beds"] == "6+":
            sql += " AND p.bedrooms >= 6"
        else:
            sql += " AND COALESCE(p.bedrooms, 0) = ?"
            args.append(int(f["beds"]))
    if f["floor"]:
        sql += " AND lower(TRIM(COALESCE(p.floor_no,''))) = lower(?)"
        args.append(f["floor"].strip())
    if f["owned"] in ("1", "0"):
        sql += " AND COALESCE(p.is_own, 0) = ?"
        args.append(int(f["owned"]))
    # Grouping by building is the default, so units in one tower read in order.
    # unit_no is text ("402", "12B", "G04"), and plain text sorting puts 1102
    # before 402 — so sort on the leading number first, then the text itself.
    # Within a building: floor, then flat, counting up from one. natkey()
    # (see db.natural_key) is what makes 2 come before 10 and keeps a lettered
    # unit like A-15 from jumping ahead of flat 1 — CAST used to read every
    # such label as zero, so they all landed at the top of the building.
    NATURAL_UNIT = "natkey(p.floor_no), natkey(p.unit_no)"
    # Our own stock first, then each owner or partner together, then anything
    # unattributed. Inside a company the units still read in flat order.
    COMPANY_ORDER = (
        "CASE WHEN COALESCE(p.is_own, 0) = 1 THEN 0"
        "     WHEN COALESCE(o.name, pa.name, p.import_source, '') != '' THEN 1"
        "     ELSE 2 END,"
        " LOWER(COALESCE(o.name, pa.name, p.import_source, '')),"
        " natkey(p.building_no), " + NATURAL_UNIT + ", p.id")
    SORTS = {
        "company": COMPANY_ORDER,
        "building": ("CASE WHEN COALESCE(TRIM(p.building_no), '') = '' THEN 1 ELSE 0 END,"
                     " p.area, natkey(p.building_no), " + NATURAL_UNIT + ", p.id"),
        "newest": "p.id DESC",
        "oldest": "p.id",
        "price_asc": "p.price, p.id",
        "price_desc": "p.price DESC, p.id",
        "area": "p.area, natkey(p.building_no), " + NATURAL_UNIT,
    }
    sort = request.args.get("sort", "company")
    if sort not in SORTS:
        sort = "company"
    sql += " ORDER BY " + SORTS[sort]

    company_name = get_setting("company_name", "Planned Real Estate")
    floors = [r["floor_no"] for r in query(
        "SELECT DISTINCT floor_no FROM properties"
        " WHERE COALESCE(TRIM(floor_no),'') != ''"
        " ORDER BY CAST(floor_no AS INTEGER), floor_no")]
    pager = paginate(sql, args, request.args.get("page", 1), per_page=60)
    rows = pager["rows"]

    # When grouped, hand the template ready-made blocks rather than making it
    # work out where one building ends and the next begins.
    groups = []
    if sort == "company":
        for row in rows:
            if row["is_own"]:
                key, label, kind = ("__ours__", company_name, "ours")
            else:
                name = (row["owner_name"] or row["partner_name"]
                        or row["import_source"] or "").strip()
                if name:
                    key = name.lower()
                    label = name
                    kind = "owner" if row["owner_name"] else (
                        "partner" if row["partner_name"] else "list")
                else:
                    key, label, kind = ("__none__", "No owner recorded", "none")
            if not groups or groups[-1]["key"] != key:
                groups.append({"key": key, "label": label, "kind": kind,
                               "photo": row["owner_photo"] or row["partner_photo"],
                               "company": row["owner_company"], "rows": []})
            groups[-1]["rows"].append(row)
    elif sort == "building":
        for row in rows:
            building = (row["building_no"] or "").strip()
            area = (row["area"] or "").strip()
            key = (building.lower(), area.lower())
            if not groups or groups[-1]["key"] != key:
                groups.append({"key": key, "label": building or "No building set",
                               "kind": "building", "photo": None,
                               "company": area, "rows": []})
            groups[-1]["rows"].append(row)

    agents = query("SELECT id, name FROM users WHERE is_active = 1 ORDER BY name")
    view = request.args.get("view", "grid")
    args_out = {k: v for k, v in f.items() if v}
    args_out.update({"view": view, "sort": sort})
    return render_template("properties/index.html", rows=rows, f=f, agents=agents,
                           prop_types=PROP_TYPES, statuses=PROP_STATUS,
                           listing_types=LISTING_TYPES, groups=groups, sort=sort,
                           view=view, pager=pager, args=args_out,
                           bulk_actions=BULK_ACTIONS, floors=floors,
                           stale_cutoff=days_ago(STALE_DAYS),
                           owners_list=query("SELECT id, name FROM owners ORDER BY name"),
                           partners_list=query("SELECT id, name FROM partners ORDER BY name"))


@bp.route("/<int:pid>")
@login_required
def detail(pid):
    p = query("SELECT p.*, u.name AS agent_name, o.name AS owner_name,"
              " o.phone AS owner_phone, o.email AS owner_email, o.photo AS owner_photo,"
              " o.company AS owner_company, pa.name AS partner_name,"
              " pa.photo AS partner_photo, pa.phone AS partner_phone,"
              " pa.email AS partner_email, pa.partner_type,"
              " r.name AS reviewed_name, s.name AS submitted_name"
              " FROM properties p LEFT JOIN users u ON u.id = p.agent_id"
              " LEFT JOIN owners o ON o.id = p.owner_id"
              " LEFT JOIN partners pa ON pa.id = p.partner_id"
              " LEFT JOIN users r ON r.id = p.reviewed_by"
              " LEFT JOIN users s ON s.id = p.submitted_by"
              " WHERE p.id = ?", (pid,), one=True)
    if p is None:
        flash("That property no longer exists.", "error")
        return redirect(url_for("properties.index"))
    if not can_see_listing(p):
        flash("That listing is still waiting to be published.", "error")
        return redirect(url_for("properties.index"))
    images = query("SELECT * FROM property_images WHERE property_id = ?"
                   " ORDER BY is_cover DESC, id", (pid,))
    docs = query("SELECT d.*, u.name AS uploader FROM documents d"
                 " LEFT JOIN users u ON u.id = d.uploaded_by"
                 " WHERE d.property_id = ? ORDER BY d.id DESC", (pid,))
    comments = query("SELECT c.*, u.name AS user_name, u.photo AS user_photo FROM comments c"
                     " LEFT JOIN users u ON u.id = c.user_id"
                     " WHERE c.entity_type='property' AND c.entity_id = ?"
                     " ORDER BY c.id DESC", (pid,))
    leads = query("SELECT l.*, u.name AS agent_name FROM leads l"
                  " LEFT JOIN users u ON u.id = l.agent_id"
                  " WHERE l.property_id = ? ORDER BY l.id DESC", (pid,))
    trail = query("SELECT a.*, u.name AS user_name FROM activity a"
                  " LEFT JOIN users u ON u.id = a.user_id"
                  " WHERE a.entity_type='property' AND a.entity_id = ?"
                  " ORDER BY a.id DESC LIMIT 25", (pid,))
    return render_template("properties/detail.html", p=p, images=images, docs=docs,
                           comments=comments, leads=leads, trail=trail,
                           editable=can_edit(p), cutoff=days_ago(STALE_DAYS))


@bp.route("/new", methods=("GET", "POST"))
@bp.route("/<int:pid>/edit", methods=("GET", "POST"))
@login_required
def form(pid=None):
    p = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True) if pid else None
    if pid and p is None:
        flash("That property no longer exists.", "error")
        return redirect(url_for("properties.index"))
    if p is not None and not can_edit(p):
        flash("That listing belongs to another agent. Ask an admin to reassign it.", "error")
        return redirect(url_for("properties.detail", pid=pid))

    if request.method == "POST":
        d = request.form
        ok, map_url = maps.normalise(d.get("map_url", ""))
        if not ok:
            flash(map_url, "error")
            return redirect(request.url)
        vals = (
            d.get("title", "").strip(), d.get("address", "").strip(),
            d.get("area", "").strip(), d.get("prop_type"), d.get("listing_type"),
            d.get("status"), float(d.get("price") or 0),
            float(d.get("size_sqm") or 0) or None,
            int(d.get("bedrooms") or 0) or None, int(d.get("bathrooms") or 0) or None,
            d.get("description", "").strip(), d.get("features", "").strip(),
            int(d["owner_id"]) if d.get("owner_id") else None,
            int(d["partner_id"]) if d.get("partner_id") else None,
            int(d["agent_id"]) if d.get("agent_id") else None,
            d.get("building_no", "").strip(), d.get("floor_no", "").strip(),
            d.get("unit_no", "").strip(),
            ", ".join(d.getlist("extras") + ([d.get("extras_other", "").strip()]
                      if d.get("extras_other", "").strip() else [])),
            map_url, 1 if d.get("is_own") else 0,
        )
        if not vals[0]:
            flash("A listing needs a title.", "error")
            return redirect(request.url)

        if p is None:
            ref = next_ref("PRE-P", "properties")
            # An admin publishes as they write. Anyone else is proposing a
            # listing, and it waits where the rest of the office can't see it.
            waiting = not can_publish()
            approval = "pending" if waiting else "approved"
            pid = execute(
                "INSERT INTO properties (title,address,area,prop_type,listing_type,status,"
                "price,size_sqm,bedrooms,bathrooms,description,features,owner_id,partner_id,"
                "agent_id,building_no,floor_no,unit_no,extras,map_url,is_own,ref,"
                "created_at,updated_at,last_verified,approval,submitted_by)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                vals + (ref, now(), now(), now(), approval, g.user["id"]))
            log(g.user["id"],
                "Submitted listing for approval" if waiting else "Added listing",
                "property", pid, f"{ref} — {vals[0]}")
            if waiting:
                for admin_row in query(
                        "SELECT id FROM users WHERE role = 'admin' AND is_active = 1"):
                    notify(admin_row["id"],
                           f"{g.user['name']} submitted {vals[0]} for approval",
                           url_for("properties.waiting"))
                flash("Sent for approval. It stays out of the listings until "
                      "an admin publishes it.", "ok")
            else:
                if vals[14] and vals[14] != g.user["id"]:
                    notify(vals[14], f"You were assigned the listing {vals[0]}",
                           url_for("properties.detail", pid=pid))
                flash("Listing added.", "ok")
        else:
            changes = []
            if float(p["price"] or 0) != vals[6]:
                changes.append(f"price {p['price']:,.0f} → {vals[6]:,.0f}")
            if p["status"] != vals[5]:
                changes.append(f"status {p['status']} → {vals[5]}")
            if p["agent_id"] != vals[14]:
                changes.append("agent reassigned")
            execute(
                "UPDATE properties SET title=?,address=?,area=?,prop_type=?,listing_type=?,"
                "status=?,price=?,size_sqm=?,bedrooms=?,bathrooms=?,description=?,features=?,"
                "owner_id=?,partner_id=?,agent_id=?,building_no=?,floor_no=?,unit_no=?,"
                "extras=?,map_url=?,is_own=?,"
                "updated_at=? WHERE id=?",
                vals + (now(), pid))
            log(g.user["id"], "Updated listing", "property", pid,
                "; ".join(changes) or "details edited")
            if vals[14] and vals[14] != p["agent_id"] and vals[14] != g.user["id"]:
                notify(vals[14], f"You were assigned the listing {vals[0]}",
                       url_for("properties.detail", pid=pid))
            if p["status"] != vals[5] and p["agent_id"] and p["agent_id"] != g.user["id"]:
                notify(p["agent_id"], f"{vals[0]} is now {vals[5]}",
                       url_for("properties.detail", pid=pid))
            flash("Listing saved.", "ok")

        for fs in request.files.getlist("images"):
            if fs and fs.filename and _ext_ok(fs.filename, IMG_EXT):
                execute("INSERT INTO property_images (property_id, filename, is_cover,"
                        " created_at) VALUES (?,?,0,?)", (pid, _save(fs, "images"), now()))
        return redirect(url_for("properties.detail", pid=pid))

    owners = query("SELECT id, name FROM owners ORDER BY name")
    partners = query("SELECT id, name, partner_type FROM partners ORDER BY name")
    agents = query("SELECT id, name FROM users WHERE is_active = 1 ORDER BY name")
    # Buildings already in use, so agents pick an existing spelling instead of
    # inventing a new one. Several buildings per area is the normal case.
    buildings = query(
        "SELECT building_no, area, COUNT(*) AS units FROM properties"
        " WHERE building_no IS NOT NULL AND TRIM(building_no) != ''"
        " GROUP BY building_no, area ORDER BY area, building_no")
    areas = query(
        "SELECT DISTINCT area FROM properties"
        " WHERE area IS NOT NULL AND TRIM(area) != '' ORDER BY area")
    from db import EXTRA_ROOMS
    return render_template("properties/form.html", p=p, owners=owners, agents=agents,
                           extra_rooms=EXTRA_ROOMS,
                           prop_types=PROP_TYPES, statuses=PROP_STATUS,
                           listing_types=LISTING_TYPES, buildings=buildings,
                           areas=areas, partners=partners)


@bp.route("/<int:pid>/documents", methods=("POST",))
@login_required
def add_document(pid):
    fs = request.files.get("document")
    if not fs or not fs.filename:
        flash("Pick a file to upload.", "error")
    elif not _ext_ok(fs.filename, DOC_EXT):
        flash("That file type isn't accepted. Use PDF, Word, Excel, an image or DWG.", "error")
    else:
        execute("INSERT INTO documents (property_id, filename, original_name, label,"
                " uploaded_by, created_at) VALUES (?,?,?,?,?,?)",
                (pid, _save(fs, "docs"), secure_filename(fs.filename),
                 request.form.get("label", "").strip(), g.user["id"], now()))
        log(g.user["id"], "Uploaded document", "property", pid, fs.filename)
        flash("Document stored.", "ok")
    return redirect(url_for("properties.detail", pid=pid))


@bp.route("/<int:pid>/images/<int:img_id>/cover", methods=("POST",))
@login_required
def set_cover(pid, img_id):
    execute("UPDATE property_images SET is_cover = 0 WHERE property_id = ?", (pid,))
    execute("UPDATE property_images SET is_cover = 1 WHERE id = ?", (img_id,))
    return redirect(url_for("properties.detail", pid=pid))


@bp.route("/<int:pid>/images/<int:img_id>/delete", methods=("POST",))
@login_required
def delete_image(pid, img_id):
    prop = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True)
    if prop is None or not can_edit(prop):
        flash("That listing belongs to another agent.", "error")
        return redirect(url_for("properties.index"))
    execute("DELETE FROM property_images WHERE id = ? AND property_id = ?", (img_id, pid))
    return redirect(url_for("properties.detail", pid=pid))


@bp.route("/<int:pid>/duplicate", methods=("POST",))
@login_required
def duplicate(pid):
    """Copy a listing so near-identical units don't have to be typed out again.

    Photos are shared rather than copied on disk — the same files, referenced
    twice — so ten units in one tower don't store ten sets of the same images.
    Leads, notes and history are never carried over; they belong to the original.
    """
    src = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True)
    if src is None:
        flash("That listing no longer exists.", "error")
        return redirect(url_for("properties.index"))

    title = request.form.get("title", "").strip() or f"{src['title']} (copy)"
    ref = next_ref("PRE-P", "properties")
    # The building stays the same; the flat number is the thing that changes.
    unit_no = request.form.get("unit_no", "").strip()

    new_id = execute(
        "INSERT INTO properties (title,address,area,prop_type,listing_type,status,price,"
        "size_sqm,bedrooms,bathrooms,description,features,owner_id,agent_id,building_no,"
        "floor_no,unit_no,extras,map_url,is_own,ref,created_at,updated_at,last_verified)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (title, src["address"], src["area"], src["prop_type"], src["listing_type"],
         request.form.get("status") or src["status"], src["price"], src["size_sqm"],
         src["bedrooms"], src["bathrooms"], src["description"], src["features"],
         src["owner_id"], src["agent_id"] or g.user["id"], src["building_no"],
         request.form.get("floor_no", "").strip() or src["floor_no"], unit_no,
         src["extras"], src["map_url"], src["is_own"], ref, now(), now(), now()))

    copied_images = 0
    if request.form.get("copy_images"):
        for im in query("SELECT filename, is_cover FROM property_images"
                        " WHERE property_id = ? ORDER BY is_cover DESC, id", (pid,)):
            execute("INSERT INTO property_images (property_id, filename, is_cover,"
                    " created_at) VALUES (?,?,?,?)",
                    (new_id, im["filename"], im["is_cover"], now()))
            copied_images += 1

    copied_docs = 0
    if request.form.get("copy_docs"):
        for d in query("SELECT filename, original_name, label FROM documents"
                       " WHERE property_id = ?", (pid,)):
            execute("INSERT INTO documents (property_id, filename, original_name, label,"
                    " uploaded_by, created_at) VALUES (?,?,?,?,?,?)",
                    (new_id, d["filename"], d["original_name"], d["label"],
                     g.user["id"], now()))
            copied_docs += 1

    log(g.user["id"], "Duplicated listing", "property", new_id,
        f"copied from {src['ref']} — {src['title']}")

    extras = []
    if copied_images:
        extras.append(f"{copied_images} photo{'' if copied_images == 1 else 's'}")
    if copied_docs:
        extras.append(f"{copied_docs} document{'' if copied_docs == 1 else 's'}")
    tail = f" with {' and '.join(extras)}" if extras else ""
    flash(f"Copy created as {ref}{tail}. Change what's different and save.", "ok")
    return redirect(url_for("properties.form", pid=new_id))


@bp.route("/<int:pid>/verify", methods=("POST",))
@login_required
def verify(pid):
    """Confirm a listing is still on the market, right now, as told to whoever
    checked. This is the only thing that resets the staleness clock — editing
    a field is not the same as someone having actually confirmed it."""
    p = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True)
    if p is None:
        flash("That listing no longer exists.", "error")
        return redirect(url_for("properties.index"))
    if not can_edit(p):
        flash("That listing belongs to another agent.", "error")
        return redirect(url_for("properties.detail", pid=pid))
    execute("UPDATE properties SET last_verified = ? WHERE id = ?", (now(), pid))
    log(g.user["id"], "Verified listing", "property", pid, p["title"])
    flash("Marked as verified today.", "ok")
    back = request.form.get("back")
    return redirect(back if back and back.startswith("/") else url_for("properties.detail", pid=pid))


@bp.route("/waiting")
@login_required
def waiting():
    """Listings an agent or manager has proposed but nobody has published.

    Admins see everything here and act on it. Everyone else sees only their
    own, so they can follow what they sent and fix anything sent back.
    """
    sql = ("SELECT p.*, u.name AS agent_name, s.name AS submitted_name,"
           " r.name AS reviewed_name,"
           " (SELECT filename FROM property_images i WHERE i.property_id = p.id"
           "   ORDER BY is_cover DESC, id LIMIT 1) AS cover"
           " FROM properties p"
           " LEFT JOIN users u ON u.id = p.agent_id"
           " LEFT JOIN users s ON s.id = p.submitted_by"
           " LEFT JOIN users r ON r.id = p.reviewed_by"
           " WHERE COALESCE(p.approval, 'approved') IN ('pending', 'rejected')")
    args = []
    if not sees_all():
        sql += " AND p.submitted_by = ?"
        args.append(g.user["id"])
    # Sent-back ones first: someone is waiting on those to be corrected.
    sql += " ORDER BY CASE p.approval WHEN 'rejected' THEN 0 ELSE 1 END, p.id"

    pager = paginate(sql, args, request.args.get("page", 1), per_page=60)
    return render_template("properties/waiting.html", rows=pager["rows"],
                           pager=pager, args={}, can_publish=can_publish())


@bp.route("/<int:pid>/approve", methods=("POST",))
@login_required
def approve(pid):
    """Publish a waiting listing so the rest of the office can see it."""
    if not can_publish():
        flash("Only an admin can publish a listing.", "error")
        return redirect(url_for("properties.waiting"))
    p = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True)
    if p is None:
        flash("That listing no longer exists.", "error")
        return redirect(url_for("properties.waiting"))

    execute("UPDATE properties SET approval='approved', reviewed_by=?,"
            " reviewed_at=?, review_note=NULL, last_verified=?, updated_at=?"
            " WHERE id=?", (g.user["id"], now(), now(), now(), pid))
    log(g.user["id"], "Published listing", "property", pid, p["title"])
    if p["submitted_by"] and p["submitted_by"] != g.user["id"]:
        notify(p["submitted_by"], f"{p['title']} is now published",
               url_for("properties.detail", pid=pid))
    flash(f"{p['title']} is published.", "ok")
    return redirect(url_for("properties.waiting"))


@bp.route("/<int:pid>/reject", methods=("POST",))
@login_required
def reject(pid):
    """Send a listing back to whoever wrote it, with a note saying why."""
    if not can_publish():
        flash("Only an admin can send a listing back.", "error")
        return redirect(url_for("properties.waiting"))
    p = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True)
    if p is None:
        flash("That listing no longer exists.", "error")
        return redirect(url_for("properties.waiting"))

    note = request.form.get("note", "").strip()
    if not note:
        flash("Say what needs fixing — that note is the whole point of "
              "sending it back.", "error")
        return redirect(url_for("properties.waiting"))

    execute("UPDATE properties SET approval='rejected', reviewed_by=?,"
            " reviewed_at=?, review_note=?, updated_at=? WHERE id=?",
            (g.user["id"], now(), note, now(), pid))
    log(g.user["id"], "Sent listing back", "property", pid,
        f"{p['title']} — {note[:80]}")
    author = query("SELECT name FROM users WHERE id = ?",
                   (p["submitted_by"],), one=True) if p["submitted_by"] else None
    if p["submitted_by"]:
        notify(p["submitted_by"],
               f"{p['title']} was sent back: {note[:60]}",
               url_for("properties.detail", pid=pid))
    flash(f"{p['title']} went back to {author['name'] if author else 'its author'}.",
          "ok")
    return redirect(url_for("properties.waiting"))


@bp.route("/<int:pid>/resubmit", methods=("POST",))
@login_required
def resubmit(pid):
    """Put a corrected listing back in front of the admin."""
    p = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True)
    if p is None:
        flash("That listing no longer exists.", "error")
        return redirect(url_for("properties.waiting"))
    if not can_edit(p) and p["submitted_by"] != g.user["id"]:
        flash("That listing belongs to another agent.", "error")
        return redirect(url_for("properties.waiting"))
    if (p["approval"] or "approved") != "rejected":
        flash("That listing isn't waiting to be corrected.", "error")
        return redirect(url_for("properties.waiting"))

    execute("UPDATE properties SET approval='pending', review_note=NULL,"
            " updated_at=? WHERE id=?", (now(), pid))
    log(g.user["id"], "Resubmitted listing", "property", pid, p["title"])
    for admin_row in query(
            "SELECT id FROM users WHERE role = 'admin' AND is_active = 1"):
        notify(admin_row["id"],
               f"{g.user['name']} resubmitted {p['title']}",
               url_for("properties.waiting"))
    flash("Sent back for approval.", "ok")
    return redirect(url_for("properties.waiting"))


@bp.route("/stale")
@login_required
def stale():
    """Active listings nobody has confirmed are still on the market in a
    while — the office-admin screen for chasing agents to re-check stock.
    Sold and rented units are done, so they're left out of this list."""
    from auth import sees_all
    cutoff = days_ago(STALE_DAYS)
    sql = ("SELECT p.*, u.name AS agent_name,"
           " (SELECT filename FROM property_images i WHERE i.property_id = p.id"
           "   ORDER BY is_cover DESC, id LIMIT 1) AS cover"
           " FROM properties p LEFT JOIN users u ON u.id = p.agent_id"
           " WHERE p.status IN ('Available','Reserved')"
           # A listing nobody has published yet isn't stock to chase up.
           + published_only("p") +
           " AND (p.last_verified IS NULL OR p.last_verified < ?)")
    args = [cutoff]
    if not sees_all():
        sql += " AND (p.agent_id = ? OR p.agent_id IS NULL)"
        args.append(g.user["id"])
    sql += " ORDER BY (p.last_verified IS NULL) DESC, p.last_verified, p.id"

    pager = paginate(sql, args, request.args.get("page", 1), per_page=60)
    return render_template("properties/stale.html", rows=pager["rows"], pager=pager,
                           args={}, cutoff=cutoff, stale_days=STALE_DAYS)


@bp.route("/leases")
@login_required
def leases_ending():
    """Tenancies that have run out, or are about to.

    The counterpart to /stale: that screen chases listings nobody has
    confirmed, this one catches units nobody has noticed are free. Everything
    on it needs a person to decide, so nothing is changed automatically.
    """
    import leases
    rows = leases.ending()
    if not sees_all():
        rows = [r for r in rows
                if g.user["id"] in (r["prop_agent_id"], r["agent_id"])]
    today = local_today()
    items = [{"deal": r, "left": leases.days_left(r["lease_end"], today)}
             for r in rows]
    return render_template("properties/leases.html", items=items,
                           notice_days=LEASE_NOTICE_DAYS)


@bp.route("/<int:pid>/available", methods=("POST",))
@login_required
def mark_available(pid):
    """Put a unit back on the market once its tenancy is over.

    Reached from the leases screen, which is the moment someone has actually
    decided the tenant has gone. The lease is marked dealt with at the same
    time, or the reminder would keep coming back about a unit already relisted.
    """
    import leases
    p = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True)
    if p is None:
        flash("That property no longer exists.", "error")
        return redirect(url_for("properties.leases_ending"))
    if not can_edit(p):
        flash("That listing belongs to another agent. Ask an admin to reassign it.",
              "error")
        return redirect(url_for("properties.leases_ending"))

    deal_id = request.form.get("deal_id", type=int)
    if deal_id:
        leases.resolve(deal_id)

    if p["status"] != "Available":
        execute("UPDATE properties SET status = 'Available', last_verified = ?,"
                " updated_at = ? WHERE id = ?", (now(), now(), pid))
        log(g.user["id"], "Updated listing", "property", pid,
            f"status {p['status']} → Available (lease ended)")
        if p["agent_id"] and p["agent_id"] != g.user["id"]:
            notify(p["agent_id"], f"{p['title']} is available again",
                   url_for("properties.detail", pid=pid))
        flash("Back on the market.", "ok")
    else:
        flash("That listing was already available. The reminder is cleared.", "ok")
    return redirect(url_for("properties.leases_ending"))


@bp.route("/<int:pid>/comment", methods=("POST",))
@login_required
def comment(pid):
    body = request.form.get("body", "").strip()
    if body:
        execute("INSERT INTO comments (entity_type, entity_id, user_id, body, created_at)"
                " VALUES ('property',?,?,?,?)", (pid, g.user["id"], body, now()))
        p = query("SELECT title, agent_id FROM properties WHERE id = ?", (pid,), one=True)
        log(g.user["id"], "Commented on listing", "property", pid, body[:80])
        if p and p["agent_id"] and p["agent_id"] != g.user["id"]:
            notify(p["agent_id"], f"{g.user['name']} commented on {p['title']}",
                   url_for("properties.detail", pid=pid))
    return redirect(url_for("properties.detail", pid=pid) + "#comments")


BULK_ACTIONS = {
    "status": "Change status",
    "agent": "Assign an agent",
    "owner": "Set the owner",
    "partner": "Set the partner",
    "listing_type": "Change sale or rent",
    "ours": "Mark as our own stock",
    "third_party": "Mark as third-party",
    "verify": "Mark as verified today",
    "delete": "Delete",
}


@bp.route("/bulk", methods=("POST",))
@login_required
def bulk():
    """Apply one change to several listings at once.

    Every listing is still checked individually, so an agent acting on a
    selection that includes a colleague's listing changes only their own.
    """
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    action = request.form.get("action", "")
    back = request.form.get("back") or url_for("properties.index")

    if not ids:
        flash("Nothing was selected.", "error")
        return redirect(back)
    if action not in BULK_ACTIONS:
        flash("Pick what to do with the selected listings.", "error")
        return redirect(back)

    rows = query("SELECT * FROM properties WHERE id IN (%s)"
                 % ",".join("?" * len(ids)), ids)
    allowed = [r for r in rows if can_edit(r)]
    blocked = len(rows) - len(allowed)

    if action == "delete":
        if not can("delete"):
            flash("You don't have access to delete listings.", "error")
            return redirect(back)
        # a bulk delete is the easiest way to lose a lot at once
        try:
            import backups
            backups.make_backup(current_app, "before-bulk-delete")
        except Exception:
            pass
        for r in allowed:
            execute("DELETE FROM properties WHERE id = ?", (r["id"],))
        log(g.user["id"], "Deleted listings in bulk",
            detail=f"{len(allowed)} listings: "
                   + ", ".join(x["ref"] or str(x["id"]) for x in allowed[:10])
                   + (" …" if len(allowed) > 10 else ""))
        flash(f"{len(allowed)} listing{'' if len(allowed) == 1 else 's'} deleted. "
              "A backup was taken first, so this can be undone from Settings.", "ok")
        return redirect(back)

    value = request.form.get("value", "").strip()
    field, new_value, label = None, None, ""

    if action == "status" and value in PROP_STATUS:
        field, new_value, label = "status", value, f"status → {value}"
    elif action == "listing_type" and value in LISTING_TYPES:
        field, new_value, label = "listing_type", value, f"type → {value}"
    elif action == "agent":
        field = "agent_id"
        new_value = int(value) if value.isdigit() else None
        who = query("SELECT name FROM users WHERE id = ?", (new_value,), one=True) \
            if new_value else None
        label = f"agent → {who['name'] if who else 'unassigned'}"
    elif action == "owner":
        field = "owner_id"
        new_value = int(value) if value.isdigit() else None
        label = "owner changed"
    elif action == "partner":
        field = "partner_id"
        new_value = int(value) if value.isdigit() else None
        label = "partner changed"
    elif action == "ours":
        field, new_value, label = "is_own", 1, "marked as our own stock"
    elif action == "third_party":
        field, new_value, label = "is_own", 0, "marked as third-party"
    elif action == "verify":
        field, new_value, label = "last_verified", now(), "marked as verified today"

    if field is None:
        flash("That change needs a value choosing.", "error")
        return redirect(back)

    for r in allowed:
        execute(f"UPDATE properties SET {field} = ?, updated_at = ? WHERE id = ?",
                (new_value, now(), r["id"]))
        if field == "agent_id" and new_value and new_value != g.user["id"]:
            notify(new_value, f"You were assigned {r['title']}",
                   url_for("properties.detail", pid=r["id"]))

    log(g.user["id"], "Edited listings in bulk",
        detail=f"{len(allowed)} listings: {label}")

    message = f"{len(allowed)} listing{'' if len(allowed) == 1 else 's'} updated."
    if blocked:
        message += (f" {blocked} skipped — {'it belongs' if blocked == 1 else 'they belong'}"
                    " to another agent.")
    flash(message, "ok")
    return redirect(back)


@bp.route("/<int:pid>/delete", methods=("POST",))
@login_required
def delete(pid):
    if not can("delete"):
        flash("You don't have access to delete listings.", "error")
        return redirect(url_for("properties.detail", pid=pid))
    p = query("SELECT title FROM properties WHERE id = ?", (pid,), one=True)
    execute("DELETE FROM properties WHERE id = ?", (pid,))
    log(g.user["id"], "Deleted listing", "property", pid, p["title"] if p else "")
    flash("Listing deleted.", "ok")
    return redirect(url_for("properties.index"))
