"""Bringing partner availability lists into the CRM.

Three steps, so nothing is written until someone has looked at it:
  1. upload the file
  2. check what was detected, correct any column that was guessed wrong
  3. import — matching units are updated, new ones are added
"""
import json
import os
import re
import uuid

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, url_for)

import importer
import normalize
from auth import admin_required, login_required, requires
from db import (IMPORT_MODES, LISTING_TYPES, PROP_STATUS, PROP_TYPES, execute,
                log, next_ref, now, query)

bp = Blueprint("imports", __name__, url_prefix="/import")


def known_buildings():
    """Building -> district, learned from what is already in the CRM.

    Once anyone has saved 'Retaj La Plage' with its district, every later
    list from any partner that mentions that building gets the same district
    without asking again. The most common answer wins if listings disagree."""
    rows = query("SELECT lower(TRIM(building_no)) AS b, area, COUNT(*) AS n"
                 " FROM properties WHERE COALESCE(TRIM(building_no),'') != ''"
                 " AND COALESCE(TRIM(area),'') != '' GROUP BY 1, 2 ORDER BY n")
    known = {}
    for r in rows:
        known[r["b"]] = normalize.find_area(r["area"]) or r["area"]
    return known


def _area_names():
    import areas
    return areas.all_names()


def _mapping_lists(mapping):
    return {k: importer.as_columns(v) for k, v in mapping.items()}

MAX_PREVIEW = 12


def _folder():
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "imports")
    os.makedirs(folder, exist_ok=True)
    return folder


def _path(token):
    """Tokens are generated here, never taken from the browser unchecked."""
    safe = os.path.basename(token)
    if not safe.endswith(".xlsx"):
        return None
    path = os.path.join(_folder(), safe)
    return path if os.path.exists(path) else None


@bp.route("/", methods=("GET", "POST"))
@requires("import")
def upload():
    if request.method == "POST":
        fs = request.files.get("workbook")
        if not fs or not fs.filename:
            flash("Choose a spreadsheet to upload.", "error")
            return redirect(request.url)
        if not fs.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("That needs to be an Excel file ending in .xlsx. If the partner sent "
                  "an older .xls, open it in Excel and use Save As to make an .xlsx.",
                  "error")
            return redirect(request.url)

        token = f"{uuid.uuid4().hex}.xlsx"
        fs.save(os.path.join(_folder(), token))
        return redirect(url_for("imports.review", token=token,
                                name=fs.filename))

    recent = query("SELECT a.*, u.name AS user_name FROM activity a"
                   " LEFT JOIN users u ON u.id = a.user_id"
                   " WHERE a.action LIKE 'Imported%' ORDER BY a.id DESC LIMIT 8")
    return render_template("imports/upload.html", recent=recent)


