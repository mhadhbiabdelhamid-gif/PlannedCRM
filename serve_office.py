"""Run the CRM on the office network so the whole team can reach it.

    python serve_office.py

Uses Waitress, which is a proper production server and works on Windows, macOS
and Linux. The dev server in app.py is single-threaded and prints a warning
about not being for production — it is fine for one person, not for a team.

Leave this window open. Closing it stops the CRM for everyone.
"""
import os
import socket
import sys


def lan_ip():
    """The address this machine has on the office network.

    Opens a UDP socket toward a public address to see which local interface the
    OS would route through. Nothing is actually sent.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    port = int(os.environ.get("PORT", 5000))

    # A stable secret key, so nobody is signed out every time this restarts.
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "instance", "secret.key")
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    if not os.path.exists(key_file):
        with open(key_file, "w") as fh:
            fh.write(os.urandom(32).hex())
    with open(key_file) as fh:
        os.environ.setdefault("SECRET_KEY", fh.read().strip())

    from app import create_app
    application = create_app()

    ip = lan_ip()
    line = "=" * 58
    print(f"\n{line}")
    print("  Planned Real Estate CRM is running")
    print(line)
    print(f"\n  On this computer:      http://localhost:{port}")
    print(f"  For everyone else:     http://{ip}:{port}\n")
    print("  Send that second address to the team. They open it in any")
    print("  browser on the office wifi — no install needed.\n")
    print("  Keep this window open. Closing it stops the CRM.")
    print(f"{line}\n")

    try:
        from waitress import serve
    except ImportError:
        print("Waitress isn't installed. Run:  pip install waitress\n")
        sys.exit(1)

    serve(application, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    main()
