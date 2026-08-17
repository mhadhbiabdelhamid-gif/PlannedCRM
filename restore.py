"""
One-time database restore, for moving the CRM onto a server.

Upload your local crm.sqlite3 and it becomes the live database. Intended to be
used once, when you first deploy, and then switched off.

Three locks, because this endpoint replaces everything:
  1. You must be signed in as an admin.
  2. RESTORE_TOKEN must be set in the environment, and typed into the form.
     No token set means the page returns 404 and the upload cannot run.
  3. The uploaded file is checked to be a real CRM database before anything is
     replaced, and the current database is backed up first.

To use it:
  1. Register in app.py:  from restore import restore_bp
                          app.register_blueprint(restore_bp)
  2. On Render, add an environment variable RESTORE_TOKEN with any long random
     value. The service restarts.
  3. Go to /restore, type the token, choose your local crm.sqlite3, upload.
  4. Restart the service from the Render dashboard so it reopens the new file.
  5. DELETE the RESTORE_TOKEN variable. The page disappears again.

Step 5 matters. Anyone who is an admin and knows the token can overwrite your
whole database while it is set.
"""
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template_string, request, url_for)

from auth import admin_required

restore_bp = Blueprint("restore", __name__, url_prefix="/restore")

# A file claiming to be the CRM must have at least these.
EXPECTED = {"users", "properties"}


def token():
    return os.environ.get("RESTORE_TOKEN", "")


def db_path():
    return (current_app.config.get("DATABASE")
            or os.environ.get("DATABASE_PATH", "instance/crm.sqlite3"))


def inspect(path):
    """Return (ok, message, counts) for a candidate database file."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = EXPECTED - names
        if missing:
            conn.close()
            return False, f"That file has no {', '.join(sorted(missing))} table. " \
                          "It doesn't look like a CRM database.", {}
        counts = {}
        for table in ("properties", "users", "leads", "deals"):
            if table in names:
                counts[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return True, "", counts
    except sqlite3.Error as exc:
        return False, f"That file isn't a readable SQLite database ({exc}).", {}


PAGE = """<!doctype html>
<title>Restore database</title>
<style>
 body{margin:0;padding:40px 20px;background:#17181a;color:#e8e6e1;
      font:15px/1.55 "Segoe UI",system-ui,sans-serif}
 .w{max-width:560px;margin:0 auto}
 h1{font-size:20px;margin:0 0 6px}
 p.sub{color:#9a9691;font-size:13px;margin:0 0 22px}
 .panel{background:#1f2124;border:1px solid #33363b;border-radius:6px;padding:18px}
 .warn{background:#2b1c1b;border:1px solid #6a2f2a;border-radius:6px;
       padding:12px 14px;font-size:13px;margin-bottom:18px}
 label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.06em;
       color:#9a9691;margin:14px 0 4px}
 label:first-child{margin-top:0}
 input{width:100%;padding:9px 10px;background:#17181a;color:#e8e6e1;
       border:1px solid #33363b;border-radius:4px;font:inherit}
 input:focus{outline:2px solid #c9a227;outline-offset:1px}
 button{background:#c9a227;color:#17181a;border:0;border-radius:6px;
        padding:11px 22px;font:600 14px inherit;cursor:pointer;margin-top:18px}
 .msg{border-radius:6px;padding:12px 14px;font-size:13px;margin-bottom:16px}
 .ok{background:#1b2620;border:1px solid #2f5a3f}
 .bad{background:#2b1c1b;border:1px solid #6a2f2a}
 .now{color:#9a9691;font-size:13px;margin-top:16px}
</style>
<div class="w">
 <h1>Restore database</h1>
 <p class="sub">Replaces everything on this server with the file you upload.</p>

 {% for cat, m in messages %}<div class="msg {{ 'ok' if cat == 'ok' else 'bad' }}">{{ m }}</div>{% endfor %}

 <div class="warn">
  This overwrites the live database. The current one is backed up first, but
  anything entered on this server since your local copy was made will be gone.
 </div>

 <form method="post" enctype="multipart/form-data" class="panel">
  <label for="tok">Restore token</label>
  <input id="tok" name="token" type="password" autocomplete="off" required>

  <label for="f">Your crm.sqlite3</label>
  <input id="f" name="database" type="file" accept=".sqlite3,.db,.sqlite" required>

  <button type="submit">Replace the database</button>
 </form>

 <div class="now">Currently live: {{ current }}</div>
</div>
"""


@restore_bp.route("/", methods=("GET", "POST"))
@admin_required
def restore():
    if not token():
        abort(404)                       # switched off, so it doesn't exist

    messages = []
    if request.method == "POST":
        if request.form.get("token", "") != token():
            messages.append(("bad", "That token doesn't match."))
        else:
            upload = request.files.get("database")
            if not upload or not upload.filename:
                messages.append(("bad", "Choose a database file."))
            else:
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite3")
                tmp.close()
                upload.save(tmp.name)

                ok, why, counts = inspect(tmp.name)
                if not ok:
                    os.unlink(tmp.name)
                    messages.append(("bad", why))
                else:
                    live = db_path()
                    os.makedirs(os.path.dirname(live) or ".", exist_ok=True)
                    if os.path.exists(live):
                        kept = f"{live}.replaced-{datetime.now():%Y%m%d-%H%M%S}"
                        shutil.copy2(live, kept)
                    # Drop the write-ahead files, or the old data can bleed back.
                    for suffix in ("-wal", "-shm"):
                        side = live + suffix
                        if os.path.exists(side):
                            os.unlink(side)
                    shutil.move(tmp.name, live)
                    summary = ", ".join(f"{v} {k}" for k, v in counts.items())
                    messages.append(("ok",
                        f"Restored: {summary}. Restart the service from the "
                        "Render dashboard, then delete the RESTORE_TOKEN "
                        "variable."))

    live = db_path()
    ok, _, counts = inspect(live) if os.path.exists(live) else (False, "", {})
    current = (", ".join(f"{v} {k}" for k, v in counts.items())
               if ok else "no database yet")
    return render_template_string(PAGE, messages=messages, current=current)