@bp.route("/review/<token>")
@requires("import")
def review(token):
    path = _path(token)
    if not path:
        flash("That upload has expired. Please upload the file again.", "error")
        return redirect(url_for("imports.upload"))

    wb = importer.open_workbook(path)
    sheets = []
    known = known_buildings()
    file_area = normalize.find_area(request.args.get("name", "")) or ""
    for name in wb.sheetnames:
        ws = wb[name]
        if ws.max_row < 2:
            continue
        # A sheet is usually one table, but a partner sometimes pastes two
        # buildings' lists side by side instead of stacking them — read
        # each as its own table so the second one isn't dropped or blended
        # into the first (see importer.find_column_blocks).
        infos = importer.read_sheet_blocks(ws)
        listings = []
        banner_labels = []
        primary = infos[0]
        sheet_area = primary.get("area_hint") or ""
        if not sheet_area and "area" not in primary["mapping"]:
            sheet_area = file_area
        for info in infos:
            block_defaults = {"listing_type": "Rent", "known_buildings": known,
                              "building_no": primary.get("building", ""),
                              "area": sheet_area, "file_area": file_area}
            # A block's own section heading ('Studio', 'One Bedroom') fills in
            # the bedroom count when no column in that block gives one — see
            # importer.guess_banner_bedrooms. Only that block's rows get it;
            # a heading over one table never leaks into another table's rows.
            if info.get("banner_bedrooms") is not None:
                block_defaults["bedrooms"] = info["banner_bedrooms"]
                if info["banner_bedrooms_label"] not in banner_labels:
                    banner_labels.append(info["banner_bedrooms_label"])
            listings += importer.extract(info, info["mapping"], block_defaults)
        flagged = [l for l in listings if l.get("issues")]
        summary = {}
        for l in flagged:
            for issue in l["issues"]:
                summary[issue] = summary.get(issue, 0) + 1
        sheets.append({
            "flagged": len(flagged),
            "issue_summary": ", ".join(f"{n} {k}" for k, n in
                                       sorted(summary.items(), key=lambda x: -x[1])),
            "name": name,
            "header_row": primary["header_row"],
            "headers": primary["headers"],
            "mapping": _mapping_lists(primary["mapping"]),
            "context": primary["context"],
            "building": primary.get("building", ""),
            "area": sheet_area,
            "count": len(listings),
            "preview": listings[:MAX_PREVIEW],
            "total_rows": sum(len(i["rows"]) for i in infos),
            "extra_tables": len(infos) - 1,
            "banner_labels": banner_labels,
        })

    owners = query("SELECT id, name FROM owners ORDER BY name")
    partners = query("SELECT id, name FROM partners ORDER BY name")
    agents = query("SELECT id, name FROM users WHERE is_active = 1 ORDER BY name")
    sources = [r["import_source"] for r in query(
        "SELECT import_source, COUNT(*) n FROM properties"
        " WHERE COALESCE(import_source,'') != '' GROUP BY import_source"
        " ORDER BY MAX(imported_at) DESC")]
    areas = [r["area"] for r in query(
        "SELECT DISTINCT area FROM properties WHERE COALESCE(TRIM(area),'') != ''"
        " ORDER BY area")]

    # a sensible default label: the filename with dates and noise stripped out
    raw_name = request.args.get("name", "spreadsheet")
    suggested_source = re.sub(r"[-_]?\d{1,4}[-_.]\d{1,2}[-_.]\d{1,4}", "",
                              os.path.splitext(raw_name)[0])
    suggested_source = re.sub(r"[-_]+", " ", suggested_source)
    suggested_source = re.sub(r"\s{2,}", " ", suggested_source).strip(" -_") or raw_name

    return render_template(
        "imports/review.html", token=token, sheets=sheets,
        filename=raw_name,
        fields=importer.FIELDS, owners=owners, partners=partners, agents=agents,
        areas=sorted(set(areas) | set(_area_names())),
        modes=IMPORT_MODES,
        sources=sources, suggested_source=suggested_source,
        prop_types=PROP_TYPES, statuses=PROP_STATUS, listing_types=LISTING_TYPES)


