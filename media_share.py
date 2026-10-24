"""Send a listing's photos and videos to a client on WhatsApp.

The agent picks a listing on the client's page and presses one button. That
makes a private link to a branded gallery of the listing's media and opens
WhatsApp with a pre-filled message carrying the link — so nothing has to be
downloaded to the agent's phone first, and the client sees every photo and
video in one place instead of twenty separate WhatsApp attachments.

The gallery is the only page in the CRM that works without signing in. It is
reached through an unguessable token, shows only what a client should see
(no owner, building number, unit number, notes or description — partner
descriptions often carry the owner's phone number), and stops working after
SHARE_DAYS or as soon as someone presses "Stop sharing".
"""
import os
import secrets
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, send_from_directory, url_for)

import whatsapp
from auth import can_edit, can_see_listing, login_required
from db import STAMP, execute, get_setting, log, now, query

bp = Blueprint("media", __name__)

SHARE_DAYS = 30

# Link-preview robots fetch the page the moment the message is sent. Counting
# them would tell the agent the client opened it when nobody did.
BOT_MARKERS = ("whatsapp", "facebookexternalhit", "facebot", "telegrambot",
               "twitterbot", "slackbot", "discordbot", "bot", "crawler", "spider",
               "preview")


def media_counts(pid):
    row = query("SELECT"
                " (SELECT COUNT(*) FROM property_images WHERE property_id = ?) AS photos,"
                " (SELECT COUNT(*) FROM property_videos WHERE property_id = ?) AS videos",
                (pid, pid), one=True)
    return row["photos"], row["videos"]


def shareable_listings(lead):
    """Published listings that have something to show, the client's own
    listing of interest first."""
    rows = query(
        "SELECT p.id, p.ref, p.title, p.status,"
        " (SELECT COUNT(*) FROM property_images i WHERE i.property_id = p.id) AS photos,"
        " (SELECT COUNT(*) FROM property_videos v WHERE v.property_id = p.id) AS videos"
        " FROM properties p"
        " WHERE COALESCE(p.approval, 'approved') = 'approved'"
        " AND (EXISTS (SELECT 1 FROM property_images i WHERE i.property_id = p.id)"
        "   OR EXISTS (SELECT 1 FROM property_videos v WHERE v.property_id = p.id))"
        " ORDER BY (p.id = ?) DESC,"
        " (p.status IN ('Available', 'Reserved')) DESC, p.id DESC LIMIT 300",
        (lead["property_id"] or 0,))
    return rows


def shares_for_lead(lid):
    return query("SELECT s.*, p.title AS prop_title, p.ref AS prop_ref,"
                 " u.name AS sender FROM media_shares s"
                 " JOIN properties p ON p.id = s.property_id"
                 " LEFT JOIN users u ON u.id = s.created_by"
                 " WHERE s.lead_id = ? ORDER BY s.id DESC LIMIT 20", (lid,))


def share_url(token):
    from auth import _site_url
    return _site_url() + url_for("media.gallery", token=token)


def _live(share):
    return (share is not None and not share["revoked_at"]
            and share["expires_at"] > now())


