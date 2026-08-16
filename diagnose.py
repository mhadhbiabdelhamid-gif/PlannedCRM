"""Check why the CRM won't start, and say what to do about it.

Uses nothing outside the standard library, so it runs even when the project's
dependencies were never installed.

    python diagnose.py

On Windows you can also double-click check-setup.bat, which keeps the window open.
"""
import os
import platform
import socket
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", 5000))

problems = []
notes = []


def line(char="-", width=62):
    print(char * width)


def head(title):
    print(f"\n{title}")
    line()


def ok(msg):
    print(f"  [ OK ]  {msg}")


def bad(msg, fix):
    print(f"  [FAIL]  {msg}")
    problems.append((msg, fix))


def warn(msg):
    print(f"  [ ?  ]  {msg}")


def tell(msg):
    """Advice for the summary, as opposed to a diagnosis."""
    if msg not in notes:
        notes.append(msg)


def wrap(text, indent):
    return textwrap.fill(text, width=60, initial_indent=indent,
                         subsequent_indent=indent)


# ------------------------------------------------------------------ 1. python
def check_python():
    head("1. Python")
    v = sys.version_info
    print(f"  Version: {v.major}.{v.minor}.{v.micro}")
    print(f"  Running from: {sys.executable}")
    if v < (3, 9):
        bad(f"Python {v.major}.{v.minor} is too old.",
            "Install Python 3.10 or newer from python.org, and tick "
            "'Add Python to PATH' during setup.")
    else:
        ok("Python version is fine.")

    if "WindowsApps" in sys.executable:
        warn("This is the Microsoft Store version of Python, which sometimes "
             "blocks folder access.")
        tell("If anything below fails oddly, install Python from python.org "
             "rather than the Microsoft Store.")


# ------------------------------------------------------------- 2. right folder
def check_folder():
    head("2. Project folder")
    print(f"  Looking in: {HERE}")
    required = ["app.py", "db.py", "auth.py", "requirements.txt", "templates", "static"]
    missing = [f for f in required if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        bad(f"These are missing: {', '.join(missing)}",
            "You are probably in the wrong folder, or the zip was only partly "
            "extracted. Extract the whole zip again and make sure app.py sits "
            "directly beside this file.")
    else:
        ok("All the project files are here.")

    if HERE.lower().endswith((".zip", "temp")) or "\\Temp\\" in HERE or "/Temp/" in HERE:
        bad("The project is running from a temporary folder.",
            "You opened the zip without extracting it. Right-click the zip, "
            "choose 'Extract All', and work from the extracted folder.")


# ------------------------------------------------------------- 3. dependencies
def check_deps():
    head("3. Required packages")
    venv = os.path.join(HERE, ".venv")
    if os.path.isdir(venv):
        ok("A .venv folder exists.")
    else:
        warn("No .venv folder yet — start-office.bat creates one on first run.")

    for mod, why in (("flask", "the web framework"), ("waitress", "the server")):
        try:
            __import__(mod)
            ok(f"{mod} is installed ({why}).")
        except ImportError:
            bad(f"{mod} is not installed ({why}).",
                f"Run:  pip install -r requirements.txt")


# ---------------------------------------------------------------- 4. database
def check_db():
    head("4. Database")
    path = os.path.join(HERE, "instance", "crm.sqlite3")
    if os.path.exists(path):
        kb = os.path.getsize(path) / 1024
        ok(f"Database found, {kb:,.0f} KB.")
        try:
            import sqlite3
            con = sqlite3.connect(path)
            n = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            admins = con.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1"
            ).fetchone()[0]
            con.close()
            print(f"  Accounts: {n}   Active admins: {admins}")
            if admins == 0:
                bad("There is no active admin account.",
                    "Run:  python manage.py newadmin your@email")
        except Exception as exc:
            warn(f"Could not read the database: {exc}")
    else:
        warn("No database yet — it gets created the first time the app starts.")