@bp.route("/commit/<token>", methods=("POST",))
@requires("import")
def commit(token):
    path = _path(token)
    if not path:
        flash("That upload has expired. Please upload the file again.", "error")
        return redirect(url_for("imports.upload"))

    d = request.form
    wb = importer.open_workbook(path)
    added = updated = skipped = 0
    touched_sheets = []

    # A label groups every import from the same partner, so next month's list
    # can be compared against what that partner gave us last time.
    source = d.get("source", "").strip()[:120]
    mode = d.get("mode", "update")
    if mode not in dict(IMPORT_MODES):
        mode = "update"
    overwrite_existing = mode in ("update", "replace")

    known = known_buildings()
    file_area = normalize.find_area(d.get("filename", "")) or ""
    seen_keys = set()          # (building, unit) present in the file just read
    stamp = now()
    rows_read = failed = 0
    # everything needed to put the database back as it was
    undo = {"inserted": [], "updated": [], "deleted": []}

    def snapshot(row):
        return {k: row[k] for k in row.keys()}

    for name in wb.sheetnames:
        if not d.get(f"use__{name}"):
            continue
        ws = wb[name]
        header_row = int(d.get(f"header__{name}") or 0) or None
        # As in review(): a sheet can hold more than one table side by side.
        # The reviewer only ever sees and corrects the first table's column
        # mapping on screen, so that override applies to the first table
        # only — later tables keep their own auto-detected mapping, since
        # applying a first-table override to a second table's differently
        # positioned columns would scramble it rather than fix it.
        infos = importer.read_sheet_blocks(ws, header_row=header_row)

        # whatever the reviewer chose on screen wins over the guess
        mapping = {}
        for field, _label in importer.FIELDS:
            chosen = importer.as_columns(d.getlist(f"map__{name}__{field}"))
            if chosen:
                mapping[field] = chosen

        defaults = {
            "building_no": d.get(f"building__{name}", "").strip(),
            "area": d.get(f"area__{name}", "").strip(),
            "known_buildings": known,
            "file_area": file_area,
            "listing_type": d.get("listing_type", "Rent"),
            "prop_type": d.get("prop_type", "Apartment"),
            "status": d.get("status", "Available"),
        }
        listings = []
        for i, info in enumerate(infos):
            use_mapping = mapping if i == 0 and mapping else info["mapping"]
            # As in review(): a block's own section heading ('Studio', 'One
            # Bedroom') fills in the bedroom count for that block's rows only,
            # when nothing in the row itself gives one.
            block_defaults = defaults
            if info.get("banner_bedrooms") is not None:
                block_defaults = dict(defaults, bedrooms=info["banner_bedrooms"])
            listings += importer.extract(
                info, use_mapping, block_defaults,
                fill_down=bool(d.get(f"filldown__{name}")),
                fill_numbers=bool(d.get(f"fillnumbers__{name}")))

        owner_id = int(d["owner_id"]) if d.get("owner_id") else None
        partner_id = int(d["partner_id"]) if d.get("partner_id") else None
        agent_id = int(d["agent_id"]) if d.get("agent_id") else None
        overwrite = overwrite_existing
        rows_read += len(listings)

        for item in listings:
            building = item["building_no"] or defaults["building_no"]
            unit = item["unit_no"]
            if not building and not unit:
                failed += 1
                continue

            existing = None
            if building and unit:
                existing = query(
                    "SELECT * FROM properties WHERE lower(TRIM(COALESCE(building_no,'')))"
                    " = lower(?) AND lower(TRIM(COALESCE(unit_no,''))) = lower(?)",
                    (building.strip(), unit.strip()), one=True)

            if existing and not overwrite:
                skipped += 1
                continue
            if mode == "preview":
                if existing:
                    updated += 1
                else:
                    added += 1
                seen_keys.add((building.strip().lower(), unit.strip().lower()))
                continue

            seen_keys.add((building.strip().lower(), unit.strip().lower()))

            values = (
                item["title"], item.get("address", ""), item["area"] or defaults["area"],
                item["prop_type"], item["listing_type"], item["status"],
                item["price"] or 0, item["size_sqm"], item["bedrooms"],
                item["bathrooms"], item["description"], item["features"],
                owner_id, agent_id, building, item.get("floor_no", ""), unit,
                item.get("extras", ""), item["map_url"], 0, source, stamp,
                item.get("furnishing"), item.get("view"), item.get("balcony"),
                item.get("bills"), partner_id,
            )

            if existing:
                undo["updated"].append(snapshot(existing))
                execute(
                    "UPDATE properties SET title=?,address=COALESCE(NULLIF(?,''),address),"
                    "area=COALESCE(NULLIF(?,''),area),prop_type=?,"
                    "listing_type=?,status=?,price=?,size_sqm=?,bedrooms=?,bathrooms=?,"
                    "description=?,features=?,owner_id=COALESCE(?, owner_id),"
                    "agent_id=COALESCE(?, agent_id),building_no=?,floor_no=?,"
                    "unit_no=?,extras=?,map_url=?,is_own=?,import_source=?,"
                    "imported_at=?,furnishing=COALESCE(?,furnishing),view=COALESCE(?,view),"
                    "balcony=COALESCE(?,balcony),bills=COALESCE(?,bills),"
                    "partner_id=COALESCE(?, partner_id),updated_at=? WHERE id=?",
                    values + (stamp, existing["id"]))
                updated += 1
            else:
                new_id = execute(
                    "INSERT INTO properties (title,address,area,prop_type,listing_type,"
                    "status,price,size_sqm,bedrooms,bathrooms,description,features,"
                    "owner_id,agent_id,building_no,floor_no,unit_no,extras,map_url,"
                    "is_own,import_source,imported_at,furnishing,view,balcony,bills,"
                    "partner_id,ref,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values + (next_ref("PRE-P", "properties"), stamp, stamp))
                undo["inserted"].append(new_id)
                added += 1

        touched_sheets.append(name)

    if not touched_sheets:
        flash("No sheets were ticked, so nothing was imported.", "error")
        return redirect(url_for("imports.review", token=token))

    # Replace mode removes everything this partner previously gave us that is
    # not in the new file. Same safety rule as the sweep below: only listings
    # imported under this label, never anything typed in by hand.
    missing_action = d.get("missing", "")
    gone = 0
    if mode == "replace" and source:
        for row in query(
                "SELECT * FROM properties WHERE import_source = ?"
                " AND COALESCE(is_own, 0) = 0 AND COALESCE(imported_at, '') < ?",
                (source, stamp)):
            key = ((row["building_no"] or "").strip().lower(),
                   (row["unit_no"] or "").strip().lower())
            if key in seen_keys:
                continue
            undo["deleted"].append(snapshot(row))
            execute("DELETE FROM properties WHERE id = ?", (row["id"],))
            gone += 1
        missing_action = ""      # replace already dealt with them

    if missing_action and source and mode != "preview":
        candidates = query(
            "SELECT id, building_no, unit_no FROM properties"
            " WHERE import_source = ? AND COALESCE(is_own, 0) = 0"
            " AND COALESCE(imported_at, '') < ?", (source, stamp))
        for row in candidates:
            key = ((row["building_no"] or "").strip().lower(),
                   (row["unit_no"] or "").strip().lower())
            if key in seen_keys:
                continue
            if missing_action == "delete":
                execute("DELETE FROM properties WHERE id = ?", (row["id"],))
            else:
                execute("UPDATE properties SET status = ?, updated_at = ? WHERE id = ?",
                        (missing_action, stamp, row["id"]))
            gone += 1

    import_id = execute(
        "INSERT INTO imports (user_id, filename, source, mode, sheets, rows_read,"
        " added, updated, skipped, removed, failed, status, undo_data, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (g.user["id"], d.get("filename", "")[:200], source, mode,
         ", ".join(touched_sheets), rows_read, added, updated, skipped, gone, failed,
         "preview" if mode == "preview" else "complete",
         json.dumps(undo) if (undo["inserted"] or undo["updated"] or undo["deleted"])
         else None, stamp))

    if mode == "preview":
        flash(f"Preview only — nothing was saved. {rows_read} rows read: "
              f"{added} would be added, {updated} updated"
              + (f", {failed} unusable" if failed else "") + ".", "ok")
        return redirect(url_for("imports.history"))

    log(g.user["id"], "Imported listings", detail=(
        f"{added} added, {updated} updated, {skipped} skipped"
        + (f", {gone} no longer listed" if gone else "")
        + f" — {source or 'unlabelled'} ({', '.join(touched_sheets)})"))

    try:
        os.remove(path)
    except OSError:
        pass

    parts = [f"{added} added"]
    if updated:
        parts.append(f"{updated} updated")
    if skipped:
        parts.append(f"{skipped} skipped")
    if gone:
        if mode == "replace":
            word = "removed"
        else:
            word = "deleted" if missing_action == "delete" else f"set to {missing_action}"
        parts.append(f"{gone} no longer in the file, {word}")
    if failed:
        parts.append(f"{failed} unusable rows")
    flash("Import finished: " + ", ".join(parts) + ".", "ok")
    return redirect(url_for("properties.index"))