# ----------------------------------------------------------- agent side
@bp.route("/leads/<int:lid>/share-media", methods=("POST",))
@login_required
def share(lid):
    lead = query("SELECT * FROM leads WHERE id = ?", (lid,), one=True)
    if lead is None:
        flash("That lead no longer exists.", "error")
        return redirect(url_for("leads.board"))
    try:
        pid = int(request.form.get("property_id") or 0)
    except ValueError:
        pid = 0
    p = query("SELECT * FROM properties WHERE id = ?", (pid,), one=True)
    if p is None:
        flash("Pick the listing whose photos and videos you want to send.", "error")
        return redirect(url_for("leads.detail", lid=lid))
    if not can_see_listing(p) or (p["approval"] or "approved") != "approved":
        flash("That listing isn't published yet, so it can't be sent to a client.", "error")
        return redirect(url_for("leads.detail", lid=lid))
    photos, videos = media_counts(pid)
    if not photos and not videos:
        flash("That listing has no photos or videos yet. Add them from its edit screen.",
              "error")
        return redirect(url_for("leads.detail", lid=lid))

    # Sending the same listing to the same client again reuses the link they
    # already have rather than piling up new ones.
    existing = query("SELECT * FROM media_shares WHERE lead_id = ? AND property_id = ?"
                     " AND revoked_at IS NULL AND expires_at > ? ORDER BY id DESC",
                     (lid, pid, now()), one=True)
    if existing:
        token = existing["token"]
        execute("UPDATE media_shares SET expires_at = ? WHERE id = ?",
                (_expiry(), existing["id"]))
    else:
        token = secrets.token_urlsafe(16)
        execute("INSERT INTO media_shares (token, property_id, lead_id, created_by,"
                " created_at, expires_at) VALUES (?,?,?,?,?,?)",
                (token, pid, lid, g.user["id"], now(), _expiry()))

    url = share_url(token)
    what = ", ".join(x for x in [
        f"{photos} photo{'' if photos == 1 else 's'}" if photos else "",
        f"{videos} video{'' if videos == 1 else 's'}" if videos else ""] if x)
    execute("INSERT INTO comments (entity_type, entity_id, user_id, body, created_at)"
            " VALUES ('lead',?,?,?,?)",
            (lid, g.user["id"],
             f"Sent {what} of {p['ref'] or ''} {p['title']} on WhatsApp.\n{url}".strip(),
             now()))
    execute("UPDATE leads SET last_contact_at = ?, updated_at = ? WHERE id = ?",
            (now(), now(), lid))
    log(g.user["id"], "Sent listing media on WhatsApp", "lead", lid,
        f"{p['ref'] or ''} {p['title']} — {what}".strip())

    template = get_setting("wa_media", "") or whatsapp.DEFAULTS["wa_media"]
    message = whatsapp.fill(
        template, name=lead["full_name"], property=p["title"], link=url,
        agent=g.user["name"],
        company=get_setting("company_name", "Planned Real Estate"),
        currency=get_setting("currency", "QAR"))
    # A template edited without {link} would send a message with nothing in it.
    if url not in message:
        message = f"{message}\n{url}"
    return redirect(whatsapp.link(lead["phone"], message, pick_contact=True))


@bp.route("/media-shares/<int:sid>/stop", methods=("POST",))
@login_required
def stop(sid):
    s = query("SELECT s.*, l.agent_id FROM media_shares s"
              " LEFT JOIN leads l ON l.id = s.lead_id WHERE s.id = ?", (sid,), one=True)
    if s is None:
        return redirect(url_for("leads.board"))
    if not (can_edit(s) or s["created_by"] == g.user["id"]):
        flash("That link was sent by another agent.", "error")
    else:
        execute("UPDATE media_shares SET revoked_at = ? WHERE id = ?", (now(), sid))
        log(g.user["id"], "Stopped sharing listing media", "lead", s["lead_id"])
        flash("That link no longer works. The client will see a 'link expired' page.", "ok")
    return redirect(url_for("leads.detail", lid=s["lead_id"]) if s["lead_id"]
                    else url_for("leads.board"))


def listing_link(p, user_id):
    """A photo link for one listing that isn't tied to any client: used on the
    printed vacant list, so agents can open or forward the photos.

    One live link per listing is reused (and kept alive another SHARE_DAYS)
    rather than making a new one on every print. The gallery shows the
    contact details of whoever the link belongs to, so it is the listing's
    agent where there is one. Returns None when there are no photos/videos.
    """
    photos, videos = media_counts(p["id"])
    if not photos and not videos:
        return None, 0
    existing = query("SELECT * FROM media_shares WHERE property_id = ?"
                     " AND lead_id IS NULL AND revoked_at IS NULL AND expires_at > ?"
                     " ORDER BY id DESC", (p["id"], now()), one=True)
    if existing:
        token = existing["token"]
        execute("UPDATE media_shares SET expires_at = ? WHERE id = ?",
                (_expiry(), existing["id"]))
    else:
        token = secrets.token_urlsafe(16)
        execute("INSERT INTO media_shares (token, property_id, lead_id, created_by,"
                " created_at, expires_at) VALUES (?,?,NULL,?,?,?)",
                (token, p["id"], p["agent_id"] or user_id, now(), _expiry()))
    return share_url(token), photos


