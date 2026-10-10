"""One standard shape for every listing, whoever sent it.

Partners describe the same thing a dozen ways: "F/F", "Furnished", "FURNISHED",
"1 Bedroom Furnished"; "Sea View No Balcony", "SEA", "beach view"; "Including
Bills + Internet", "Utilities Are Not Including", "WIFI Including". Search can
only be as precise as what is stored, so these rules boil every variant down to
a short fixed list of values before anything is saved.

The same rules run in three places: the spreadsheet importer, the "Tidy
listings" clean-up for what is already in the CRM, and nowhere else — so a
listing typed in by hand is never rewritten behind anyone's back.

Every function returns None (or "") when the text genuinely says nothing,
never a guess, so the review screen can show what is missing.
"""
import re

import areas

NOISE = re.compile(r"[\s ]+")


def clean(value):
    if value is None:
        return ""
    return NOISE.sub(" ", str(value)).strip()


def joined(*values):
    return " | ".join(clean(v) for v in values if clean(v))


# ------------------------------------------------------------------- names
# Short words that stay as they are when a SHOUTED name is put in title case.
KEEP_UPPER = {"HBK", "AHB", "VB", "PA", "QD", "QAR", "UDC", "RP", "AP", "II", "III",
              "IV", "WB", "GF", "PH", "FF", "SF", "BHK", "BR", "LLC", "QSC", "WLL"}
KEEP_LOWER = {"and", "of", "the", "at", "in", "on", "by"}


def tidy_name(text):
    """'AIN GARDENS COMPOUND' -> 'Ain Gardens Compound'; mixed case is left
    alone, since 'Regency Pearl 2' or 'West Walk Residence' are already right.
    Codes such as 'AP21' or 'C-03' keep their capitals."""
    text = clean(text).strip(" -|•·,")
    if not text:
        return ""
    letters = [c for c in text if c.isalpha() and c.isascii()]
    if not letters or not all(c.isupper() for c in letters):
        return text
    words = []
    for i, w in enumerate(text.split(" ")):
        bare = re.sub(r"[^A-Za-z]", "", w)
        if bare in KEEP_UPPER or re.search(r"\d", w):
            words.append(w)
        elif i and w.lower() in KEEP_LOWER:
            words.append(w.lower())
        else:
            words.append(w[:1].upper() + w[1:].lower())
    return " ".join(words)


# --------------------------------------------------------------- furnishing
FURNISHING = ["Furnished", "Semi Furnished", "Unfurnished"]

_UNFURN = re.compile(r"\bun[\s-]?furnished\b|\bnot\s+furnished\b|\bu/f\b|\bunfurn\b"
                     r"|غير\s*مفروش", re.IGNORECASE)
_SEMI = re.compile(r"\bsemi[\s-]?furnished\b|\bsemi[\s-]?furn\b|\bs/f\b|\bsemi\b"
                   r"|نصف\s*مفروش|شبه\s*مفروش", re.IGNORECASE)
_FURN = re.compile(r"\bfully[\s-]?furnished\b|\bfurnished\b|\bf/f\b|\bff\b|\bfurn\b"
                   r"|مفروش", re.IGNORECASE)


def parse_furnishing(*values):
    """The first value that says anything wins, so pass the most specific
    column first (a FURNITURE column before a whole-building description)."""
    for v in values:
        text = clean(v)
        if not text:
            continue
        if _UNFURN.search(text):
            return "Unfurnished"
        if _SEMI.search(text):
            return "Semi Furnished"
        if _FURN.search(text):
            return "Furnished"
    return None


# --------------------------------------------------------------------- view
# canonical view -> words that mean it. Order matters only for display.
VIEWS = [
    ("Sea", r"sea|ocean|بحر"),
    ("Marina", r"marina|harbou?r|yacht"),
    ("Beach", r"beach|شاطئ"),
    ("Corniche", r"corniche|cornish|كورنيش"),
    ("Lagoon", r"lagoon"),
    ("Canal", r"canal"),
    ("Pool", r"pool|swimming"),
    ("Park", r"park|garden|green"),
    ("City", r"city|skyline|tower"),
    ("Porto Arabia", r"porto\s*arabia"),
    ("Building", r"building|bldg"),
    ("Road", r"road|street"),
    ("Entrance", r"entrance"),
    ("Side", r"side"),
    ("Front", r"front"),
]
VIEW_NAMES = [v for v, _ in VIEWS]
_VIEW_RE = [(name, re.compile(rf"\b(?:{pat})\w*", re.IGNORECASE)) for name, pat in VIEWS]