@bp.route("/history")
@requires("import")
def history():
    rows = query(
        "SELECT i.*, u.name AS user_name, u.photo AS user_photo,"
        " un.name AS undone_name FROM imports i"
        " LEFT JOIN users u ON u.id = i.user_id"
        " LEFT JOIN users un ON un.id = i.undone_by"
        " ORDER BY i.id DESC LIMIT 60")
    return render_template("imports/history.html", rows=rows,
                           modes=dict(IMPORT_MODES))


@bp.route("/history/<int:iid>/undo", methods=("POST",))
@admin_required
def undo(iid):
    """Put the database back as it was before one import.

    Rows added are removed, rows changed are restored field by field, and rows
    deleted by a replace are put back. Anything edited by hand since the import
    is overwritten by the restore, which is why this is admin-only and warns.
    """
    record = query("SELECT * FROM imports WHERE id = ?", (iid,), one=True)
    if record is None:
        flash("That import is not in the history.", "error")
        return redirect(url_for("imports.history"))
    if record["undone_at"]:
        flash("That import has already been rolled back.", "error")
        return redirect(url_for("imports.history"))
    if not record["undo_data"]:
        flash("There is nothing to roll back for that import.", "error")
        return redirect(url_for("imports.history"))

    data = json.loads(record["undo_data"])
    removed = restored = returned = 0

    for pid in data.get("inserted", []):
        execute("DELETE FROM properties WHERE id = ?", (pid,))
        removed += 1

    for row in data.get("updated", []):
        fields = [k for k in row if k != "id"]
        sets = ", ".join(f"{k} = ?" for k in fields)
        execute(f"UPDATE properties SET {sets} WHERE id = ?",
                [row[k] for k in fields] + [row["id"]])
        restored += 1

    for row in data.get("deleted", []):
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        execute(f"INSERT OR REPLACE INTO properties ({', '.join(cols)})"
                f" VALUES ({placeholders})", [row[c] for c in cols])
        returned += 1

    execute("UPDATE imports SET undone_at = ?, undone_by = ?, status = 'rolled back'"
            " WHERE id = ?", (now(), g.user["id"], iid))
    log(g.user["id"], "Rolled back an import", detail=(
        f"import #{iid}: {removed} removed, {restored} restored, {returned} put back"))

    parts = []
    if removed:
        parts.append(f"{removed} added listings removed")
    if restored:
        parts.append(f"{restored} restored to how they were")
    if returned:
        parts.append(f"{returned} put back")
    flash("Import rolled back: " + ", ".join(parts) + ".", "ok")
    return redirect(url_for("imports.history"))