def _expiry():
    return (datetime.utcnow() + timedelta(days=SHARE_DAYS)).strftime(STAMP)


# ---------------------------------------------------------- client side
@bp.route("/m/<token>")
def gallery(token):
    s = query("SELECT * FROM media_shares WHERE token = ?", (token,), one=True)
    if not _live(s):
        return render_template("media/gallery.html", expired=True,
                               company=get_setting("company_name", "Planned Real Estate"),
                               **_brand()), 410
    p = query("SELECT p.*, u.name AS agent_name, u.phone AS agent_phone,"
              " u.photo AS agent_photo FROM properties p"
              " LEFT JOIN users u ON u.id = ? WHERE p.id = ?",
              (s["created_by"], s["property_id"]), one=True)
    if p is None:
        return render_template("media/gallery.html", expired=True,
                               company=get_setting("company_name", "Planned Real Estate"),
                               **_brand()), 410
    images = query("SELECT id, filename FROM property_images WHERE property_id = ?"
                   " ORDER BY is_cover DESC, id", (p["id"],))
    videos = query("SELECT id, filename FROM property_videos WHERE property_id = ?"
                   " ORDER BY id", (p["id"],))

    agent_ua = (request.headers.get("User-Agent") or "").lower()
    if g.get("user") is None and not any(m in agent_ua for m in BOT_MARKERS):
        execute("UPDATE media_shares SET views = views + 1, last_viewed_at = ?"
                " WHERE id = ?", (now(), s["id"]))

    currency = get_setting("currency", "QAR")
    price = ""
    if p["price"]:
        price = f"{currency} {p['price']:,.0f}"
        if p["listing_type"] == "Rent":
            price += " / month · شهرياً"
    lead_name = None
    if s["lead_id"]:
        row = query("SELECT full_name FROM leads WHERE id = ?", (s["lead_id"],), one=True)
        lead_name = row["full_name"] if row else None
    reply = ""
    if p["agent_phone"]:
        reply = whatsapp.link(
            p["agent_phone"],
            f"Hello {p['agent_name']}, about {p['title']}"
            + (f" (Ref {p['ref']})" if p["ref"] else "") + " — ")

    # A rented unit coming free within a month is still being offered.
    import leases
    free = leases.free_from(p)
    available_from = (datetime.strptime(free, "%Y-%m-%d").strftime("%d %b %Y")
                      if free else None)

    from auth import _site_url
    base = _site_url()
    cover = (base + url_for("media.file", token=token, kind="images",
                            filename=images[0]["filename"])) if images else None
    return render_template(
        "media/gallery.html", expired=False, p=p, images=images, videos=videos,
        token=token, price=price, reply=reply, lead_name=lead_name,
        page_url=base + url_for("media.gallery", token=token), cover=cover,
        unavailable=p["status"] in ("Sold", "Rented") and not available_from,
        available_from=available_from,
        company=get_setting("company_name", "Planned Real Estate"), **_brand())


@bp.route("/m/<token>/<kind>/<path:filename>")
def file(token, kind, filename):
    """Serve one file, and only if it belongs to the shared listing — the
    token never opens anything else in the uploads folder."""
    s = query("SELECT * FROM media_shares WHERE token = ?", (token,), one=True)
    if not _live(s) or kind not in ("images", "videos"):
        abort(404)
    table = "property_images" if kind == "images" else "property_videos"
    owned = query(f"SELECT 1 FROM {table} WHERE property_id = ? AND filename = ?",
                  (s["property_id"], filename), one=True)
    if owned is None:
        abort(404)
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], kind)
    resp = send_from_directory(folder, filename, conditional=True, max_age=86400)
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _brand():
    from app import find_logo
    logo = find_logo()
    return {"logo_url": (f"{url_for('static', filename=logo[0])}?v={logo[1]}"
                         if logo else None)}
