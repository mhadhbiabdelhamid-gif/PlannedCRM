"""Rescue and maintenance tool for the CRM.

Run these from the project folder, with the server stopped.

    python manage.py doctor              what state is everything in?
    python manage.py users               list every account
    python manage.py password <email>    set a new password for an account
    python manage.py promote <email>     make an account an admin and switch it on
    python manage.py newadmin <email>    create a fresh admin account
    python manage.py backup              timestamped copy of the database
    python manage.py restore <file>      put a backup back
"""
import getpass
import os
import sqlite3
import shutil
import socket
import sys
from datetime import datetime

from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app                                    # noqa: E402
from auth import create_user                                  # noqa: E402
from db import execute, query                                 # noqa: E402

app = create_app()


def _db_path():
    return app.config["DATABASE"]


def _ask_password(prompt="New password (at least 8 characters): "):
    while True:
        pw = getpass.getpass(prompt)
        if len(pw) < 8:
            print("  Too short — try again.")
            continue
        if pw != getpass.getpass("Type it again to confirm: "):
            print("  Those didn't match — try again.")
            continue
        return pw


# ------------------------------------------------------------------ commands
def doctor():
    print("\n--- Database ---")
    path = _db_path()
    print(f"  File:    {path}")
    if not os.path.exists(path):
        print("  MISSING. Nothing has been created yet — start the app once.")
        return
    size = os.path.getsize(path) / 1024
    print(f"  Size:    {size:,.0f} KB")

    with app.app_context():
        print("\n--- Records ---")
        for tbl in ("users", "properties", "leads", "deals", "owners", "partners",
                    "viewings", "comments", "documents", "activity"):
            try:
                n = query(f"SELECT COUNT(*) c FROM {tbl}", one=True)["c"]
                print(f"  {tbl:<12} {n}")
            except Exception as exc:
                print(f"  {tbl:<12} could not be read — {exc}")

        print("\n--- Accounts ---")
        admins = query("SELECT * FROM users WHERE role='admin' AND is_active=1")
        if admins:
            for a in admins:
                print(f"  admin: {a['email']}  ({a['name']})")
        else:
            print("  NO ACTIVE ADMIN ACCOUNT.")
            print("  You are locked out of Settings and Team members.")
            print("  Fix it with:  python manage.py promote <email>")
            print("            or: python manage.py newadmin <your@email>")

        inactive = query("SELECT email FROM users WHERE is_active=0")
        for u in inactive:
            print(f"  switched off: {u['email']}")

    print("\n--- Can the app write? ---")
    folder = os.path.dirname(path)
    for label, target in (("database file", path), ("instance folder", folder)):
        try:
            if target == folder:
                probe = os.path.join(folder, ".write-test")
                open(probe, "w").close()
                os.remove(probe)
            else:
                con = sqlite3.connect(target)
                con.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
                con.execute("DROP TABLE _probe")
                con.commit()
                con.close()
            print(f"  {label}: writable")
        except Exception as exc:
            print(f"  {label}: NOT WRITABLE — {exc}")
            print("    A read-only folder makes every save fail with a server error.")
            print("    Move the project out of Program Files, or fix its permissions.")

    print("\n--- Packages ---")
    for mod in ("flask", "waitress", "openpyxl"):
        try:
            __import__(mod)
            print(f"  {mod}: installed")
        except ImportError:
            print(f"  {mod}: MISSING — run  pip install -r requirements.txt")
    try:
        __import__("PIL")
        print("  Pillow: installed (photos will be resized on upload)")
    except ImportError:
        print("  Pillow: not installed — optional. Profile photos still upload,")
        print("    they are just stored at full size. To add it: pip install Pillow")

    print(f"\n--- Python ---")
    v = sys.version_info
    print(f"  {v.major}.{v.minor}.{v.micro}")
    if v >= (3, 13):
        print("  This version is very new. Some packages have no prebuilt Windows")
        print("  build for it yet and will try to compile, which usually fails.")
        print("  Python 3.12 is the safer choice for this app.")

    print("\n--- Network ---")
    port = int(os.environ.get("PORT", 5000))
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.6)
    busy = probe.connect_ex(("127.0.0.1", port)) == 0
    probe.close()
    if busy:
        print(f"  Something is already answering on port {port}.")
        print("  If that's the CRM, it's running. If pages still won't load,")
        print("  it may be a stale process — close that window and start again.")
    else:
        print(f"  Nothing is listening on port {port} — the CRM is not running.")
        print("  Start it with:  python serve_office.py")

    print("\n--- Backups ---")
    folder = os.path.join(os.path.dirname(_db_path()), "backups")
    if os.path.isdir(folder) and os.listdir(folder):
        for f in sorted(os.listdir(folder))[-5:]:
            print(f"  {f}")
    else:
        print("  None yet. Make one with:  python manage.py backup")
    print()


