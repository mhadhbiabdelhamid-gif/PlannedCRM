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
from auth import admin_required, login_required, requires
from db import (IMPORT_MODES, LISTING_TYPES, PROP_STATUS, PROP_TYPES, execute,
                log, next_ref, now, query)

bp = Blueprint("imports", __name__, url_prefix="/import")

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
    for name in wb.sheetnames:
        ws = wb[name]
        if ws.max_row < 2:
            continue
        info = importer.read_sheet(ws)
        listings = importer.extract(info, info["mapping"],
                                    {"listing_type": "Rent"})
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
            "header_row": info["header_row"],
            "headers": info["headers"],
            "mapping": info["mapping"],
            "context": info["context"],
            "count": len(listings),
            "preview": listings[:MAX_PREVIEW],
            "total_rows": len(info["rows"]),
        })

    owners = query("SELECT id, name FROM owners ORDER BY name")
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
        fields=importer.FIELDS, owners=owners, agents=agents, areas=areas,
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
        info = importer.read_sheet(ws, header_row=header_row)

        # whatever the reviewer chose on screen wins over the guess
        mapping = {}
        for field, _label in importer.FIELDS:
            chosen = importer.as_columns(d.getlist(f"map__{name}__{field}"))
            if chosen:
                mapping[field] = chosen

        defaults = {
            "building_no": d.get(f"building__{name}", "").strip(),
            "area": d.get(f"area__{name}", "").strip(),
            "listing_type": d.get("listing_type", "Rent"),
            "prop_type": d.get("prop_type", "Apartment"),
            "status": d.get("status", "Available"),
        }
        listings = importer.extract(
            info, mapping, defaults,
            fill_down=bool(d.get(f"filldown__{name}")),
            fill_numbers=bool(d.get(f"fillnumbers__{name}")))

        owner_id = int(d["owner_id"]) if d.get("owner_id") else None
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
                item["title"], "", item["area"] or defaults["area"],
                item["prop_type"], item["listing_type"], item["status"],
                item["price"] or 0, item["size_sqm"], item["bedrooms"],
                item["bathrooms"], item["description"], item["features"],
                owner_id, agent_id, building, item.get("floor_no", ""), unit,
                item.get("extras", ""), item["map_url"], 0, source, stamp,
            )

            if existing:
                undo["updated"].append(snapshot(existing))
                execute(
                    "UPDATE properties SET title=?,address=?,area=?,prop_type=?,"
                    "listing_type=?,status=?,price=?,size_sqm=?,bedrooms=?,bathrooms=?,"
                    "description=?,features=?,owner_id=COALESCE(?, owner_id),"
                    "agent_id=COALESCE(?, agent_id),building_no=?,floor_no=?,"
                    "unit_no=?,extras=?,map_url=?,is_own=?,import_source=?,"
                    "imported_at=?,updated_at=? WHERE id=?",
                    values + (stamp, existing["id"]))
                updated += 1
            else:
                new_id = execute(
                    "INSERT INTO properties (title,address,area,prop_type,listing_type,"
                    "status,price,size_sqm,bedrooms,bathrooms,description,features,"
                    "owner_id,agent_id,building_no,floor_no,unit_no,extras,map_url,"
                    "is_own,import_source,imported_at,ref,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
