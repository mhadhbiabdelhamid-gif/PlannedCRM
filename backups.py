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
from datetime import datetime, timedelta

KEEP = int(os.environ.get("BACKUP_KEEP", "14"))
INTERVAL_HOURS = int(os.environ.get("BACKUP_INTERVAL_HOURS", "24"))
OFFSITE_HOURS = int(os.environ.get("BACKUP_OFFSITE_INTERVAL_HOURS", "168"))
# Most providers reject a message larger than about 25 MB.
OFFSITE_MAX_MB = float(os.environ.get("BACKUP_OFFSITE_MAX_MB", "20"))


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


def send_offsite(app):
    """Email a copy of the database somewhere that isn't this server.

    Every other backup in this file lands on the same disk as the database it
    is protecting, which is no help at all if that disk is what goes. Set
    "Email a weekly copy to" in Settings, with the mail account already
    configured, and a zip leaves the building once a week.

    Returns (ok, message). Never raises.
    """
    from db import get_setting, set_setting
    import mailer

    to = (get_setting("backup_email", "") or "").strip()
    if not to:
        return False, "No off-site address set."
    if not mailer.is_configured():
        return False, "Email hasn't been set up, so no copy could be sent."

    # The database alone: uploaded photos and documents make the archive far
    # too large to email, and they are the part that can be gathered again.
    archive = build_archive(app, include_uploads=False)
    payload = archive.getvalue()
    size_mb = len(payload) / (1024 * 1024)
    if size_mb > OFFSITE_MAX_MB:
        return False, (f"The backup is {size_mb:.1f} MB, over the "
                       f"{OFFSITE_MAX_MB:.0f} MB email limit. Download it from "
                       "Settings instead.")

    stamp = datetime.now().strftime("%Y-%m-%d")
    company = get_setting("company_name", "Planned Real Estate")
    ok, detail = mailer.send(
        to,
        f"{company} — CRM backup {stamp}",
        (f"Attached is the weekly copy of the {company} CRM database, taken "
         f"{datetime.now().strftime('%d %B %Y at %H:%M')}.\n\n"
         "Keep it somewhere that isn't the server. Photos and documents are "
         "not included — only the records themselves.\n\n"
         "To restore, see READ-ME-FIRST.txt inside the zip.\n"),
        from_name=company,
        attachments=[(f"crm-backup-{stamp}.zip", payload)])

    if ok:
        set_setting("backup_offsite_at", _stamp())
        return True, f"Backup emailed to {to} ({size_mb:.1f} MB)."
    return False, detail


def _stamp(when=None):
    return (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _claim_offsite(app):
    """Take the weekly slot, or report that someone else already has it.

    The server runs several workers and each starts its own scheduler, so a
    plain "is it due yet" check has them all deciding yes at the same instant
    and sending the same backup several times over. The claim is a single
    conditional write, which SQLite serialises, so exactly one worker wins.
    """
    from db import get_db
    cutoff = _stamp(datetime.now() - timedelta(hours=OFFSITE_HOURS))
    db = get_db()
    cur = db.execute(
        "INSERT INTO settings (key, value) VALUES ('backup_offsite_at', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        "   WHERE settings.value IS NULL OR settings.value = ''"
        "      OR settings.value < ?",
        (_stamp(), cutoff))
    db.commit()
    claimed = cur.rowcount > 0
    cur.close()
    return claimed


def _release_offsite(retry_hours=6):
    """Hand the slot back after a failed send, dated so it retries before the
    week is out rather than immediately — a misconfigured mail account should
    not fill the log with the same complaint every half hour."""
    from db import set_setting
    set_setting("backup_offsite_at",
                _stamp(datetime.now() - timedelta(hours=OFFSITE_HOURS - retry_hours)))


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
                    if _claim_offsite(app):
                        ok, detail = send_offsite(app)
                        if not ok:
                            _release_offsite()
                        # Only worth a line in the log when it was actually
                        # meant to happen; an unset address is not a fault.
                        if ok or "No off-site address" not in detail:
                            print(f"  Off-site backup: {detail}")
            except Exception as exc:        # never let this kill the CRM
                print(f"  Backup failed: {exc}")
            time.sleep(1800)                # re-check every 30 minutes

    thread = threading.Thread(target=loop, daemon=True, name="crm-backup")
    thread.start()
    return thread