def users():
    with app.app_context():
        rows = query("SELECT id, name, email, role, is_active FROM users ORDER BY role, name")
        if not rows:
            print("No accounts at all. Create one with: python manage.py newadmin <email>")
            return
        print(f"\n  {'ID':<4} {'ROLE':<7} {'ON':<4} {'NAME':<22} EMAIL")
        for u in rows:
            print(f"  {u['id']:<4} {u['role']:<7} {'yes' if u['is_active'] else 'no':<4} "
                  f"{u['name'][:21]:<22} {u['email']}")
        print()


def password(email):
    with app.app_context():
        u = query("SELECT * FROM users WHERE lower(email)=?", (email.lower(),), one=True)
        if not u:
            print(f"No account with the email {email}.")
            print("Run 'python manage.py users' to see what exists.")
            return
        pw = _ask_password()
        execute("UPDATE users SET password_hash=?, is_active=1 WHERE id=?",
                (generate_password_hash(pw), u["id"]))
        print(f"Password changed for {u['name']} ({u['email']}). The account is switched on.")


def promote(email):
    with app.app_context():
        u = query("SELECT * FROM users WHERE lower(email)=?", (email.lower(),), one=True)
        if not u:
            print(f"No account with the email {email}.")
            return
        execute("UPDATE users SET role='admin', is_active=1 WHERE id=?", (u["id"],))
        print(f"{u['name']} ({u['email']}) is now an active admin.")


def newadmin(email):
    with app.app_context():
        if query("SELECT id FROM users WHERE lower(email)=?", (email.lower(),), one=True):
            print("That email is already taken. Use 'promote' or 'password' instead.")
            return
        name = input("Full name: ").strip() or "Administrator"
        pw = _ask_password()
        create_user(name, email, pw, "admin")
        print(f"Admin account created for {email}. You can sign in now.")


def backup():
    src = _db_path()
    if not os.path.exists(src):
        print("There's no database to back up yet.")
        return
    folder = os.path.join(os.path.dirname(src), "backups")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = os.path.join(folder, f"crm-{stamp}.sqlite3")
    shutil.copy2(src, dest)
    print(f"Backed up to {dest}")
    print("Uploaded photos and documents live in the uploads folder — copy that too.")
    return dest


def restore(path):
    if not os.path.exists(path):
        print(f"Can't find {path}")
        return
    dest = _db_path()
    if os.path.exists(dest):
        safety = dest + ".before-restore"
        shutil.copy2(dest, safety)
        print(f"Current database set aside as {safety}")
    shutil.copy2(path, dest)
    print(f"Restored from {path}. Start the app and check before carrying on.")


COMMANDS = {
    "doctor": (doctor, 0), "users": (users, 0), "backup": (backup, 0),
    "password": (password, 1), "promote": (promote, 1),
    "newadmin": (newadmin, 1), "restore": (restore, 1),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return
    fn, needs = COMMANDS[sys.argv[1]]
    args = sys.argv[2:]
    if len(args) < needs:
        print(f"'{sys.argv[1]}' needs another argument. See the list above.")
        print(__doc__)
        return
    fn(*args[:needs])


if __name__ == "__main__":
    main()
