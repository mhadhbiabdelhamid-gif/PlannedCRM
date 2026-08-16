"""Google Maps links pasted in by agents.

Google hands out several shapes of link depending on how it was shared, and
people sometimes paste bare coordinates instead. All of them are accepted; a
link to somewhere that isn't Maps is refused, so a stray clipboard copy doesn't
end up saved as a location.
"""
import re

COORD = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

ALLOWED_HOSTS = (
    "google.com/maps", "google.com/maps",
    "maps.google.", "www.google.com/maps",
    "maps.app.goo.gl", "goo.gl/maps",
    "g.co/kgs",
)


def normalise(raw):
    """Return (ok, value_or_message).

    An empty string is fine — it just means no location yet.
    """
    text = (raw or "").strip()
    if not text:
        return True, ""

    # bare coordinates, e.g. "25.3702, 51.5487" straight off a phone
    m = COORD.match(text)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return False, "Those coordinates are out of range."
        return True, f"https://maps.google.com/?q={lat},{lng}"

    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text

    low = text.lower()
    if not any(h in low for h in ALLOWED_HOSTS):
        return False, ("That doesn't look like a Google Maps link. In Google Maps "
                       "tap Share, then Copy link, and paste that here — or paste "
                       "the coordinates, like 25.3702, 51.5487")
    if len(text) > 1000:
        return False, "That link is too long to store."
    return True, text
