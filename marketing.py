"""Marketing performance: ad spend and reach pulled from Windsor.ai's
Connectors API, set next to the leads the CRM actually recorded from the
same channels over the same period.

Windsor.ai sits in front of the ad platforms behind one key and one small
REST API, so this module only has to speak to Windsor.ai rather than
integrate Meta, Google and every other ads platform separately. An admin
connects an ad account on the Windsor.ai side (windsor.ai) and pastes the
account's API key into Settings here; from then on this module reads
already-aggregated campaign figures for whatever date range the report
page asks for.

Zero new dependencies - uses urllib from the standard library, the same
approach as ai_intake.py's ask_model().
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from db import get_setting, local_now

SETTING_KEYS = ("windsor_api_key",)

API_BASE = "https://connectors.windsor.ai"
API_TIMEOUT = 20

# Ad-platform connectors mapped to the CRM's own lead "source" values (see
# db.LEAD_SOURCES), so spend on a channel can be set next to the leads it
# actually produced. Meta serves both Facebook and Instagram placements from
# one "facebook" connector, and boosted posts commonly click through to
# WhatsApp, so both CRM source labels count toward it. Add another entry
# here (and connect it on windsor.ai) if a Google Ads or TikTok account
# joins later - the report page and settings card both read this dict.
CONNECTORS = {
    "facebook": {"label": "Meta Ads (Facebook & Instagram)",
                 "lead_sources": ["Instagram", "WhatsApp"]},
}

FIELDS = ["date", "campaign", "spend", "impressions", "clicks", "reach", "currency"]

RANGES = [
    ("last_7d", "Last 7 days"),
    ("last_30d", "Last 30 days"),
    ("last_90d", "Last 90 days"),
    ("this_month", "This month"),
    ("last_month", "Last month"),
]


def range_dates(key):
    """(date_from, date_to) as YYYY-MM-DD strings, plus a UTC window for
    querying the CRM's own leads table over the same period."""
    today = local_now().date()
    if key == "last_7d":
        start = today - timedelta(days=6)
    elif key == "last_90d":
        start = today - timedelta(days=89)
    elif key == "this_month":
        start = today.replace(day=1)
    elif key == "last_month":
        first = today.replace(day=1)
        last_end = first - timedelta(days=1)
        start = last_end.replace(day=1)
        return start.isoformat(), last_end.isoformat()
    else:
        key = "last_30d"
        start = today - timedelta(days=29)
    return start.isoformat(), today.isoformat()


def settings():
    return {k: (get_setting(k, "") or "") for k in SETTING_KEYS}


def api_key_present():
    return bool(get_setting("windsor_api_key", "").strip())


def _request(connector, date_from, date_to):
    key = get_setting("windsor_api_key", "").strip()
    params = {
        "api_key": key,
        "fields": ",".join(FIELDS),
        "date_from": date_from,
        "date_to": date_to,
    }
    url = f"{API_BASE}/{connector}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(connector, date_from, date_to):
    """Returns (ok, rows_or_message). Never raises at the caller."""
    if not api_key_present():
        return False, ("No Windsor.ai key is set up yet. An admin can add "
                       "one under Settings.")
    try:
        payload = _request(connector, date_from, date_to)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "Windsor.ai rejected that key. Check it under Settings."
        return False, f"Windsor.ai returned an error (HTTP {exc.code})."
    except urllib.error.URLError as exc:
        return False, f"Could not reach Windsor.ai: {exc.reason}"
    except (ValueError, json.JSONDecodeError):
        return False, "Windsor.ai's response couldn't be read."

    rows = payload.get("data") if isinstance(payload, dict) else None
    if rows is None:
        # Some Windsor.ai error responses come back 200 OK with an
        # {"error": "..."} body rather than a non-2xx status.
        msg = payload.get("error") if isinstance(payload, dict) else None
        return False, msg or "Windsor.ai's response was not in the expected format."
    return True, rows


def test_connection():
    """Prove the key works with a cheap one-week pull, without assuming any
    particular connector is already attached to the account."""
    if not api_key_present():
        return False, "Paste an API key first."
    date_from, date_to = range_dates("last_7d")
    ok, result = fetch("facebook", date_from, date_to)
    if ok:
        return True, "Connected. Meta Ads data is reachable."
    return False, result


def summarize(rows):
    """Roll per-row campaign/day figures up into one set of totals plus a
    per-campaign breakdown, sorted by spend."""
    spend = sum(r.get("spend") or 0 for r in rows)
    impressions = sum(r.get("impressions") or 0 for r in rows)
    clicks = sum(r.get("clicks") or 0 for r in rows)
    reach = sum(r.get("reach") or 0 for r in rows)
    ctr = (clicks / impressions * 100) if impressions else None
    cpc = (spend / clicks) if clicks else None
    currency = next((r.get("currency") for r in rows if r.get("currency")), None)

    by_campaign = {}
    for r in rows:
        name = r.get("campaign") or "—"
        c = by_campaign.setdefault(
            name, {"spend": 0, "impressions": 0, "clicks": 0, "reach": 0})
        c["spend"] += r.get("spend") or 0
        c["impressions"] += r.get("impressions") or 0
        c["clicks"] += r.get("clicks") or 0
        c["reach"] += r.get("reach") or 0
    campaigns = sorted(
        ({"name": k, **v} for k, v in by_campaign.items()),
        key=lambda c: c["spend"], reverse=True)

    return {
        "spend": round(spend, 2), "impressions": impressions, "clicks": clicks,
        "reach": reach, "ctr": ctr, "cpc": cpc, "currency": currency,
        "campaigns": campaigns, "has_data": bool(rows),
    }
