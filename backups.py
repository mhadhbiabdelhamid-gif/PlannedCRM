"""Backups.

Three ways a copy gets made:

  1. Automatically, once a day, while the CRM is running.
  2. On demand, from the Settings page.
  3. From the command line, with `python manage.py backup`.

Copies are taken with SQLite's own backup API rather than a plain file copy, so
a backup made while someone is saving a record is still a valid database.
"""
import io
import os
import sqlite3
import threading
import time
import zipfile
from datetime import datetime

KEEP = int(os.environ.get("BACKUP_KEEP", "14"))
INTERVAL_HOURS = int(os.environ.get("BACKUP_INTERVAL_HOURS", "24"))


def default_folder(app):
    return os.path.join(os.path.dirname(app.config["DATABASE"]), "backups")


def check_folder(path):
    """Can we actually write backups there? Returns (ok, message)."""
    if not path:
        return False, "No folder given."
    path = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))

    # A relative path would quietly create a folder inside the project, which is
    # never what someone means here.
    looks_windows = len(path) > 2 and path[1] == ":"
    if not (os.path.isabs(path) or looks_windows):
        return False, ("Use the full path. On Windows it starts with a drive letter, "
                       r"like C:\Users\You\OneDrive\CRM-Backups. Copy it from the "
                       "address bar in File Explorer.")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        return False, f"That folder can't be created: {exc}"
    probe = os.path.join(path, ".crm-write-test")
    try:
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as exc:
        return False, f"That folder can't be written to: {exc}"
    return True, path


def backup_folder(app):
    """Where copies are written.

    Checked in order: the folder set on the Settings page, then the
    BACKUP_FOLDER environment variable, then instance/backups. Pointing it at a
    OneDrive or Google Drive folder carries every backup off this computer.
    """
    chosen = ""
    try:                                # only works inside an app context
        from db import get_setting
        chosen = get_setting("backup_folder", "")
    except Exception:
        chosen = ""

    candidate = chosen or os.environ.get("BACKUP_FOLDER", "")
    if candidate:
        ok, resolved = check_folder(candidate)
        if ok:
            return resolved

    folder = default_folder(app)
    os.makedirs(folder, exist_ok=True)
    return folder


def make_backup(app, label="auto"):
    """Write a consistent copy of the database. Returns its path, or None."""
    src = app.config["DATABASE"]
    if not os.path.exists(src):
        return None

    folder = backup_folder(app)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    dest = os.path.join(folder, f"crm-{stamp}-{label}.sqlite3")

    source = sqlite3.connect(src)
    target = sqlite3.connect(dest)
    try:
        with target:
            source.backup(target)
    finally:
        target.close()
        source.close()

    prune(app)
    return dest


def list_backups(app):
    """Newest first: (filename, size in KB, datetime)."""
    folder = backup_folder(app)
    out = []
    for name in os.listdir(folder):
        if not name.endswith(".sqlite3"):
            continue
        full = os.path.join(folder, name)
        out.append((name, os.path.getsize(full) / 1024,
                    datetime.fromtimestamp(os.path.getmtime(full))))
    return sorted(out, key=lambda r: r[2], reverse=True)


def prune(app):
    """Keep the most recent KEEP copies and delete the rest."""
    for name, _size, _when in list_backups(app)[KEEP:]:
        try:
            os.remove(os.path.join(backup_folder(app), name))
        except OSError:
            pass


def last_backup_age_hours(app):
    items = list_backups(app)
    if not items:
        return None
    return (datetime.now() - items[0][2]).total_seconds() / 3600


def build_archive(app, include_uploads=True):
    """A single zip holding the database and, optionally, every uploaded file.

    Built in memory so nothing temporary is left lying around on the server.
    """
    src = app.config["DATABASE"]
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(src):
            # snapshot to a temp file first, so the copy is internally consistent
            tmp = src + ".snapshot"
            source, target = sqlite3.connect(src), sqlite3.connect(tmp)
            try:
                with target:
                    source.backup(target)
            finally:
                target.close()
                source.close()
            zf.write(tmp, "crm.sqlite3")
            try:
                os.remove(tmp)
            except OSError:
                pass

        if include_uploads:
            uploads = app.config["UPLOAD_FOLDER"]
            for root, _dirs, files in os.walk(uploads):
                for name in files:
                    if name == ".gitkeep":
                        continue
                    full = os.path.join(root, name)
                    zf.write(full, os.path.join(
                        "uploads", os.path.relpath(full, uploads)))

        zf.writestr("READ-ME-FIRST.txt", (
            "Planned Real Estate CRM backup\n"
            f"Taken: {datetime.now().strftime('%d %B %Y at %H:%M')}\n\n"
            "To restore:\n"
            "  1. Stop the CRM (stop-crm.bat).\n"
            "  2. Copy crm.sqlite3 into the instance folder, replacing the\n"
            "     file already there.\n"
            "  3. Copy the uploads folder into instance, replacing it.\n"
            "  4. Start the CRM again.\n\n"
            "Or from the project folder:\n"
            "  python manage.py restore path\\to\\crm.sqlite3\n"
        ))

    buf.seek(0)
    return buf


def start_scheduler(app):
    """Take a backup every INTERVAL_HOURS while the CRM is running.

    The age check means several server workers can't stack up duplicates, and
    a machine that was switched off simply backs up when it next starts.
    """
    def loop():
        time.sleep(60)                      # let start-up settle
        while True:
            try:
                with app.app_context():
                    age = last_backup_age_hours(app)
                    if age is None or age >= INTERVAL_HOURS:
                        path = make_backup(app, "auto")
                        if path:
                            print(f"  Backup saved: {path}")
            except Exception as exc:        # never let this kill the CRM
                print(f"  Backup failed: {exc}")
            time.sleep(1800)                # re-check every 30 minutes

    thread = threading.Thread(target=loop, daemon=True, name="crm-backup")
    thread.start()
    return thread
