"""Pre-filled WhatsApp messages.

Admins edit the wording under Settings. Placeholders in curly braces are swapped
for real values when the button is drawn.
"""
import re
from urllib.parse import quote

DEFAULTS = {
    "wa_intro": (
        "Hello {name}, this is {agent} from {company}. "
        "Here are the details of {property} — {currency} {price}. "
        "Let me know if you would like to arrange a viewing."
    ),
    "wa_followup": (
        "Hello {name}, {agent} from {company} here. "
        "Just following up on the properties we discussed. "
        "Are you still looking, and has anything changed in what you need?"
    ),
    "wa_viewing": (
        "Hello {name}, confirming your viewing of {property} on {date}. "
        "I will meet you there. Call me on this number if anything changes. — {agent}"
    ),
    "wa_media": (
        "Hello {name}, this is {agent} from {company}. "
        "Here are the photos and videos of {property}.\n"
        "مرحباً {name}، إليك صور وفيديوهات العقار.\n"
        "{link}"
    ),
    "wa_owner": (
        "Hello {name}, this is {agent} from {company} regarding your property "
        "{property}. Do you have a moment to talk?"
    ),
}

LABELS = {
    "wa_intro": "Send listing details",
    "wa_followup": "Follow up",
    "wa_viewing": "Confirm viewing",
    "wa_media": "Send photos & videos",
    "wa_owner": "Contact owner",
}

PLACEHOLDERS = ["{name}", "{agent}", "{company}", "{property}", "{price}",
                "{currency}", "{date}", "{link}"]


def fill(template, **values):
    """Swap placeholders for values, leaving unknown ones blank rather than crashing."""
    def sub(match):
        return str(values.get(match.group(1), "") or "")
    text = re.sub(r"\{(\w+)\}", sub, template or "")
    return re.sub(r"\s{2,}", " ", text).strip()


def digits(phone):
    """wa.me wants digits only, no plus sign, spaces or dashes."""
    return re.sub(r"\D", "", str(phone or ""))


def link(phone, message, pick_contact=False):
    """A wa.me deep link. With pick_contact, a missing number still gives a
    link — WhatsApp then opens its own contact picker instead of a chat."""
    number = digits(phone)
    if not number:
        return f"https://wa.me/?text={quote(message)}" if pick_contact else ""
    return f"https://wa.me/{number}?text={quote(message)}"