def parse_view(*values):
    """'City View, Side Sea View No balcony' -> 'Sea, City, Side'.

    Only text from a view or features column should be passed — a whole
    building description mentions the pool and the park as amenities, not as
    what the window looks onto."""
    found = []
    for v in values:
        text = clean(v)
        if not text:
            continue
        # balcony and furnishing notes share the cell; drop them first
        text = re.sub(r"(with(out)?|no)\s+(large\s+)?balcony|balcony", " ", text,
                      flags=re.IGNORECASE)
        for name, rx in _VIEW_RE:
            if rx.search(text) and name not in found:
                found.append(name)
    # "Side" alone is a real answer; next to a proper view it is only a qualifier
    if len(found) > 1:
        found = [f for f in found if f not in ("Side", "Front")] or found
    found.sort(key=VIEW_NAMES.index)
    return ", ".join(found) or None


# ------------------------------------------------------------------ balcony
_NO_BALC = re.compile(r"\b(no|without|w/o)\s+(large\s+)?balcon", re.IGNORECASE)
_BALC = re.compile(r"\bbalcon|\bjuliet\b|\bterrace\b|شرفة|بلكون", re.IGNORECASE)
YES = {"yes", "y", "yes ", "available", "with", "نعم"}
NO = {"no", "n", "none", "without", "لا"}


def parse_balcony(*values, column_value=None):
    """'Yes'/'No' from a BALCONY column, or from text like '1402 / No Balcony'."""
    col = clean(column_value).lower()
    if col in YES:
        return "Yes"
    if col in NO:
        return "No"
    for v in values:
        text = clean(v)
        if _NO_BALC.search(text):
            return "No"
        if _BALC.search(text):
            return "Yes"
    return None


# ------------------------------------------------------------------- bills
BILLS = ["All included", "Utilities included", "Internet only", "Not included"]

_U_OUT = re.compile(
    r"(utilit\w*|bills?|electricity|kahramaa)\s+(are\s+|is\s+)?(not|excluded|exclusive|"
    r"extra)|excluding\s+(the\s+)?(utilit\w*|bills?)|exclusive\s+of\s+(utilit\w*|bills?)|"
    r"(utilit\w*|bills?)\s+not\s+includ|without\s+(utilit\w*|bills?)|"
    r"(utilit\w*|bills?)\s+(to\s+be\s+)?paid\s+by\s+tenant", re.IGNORECASE)
_U_IN = re.compile(
    r"including\s+(the\s+)?(utilit\w*|bills?)|(utilit\w*|bills?)\s+(are\s+|is\s+)?"
    r"includ|inclusive\s+of\s+(utilit\w*|bills?)|all\s+inclusive|شامل", re.IGNORECASE)
_I_OUT = re.compile(r"\bno\s+(wi-?fi|internet)|(wi-?fi|internet)\s+(is\s+)?(not|excluded)",
                    re.IGNORECASE)
_I_IN = re.compile(r"(wi-?fi|internet)\s+(is\s+)?(includ|yes|free)|"
                   r"(including|includes|incl\.?|\+|with|high[\s-]speed)\s+(wi-?fi|internet)|"
                   r"free\s+(wi-?fi|internet)", re.IGNORECASE)
_SUPPLEMENT = re.compile(r"suppl?ement|surcharge|extra\s+charge", re.IGNORECASE)