@bp.route("/discard/<token>", methods=("POST",))
@requires("import")
def discard(token):
    path = _path(token)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
    flash("Upload discarded. Nothing was saved.", "ok")
    return redirect(url_for("imports.upload"))


# ------------------------------------------------------------ tidy existing
TIDY_FIELDS = ("area", "building_no", "furnishing", "view", "balcony", "bills", "title")


def _tidy_proposal(row, known, rewrite_titles):
    """What the standard rules would change on one listing already saved.

    Only fills gaps and settles spellings: a value someone typed is never
    replaced by a different value, except a district spelled another way
    ('AL SADD' -> 'Al Sadd') and a SHOUTED building name put in title case.
    Titles are only rewritten on imported listings unless asked, since a
    hand-typed title may have been written for an advert."""
    new = {}
    building = normalize.tidy_name(row["building_no"])
    if building and building != (row["building_no"] or ""):
        new["building_no"] = building

    area = (row["area"] or "").strip()
    if area:
        canon = normalize.find_area(area)
        if canon and canon != area:
            new["area"] = canon
    else:
        guess = (importer.building_area(building, known)
                 or normalize.find_area(row["title"]))
        if guess:
            new["area"] = guess

    text = " | ".join(x for x in (row["title"], row["features"], row["extras"],
                                  row["description"]) if x)
    if not row["furnishing"]:
        v = normalize.parse_furnishing(row["features"], row["title"], row["description"])
        if v:
            new["furnishing"] = v
    if not row["view"]:
        feats = re.sub(r"car\s*park\w*|parking|gym|swimming pool|pool access", " ",
                       row["features"] or "", flags=re.IGNORECASE)
        v = normalize.parse_view(feats)
        if v:
            new["view"] = v
    if not row["balcony"]:
        v = normalize.parse_balcony(text)
        if v:
            new["balcony"] = v
    if not row["bills"]:
        v = normalize.parse_bills(row["features"], row["description"])
        if v:
            new["bills"] = v

    if row["import_source"] or rewrite_titles:
        title = normalize.standard_title(
            row["prop_type"], row["bedrooms"], new.get("building_no", building),
            row["unit_no"], new.get("area", area))
        if title != row["title"]:
            new["title"] = title
    return new


