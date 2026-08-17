"""Sending email from the CRM through the company's own mail account.

Mail goes out over SMTP using credentials set in Settings, so messages come
from info@plannedrealestate.com rather than some unfamiliar address, and land
in the account's own Sent folder if the provider supports it.
"""
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from db import get_setting

SETTING_KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_pass",
                "smtp_from", "smtp_security")

# The three providers a Doha office is most likely to be on.
PRESETS = {
    "gmail": {"label": "Gmail / Google Workspace", "host": "smtp.gmail.com",
              "port": "587", "security": "starttls",
              "note": "Google requires an App Password, not your normal one. "
                      "Turn on 2-step verification, then create an App Password "
                      "and paste it here."},
    "microsoft": {"label": "Microsoft 365 / Outlook", "host": "smtp.office365.com",
                  "port": "587", "security": "starttls",
                  "note": "Use the mailbox address and its password. If your "
                          "account has 2-step verification, create an App "
                          "Password instead."},
    "other": {"label": "Another provider or company mail server", "host": "",
              "port": "587", "security": "starttls",
              "note": "Ask whoever manages your email for the SMTP server "
                      "address, port, and whether it uses TLS or SSL."},
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_address(value):
    return bool(EMAIL_RE.match((value or "").strip()))


def settings():
    return {k: (get_setting(k, "") or "") for k in SETTING_KEYS}


def is_configured():
    """A server address is the minimum. Some company relays accept mail from
    inside the network with no username at all."""
    s = settings()
    return bool(s["smtp_host"].strip())


def _connect(s):
    host = s["smtp_host"].strip()
    port = int(s["smtp_port"] or 587)
    security = (s["smtp_security"] or "starttls").lower()
    context = ssl.create_default_context()

    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        if security == "starttls":
            server.starttls(context=context)
    user = s["smtp_user"].strip()
    if user and s["smtp_pass"]:
        try:
            server.login(user, s["smtp_pass"])
        except smtplib.SMTPNotSupportedError:
            # an internal relay that trusts the network needs no sign-in
            pass
    return server


def send(to, subject, body, from_name=None, reply_to=None, cc=None):
    """Send one message. Returns (ok, message) — never raises at the caller."""
    s = settings()
    if not is_configured():
        return False, ("Email hasn't been set up yet. An admin can add the "
                       "mail account under Settings.")
    if not valid_address(to):
        return False, f"{to or 'That address'} doesn't look like an email address."

    sender = (s["smtp_from"] or s["smtp_user"]).strip()
    msg = EmailMessage()
    msg["Subject"] = subject or "(no subject)"
    msg["From"] = formataddr((from_name, sender)) if from_name else sender
    msg["To"] = to.strip()
    if cc and valid_address(cc):
        msg["Cc"] = cc.strip()
    if reply_to and valid_address(reply_to):
        msg["Reply-To"] = reply_to.strip()
    msg.set_content(body or "")

    recipients = [to.strip()] + ([cc.strip()] if cc and valid_address(cc) else [])

    try:
        server = _connect(s)
    except smtplib.SMTPAuthenticationError:
        return False, ("The mail server rejected the username or password. "
                       "If you use Gmail or Microsoft 365 with 2-step "
                       "verification, you need an App Password rather than "
                       "your normal one.")
    except (smtplib.SMTPConnectError, OSError) as exc:
        return False, (f"Could not reach the mail server ({exc}). Check the "
                       "server address and port, and that this computer can "
                       "reach the internet.")
    except smtplib.SMTPException as exc:
        return False, f"The mail server refused the connection: {exc}"

    try:
        server.send_message(msg, from_addr=sender, to_addrs=recipients)
    except smtplib.SMTPRecipientsRefused:
        return False, f"The mail server would not accept {to}."
    except smtplib.SMTPException as exc:
        return False, f"The message could not be sent: {exc}"
    finally:
        try:
            server.quit()
        except Exception:
            pass

    return True, f"Sent to {to}."


def test_connection():
    """Prove the settings work without sending anything to a real person."""
    s = settings()
    if not is_configured():
        return False, "Fill in the mail server address first."
    try:
        server = _connect(s)
        server.quit()
    except smtplib.SMTPAuthenticationError:
        return False, ("Connected to the server, but the username or password "
                       "was rejected. Gmail and Microsoft 365 usually need an "
                       "App Password.")
    except Exception as exc:
        return False, f"Could not connect: {exc}"
    return True, "Connected and signed in successfully."