def bills_flags(*values):
    """(utilities, internet), each True/False/None, from free text.

    Header-and-value pairs work too: 'WIFI: Yes', 'Utilities Supplement: 750'.
    Earlier values win, so a row's own cell overrides a note for the sheet.
    """
    util = net = None
    for v in values:
        text = clean(v)
        if not text:
            continue
        head, _, val = text.partition(":")
        low_val = val.strip().lower()
        if val and re.search(r"wi-?fi|internet", head, re.IGNORECASE):
            if net is None and low_val in YES:
                net = True
            elif net is None and low_val in NO:
                net = False
            continue
        if val and _SUPPLEMENT.search(head):
            if util is None and re.search(r"\d", low_val):
                util = False               # utilities charged on top of the rent
            continue
        if util is None:
            if _U_OUT.search(text):
                util = False
            elif _U_IN.search(text):
                util = True
        if net is None:
            if _I_OUT.search(text):
                net = False
            elif _I_IN.search(text):
                net = True
    return util, net


def bills_label(util, net):
    if util is True and net is True:
        return "All included"
    if util is True:
        return "Utilities included"
    if net is True:
        return "Internet only"
    if util is False:
        return "Not included"
    return None


def parse_bills(*values):
    return bills_label(*bills_flags(*values))


# -------------------------------------------------------------------- area
def find_area(*values):
    """The canonical district named anywhere in the text, or None.

    'Al Darwish Tower - West Bay' -> 'West Bay'; 'The Pearl-Qatar • ...' ->
    'The Pearl'; 'Ain Khaled / Salwa Road (Keys with security)' -> 'Ain Khaled'.
    The longest name wins, so 'Old Al Ghanim' beats 'Al Ghanim'."""
    for v in values:
        text = clean(v).replace("_", " ")
        if not text:
            continue
        hit = areas.lookup(text)
        if hit:
            return hit["en"]
        key = " " + _no_al(areas._key(text)) + " "
        # also try with every "al " removed, since aliases are stored without it
        key2 = re.sub(r"\b(al|el)\s+", "", key)
        for phrase, pair in _AREA_PHRASES:
            if f" {phrase} " in key or f" {phrase} " in key2:
                return pair["en"]
    return None


def _no_al(text):
    """Arabic names carry 'ال' on every word, and only the first is dropped
    when names are indexed; drop it everywhere so 'الخليج الغربي' matches."""
    return re.sub(r"(^|\s)ال", r"\1", text)


_AREA_PHRASES = sorted(
    ((_no_al(k), pair) for k, pair in areas._INDEX.items()
     if len(k) >= 4 or re.search(r"[\u0600-\u06FF]", k) and len(k) >= 2),
    key=lambda kv: -len(kv[0]))


def canonical_area(value):
    """(area, zone) from an area cell. A bare number is a Qatar zone, not a
    district name, so it is returned separately to go into the address."""
    text = clean(value)
    if not text:
        return "", ""
    if re.fullmatch(r"(zone\s*)?\d{1,3}", text, re.IGNORECASE):
        return "", "Zone " + re.sub(r"\D", "", text)
    hit = find_area(text)
    if hit:
        return hit, ""
    # keep what the partner wrote, minus notes in brackets, tidied
    return tidy_name(re.sub(r"\(.*?\)", "", text)), ""


# ------------------------------------------------------------------- title
def bed_label(beds):
    if beds is None:
        return ""
    return "Studio" if beds == 0 else f"{beds} Bed"


def standard_title(prop_type, bedrooms, building, unit, area=""):
    """Every listing reads the same way, whoever sent it:

        2 Bed Apartment · Giardino Building AP21 · Unit 2
        Studio · Regency Pearl 4 · Unit A201
        3 Bed Villa · Ain Gardens Compound · Unit C-03
        Office · Al Darwish Tower
    """
    kind = prop_type or "Apartment"
    beds = bed_label(bedrooms) if kind in ("Apartment", "Villa") else ""
    if beds == "Studio":
        head = "Studio"
    elif beds:
        head = f"{beds} {kind}"
    else:
        head = kind
    parts = [head]
    if building:
        parts.append(building)
    elif area:
        parts.append(area)
    if unit:
        parts.append(f"Unit {unit}")
    return " · ".join(parts)