# ----------------------------------------------------------------- 5. network
def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def check_network():
    head("5. Network")
    ip = lan_ip()
    if ip and not ip.startswith("127."):
        ok(f"This computer's office address is {ip}")
        print(f"  Team members would use:  http://{ip}:{PORT}")
    else:
        bad("This computer doesn't seem to be on a network.",
            "Connect it to the office wifi or plug in the network cable.")

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.8)
    in_use = probe.connect_ex(("127.0.0.1", PORT)) == 0
    probe.close()

    if in_use:
        ok(f"Something is answering on port {PORT} — the CRM appears to be running.")
        print(f"  Try opening:  http://localhost:{PORT}")
        tell("If the CRM is running but the browser shows nothing, check you "
             "typed http:// and not https://")
    else:
        warn(f"Nothing is listening on port {PORT} — the CRM is not running now.")
        tell("That is normal if you have not started it yet. Start it with: "
             "python serve_office.py")


# ---------------------------------------------------------------- 6. firewall
def check_firewall():
    if platform.system() != "Windows":
        return
    head("6. Windows firewall")
    try:
        out = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name=all"],
            capture_output=True, text=True, timeout=25).stdout.lower()
        if "python" in out:
            ok("A firewall rule mentioning Python exists.")
            print("  If colleagues still can't connect, make sure it is allowed")
            print("  on Private networks rather than Public.")
        else:
            warn("No firewall rule for Python found.")
            tell("When you start the CRM, Windows asks for permission. Tick "
                 "'Private networks' and click Allow. Without that, only this "
                 "computer can open the CRM.")
    except Exception:
        warn("Could not read the firewall rules — skip this check.")


# --------------------------------------------------------------- 7. last log
def check_log():
    head("7. Last time it stopped")
    path = os.path.join(HERE, "instance", "server.log")
    if not os.path.exists(path):
        warn("No server.log yet — that file appears once you use keep-running.bat.")
        tell("To stop the CRM dropping out, run install-autostart.bat as "
             "administrator. It restarts the CRM automatically if it stops.")
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = [l.rstrip() for l in fh if l.strip()]
    except OSError as exc:
        warn(f"Could not read the log: {exc}")
        return

    if not lines:
        warn("The log is empty.")
        return

    print("  Last 12 lines of instance/server.log:\n")
    for l in lines[-12:]:
        print(f"    {l[:70]}")

    joined = " ".join(lines[-60:]).lower()
    if "address already in use" in joined or "only one usage" in joined:
        bad("Port 5000 is already taken by something else.",
            "Another copy of the CRM is probably still running. Run "
            "stop-crm.bat, then start it again.")
    elif "modulenotfounderror" in joined or "no module named" in joined:
        bad("A required package is missing.",
            "Run:  pip install -r requirements.txt")
    elif "traceback" in joined:
        warn("The log contains an error trace — the lines above show it.")
        tell("Copy those lines and send them if you are not sure what they mean.")
    print()


# ------------------------------------------------------------------- summary
def summary():
    print()
    line("=")
    if problems:
        print(f"  {len(problems)} thing(s) to fix")
        line("=")
        for i, (what, fix) in enumerate(problems, 1):
            print(f"\n  {i}. {what}")
            print(wrap(fix, "     "))
    else:
        print("  No blocking problems found.")
        line("=")
        print("\n  Start the CRM with:   python serve_office.py")
        print("  or double-click:      start-office.bat")

    if notes:
        print("\n  Also worth knowing:")
        for n in notes:
            print(wrap("- " + n, "    "))
    print()
    line("=")
    print("  Copy everything above and send it if you need help.")
    line("=")
    print()


if __name__ == "__main__":
    print()
    line("=")
    print("  Planned Real Estate CRM — setup check")
    print(f"  {platform.system()} {platform.release()}")
    line("=")
    check_python()
    check_folder()
    check_deps()
    check_db()
    check_network()
    check_firewall()
    check_log()
    summary()