@bp.route("/tidy", methods=("GET", "POST"))
@admin_required
def tidy():
    """Bring listings already in the CRM up to the same standard as a fresh
    import. A preview first; nothing changes until the button is pressed, a
    backup is taken before, and the whole run can be rolled back from the
    import history like any import."""
    rewrite_titles = bool(request.values.get("titles"))
    known = known_buildings()
    rows = query("SELECT * FROM properties ORDER BY id")
    changes = []
    for row in rows:
        new = _tidy_proposal(row, known, rewrite_titles)
        if new:
            changes.append((row, new))

    if request.method == "POST":
        try:
            import backups
            backups.make_backup(current_app._get_current_object(), label="before-tidy")
        except Exception as exc:          # a backup failure must stop the run
            flash(f"Could not take a backup first, so nothing was changed ({exc}).",
                  "error")
            return redirect(url_for("imports.tidy"))
        undo = {"inserted": [], "updated": [], "deleted": []}
        stamp = now()
        for row, new in changes:
            undo["updated"].append({k: row[k] for k in row.keys()})
            sets = ", ".join(f"{k} = ?" for k in new)
            execute(f"UPDATE properties SET {sets}, updated_at = ? WHERE id = ?",
                    list(new.values()) + [stamp, row["id"]])
        execute(
            "INSERT INTO imports (user_id, filename, source, mode, sheets, rows_read,"
            " added, updated, skipped, removed, failed, status, undo_data, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (g.user["id"], "Tidy existing listings", "", "tidy", "", len(rows),
             0, len(changes), 0, 0, 0, "complete",
             json.dumps(undo) if changes else None, stamp))
        log(g.user["id"], "Tidied listings",
            detail=f"{len(changes)} of {len(rows)} listings brought to the standard format")
        flash(f"{len(changes)} listings tidied. This can be rolled back from the "
              "import history.", "ok")
        return redirect(url_for("imports.history"))

    counts = {f: 0 for f in TIDY_FIELDS}
    for _row, new in changes:
        for k in new:
            counts[k] += 1
    missing_area = sum(1 for r in rows if not (r["area"] or "").strip())
    for row, new in changes:
        if "area" in new and not (row["area"] or "").strip():
            missing_area -= 1
    return render_template("imports/tidy.html", changes=changes[:300],
                           total=len(changes), counts=counts, rows=len(rows),
                           missing_area=missing_area, rewrite_titles=rewrite_titles)