# ------------------------------------------------------------------ search
_BEDS_Q = re.compile(r"\b(\d{1,2})\s*-?\s*(?:br|bhk|bed|beds|bedroom|bedrooms|b/r|bd|bdr)\b"
                     r"|\b(studio)s?\b|(ستوديو)|(\d)\s*غرف", re.IGNORECASE)
_TYPE_Q = [("Villa", r"villas?|compound|townhouse|فيلا"),
           ("Apartment", r"apartments?|flats?|شقة|شقق"),
           ("Office", r"offices?|مكتب"),
           ("Commercial", r"shops?|retail|showroom|warehouse|commercial|محل"),
           ("Land", r"land|plot|ارض|أرض")]
_FURN_Q = [("Unfurnished", r"un-?furnished|unfurn|غير مفروش"),
           ("Semi Furnished", r"semi[\s-]?furnished|semi"),
           ("Furnished", r"fully[\s-]?furnished|furnished|مفروش")]
_BILLS_Q = re.compile(r"(bills?|utilities)\s+included|including\s+(bills?|utilities)|"
                      r"all[\s-]inclusive|inclusive|شامل", re.IGNORECASE)
_LISTING_Q = [("Rent", r"for\s+rent|rent|rental|ايجار|إيجار"),
              ("Sale", r"for\s+sale|sale|buy|بيع")]
_STOP = {"in", "at", "with", "and", "the", "for", "a", "an", "view", "near", "of",
         "في", "مع"}


def parse_search(q):
    """Read a free-text search the way an agent types it.

        "2 bed furnished pearl sea view"  ->
            beds 2 · furnishing Furnished · area The Pearl · view Sea

    Returns (understood, words): understood is a dict of exact filters
    (area, beds, prop_type, furnishing, bills, listing_type, view) and words
    are whatever is left, each of which must still appear somewhere in the
    listing. Nothing is dropped: a word we don't recognise is searched for.
    """
    text = " " + clean(q).lower() + " "
    got = {}

    def take(rx):
        nonlocal text
        m = (rx.search(text) if hasattr(rx, "search")
             else re.search(rx, text, re.IGNORECASE))
        if m:
            text = text[:m.start()] + " " + text[m.end():]
        return m

    m = take(_BEDS_Q)
    if m:
        got["beds"] = 0 if (m.group(2) or m.group(3)) else int(m.group(1) or m.group(4))
    for value, rx in _FURN_Q:
        if take(rf"\b(?:{rx})\b"):
            got["furnishing"] = value
            break
    if take(_BILLS_Q):
        got["bills"] = True
    for value, rx in _LISTING_Q:
        if take(rf"\b(?:{rx})\b"):
            got["listing_type"] = value
            break
    for value, rx in _TYPE_Q:
        if take(rf"\b(?:{rx})\b"):
            got["prop_type"] = value
            break
    views = []
    for name, rx in _VIEW_RE:
        m = re.search(rf"\b{rx.pattern}\s+view\b|\bview\s+of\s+(?:the\s+)?{rx.pattern}",
                      text, re.IGNORECASE)
        if m:
            text = text[:m.start()] + " " + text[m.end():]
            views.append(name)
    if views:
        got["view"] = views

    # districts, longest name first, in either script
    key = " " + _no_al(areas._key(text)) + " "
    for phrase, pair in _AREA_PHRASES:
        if f" {phrase} " in key:
            got["area"] = pair["en"]
            got["area_words"] = phrase
            key = key.replace(f" {phrase} ", " ", 1)
            text = key
            break

    words = [w for w in re.split(r"[\s,;]+", text)
             if w and w not in _STOP and (len(w) > 1 or w.isdigit())]
    return got, words


def describe_search(got):
    """'2 Bed · The Pearl · Furnished · Sea view' for the line under the box."""
    parts = []
    if "beds" in got:
        parts.append(bed_label(got["beds"]))
    for k in ("prop_type", "area", "furnishing", "listing_type"):
        if got.get(k):
            parts.append(got[k])
    if got.get("bills"):
        parts.append("Bills included")
    for v in got.get("view", []):
        parts.append(f"{v} view")
    return " · ".join(parts)
