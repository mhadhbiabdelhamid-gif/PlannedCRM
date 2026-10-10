"""Reading partner availability lists.

Every agency sends a different shape: headers three rows down under a logo,
merged section rows, unit numbers buried in codes like ARPQ02-B00-F01-A101,
rents written as "9000 qrs", sizes as "113 sqm". This module works out the
layout, guesses which column is which, and pulls out clean values. Nothing is
saved until a person has looked at the preview and agreed.
"""
import itertools
import re

from openpyxl import load_workbook

import areas as areas_module
import normalize
from normalize import joined

# Fields the CRM can fill from a spreadsheet.
FIELDS = [
    ("unit_no", "Flat / unit number"),
    ("building_no", "Building"),
    ("floor_no", "Floor"),
    ("title", "Title or property name"),
    ("prop_type", "Property type"),
    ("bedrooms", "Bedrooms"),
    ("bathrooms", "Bathrooms"),
    ("size_sqm", "Size"),
    ("price", "Rent or price"),
    ("status", "Status"),
    ("area", "Location / district"),
    ("description", "Description or notes"),
    ("map_url", "Map link"),
    ("address", "Street, building no., zone"),
    ("furnishing", "Furnishing"),
    ("view", "View"),
    ("balcony", "Balcony"),
    ("bills", "Bills / internet included"),
    ("offer", "Offer (e.g. 1 month free)"),
    ("features", "Features, amenities"),
    ("extras", "Extra rooms (office, maid's)"),
]

# Words that suggest a column holds a given field. Matched against the header
# text, lowercased, longest first so "unit size" beats "unit".
SYNONYMS = {
    "unit_no": ["unit number", "unit no", "apt no", "apartment no", "flat no",
                "unit #", "unit", "apartment number", "flat number", "door no"],
    "building_no": ["building name", "property name", "building no", "building",
                    "tower", "block", "compound", "project"],
    "floor_no": ["floor number", "floor no", "floor", "level", "storey", "story",
                 "flr"],
    "extras": ["extra rooms", "additional rooms", "extras", "maid room",
               "maid's room", "additional"],
    "title": ["property", "description of property", "name", "unit type name"],
    "prop_type": ["property type", "apartment type", "type of property", "type",
                  "category"],
    "bedrooms": ["no. bedroom", "no of bedroom", "number of bedrooms", "bedrooms",
                 "bedroom", "no. of beds", "beds", "bed", "layout", "bhk",
                 "configuration"],
    "bathrooms": ["bathrooms", "bathroom", "baths", "bath", "no. of baths"],
    "size_sqm": ["unit size", "size sqm", "built up area", "area sqm", "sqm",
                 "sq.m", "sq m", "size", "built-up", "bua"],
    "price": ["monthly rent", "rent per month", "asking price", "rate", "rent",
              "price", "amount", "monthly", "sale price"],
    "status": ["booking status", "availability", "status", "vacant", "available"],
    "area": ["location", "district", "area", "neighbourhood", "neighborhood"],
    "description": ["description", "remarks", "notes", "comment", "details",
                    "maintenance status", "maintinance status", "broker note",
                    "caretaker", "keys with", "contact person"],
    "map_url": ["map", "google map", "location link", "maps link", "pin"],
    "address": ["street name", "street no", "street", "bldg #", "bldg no", "bldg",
                "zone", "address", "plot"],
    "furnishing": ["furniture", "furnishing", "furnished"],
    "view": ["view", "facing"],
    "balcony": ["balcony"],
    "bills": ["included in rent", "utilities", "utilites", "utility", "wifi",
              "wi-fi", "internet", "bills"],
    "offer": ["month free", "free month", "promotion", "promo", "offer"],
    "features": ["features", "amenities", "facilities"],
}

# Fields that may legitimately come from several columns at once: a partner
# splits the address over street / building / zone, or keeps WIFI and a
# utilities charge in two columns.
MULTI = {"address", "bills", "description", "features"}

# Header text that means the row is a header rather than data.
HEADER_HINTS = set()
for words in SYNONYMS.values():
    HEADER_HINTS.update(words)

NOISE = re.compile(r"[\s\u00a0]+")


def clean(value):
    if value is None:
        return ""
    return NOISE.sub(" ", str(value)).strip()


# ------------------------------------------------------------------ parsing
WORD_NUMBERS = {
    "studio": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


# Every way a partner might write a bedroom count. Longest first so "bedroom"
# is matched before "bed", and "bdrm" before "bd".
BED_WORDS = (r"bedrooms?|bed\s*rooms?|bedrms?|bdrms?|bdms?|bdrs?|"
             r"b\s*/\s*r|bhk|beds?|bds?|brs?|rooms?|غرف(?:ة|تين)?|غرفه")

# Extra rooms that are not bedrooms, so "1 bd + off" is one bedroom, not two.
EXTRAS = re.compile(
    r"\+?\s*\b(off(?:ice)?|study|maid[' ]?s?(?:\s*room)?|hall|majlis|store|"
    r"storage|driver[' ]?s?(?:\s*room)?|laundry|balcony|terrace|garden|"
    r"pantry|nanny)\b", re.IGNORECASE)

STUDIO_WORDS = re.compile(r"\bstudios?\b|\bstd\b|\bستوديو\b", re.IGNORECASE)

# Units and words that follow a number but have nothing to do with bedrooms.
# Without these, "50 sqm" was being read as fifty bedrooms.
NOT_BEDROOMS = re.compile(
    r"\b(sq\.?\s*m|sqm|sqft|sq\.?\s*ft|m2|m²|ft2|meters?|metres?|"
    r"qar?|qr|riyals?|aed|usd|k|kw|kwh|"
    r"floors?|flr|storey?s?|levels?|"
    r"parking|car\s*parks?|spaces?|years?|months?|days?|"
    r"units?|shops?|offices?)\b", re.IGNORECASE)

MAX_BEDROOMS = 15          # beyond this it is not a flat, it is a data error


def parse_bedrooms(value):
    """Read a bedroom count out of however the partner wrote it.

    Handles '1 bd+off', '2BHK+Maid', 'Studio', 'Two Bedrooms', '3 Beds; 5 Baths',
    '1-BR', '2 bdr', '3 R', '2 غرفة' and a plain number. Extra rooms — office,
    maid's, study — are recognised so they are not counted as bedrooms.

    Returns None when there is genuinely no count, never 0 as a guess, so the
    review screen can flag it rather than quietly inventing a studio.
    """
    text = clean(value)
    if not text:
        return None

    if STUDIO_WORDS.search(text):
        return 0

    # strip the extras first, so their words cannot be mistaken for a count
    stripped = EXTRAS.sub(" ", text)

    # a digit attached to a bedroom word: "2BHK", "1 bd", "3-BR", "4 bed rooms"
    m = re.search(rf"(\d+)\s*[-+/]?\s*(?:{BED_WORDS})\b", stripped, re.IGNORECASE)
    if m:
        return _sane(int(m.group(1)))

    # the word before the digit: "bedrooms: 3", "BR 2"
    m = re.search(rf"(?:{BED_WORDS})\s*[:=-]?\s*(\d+)", stripped, re.IGNORECASE)
    if m:
        return _sane(int(m.group(1)))

    # spelled out: "Two Bedrooms", "Three bedroom villa", "Two BR"
    for word, n in WORD_NUMBERS.items():
        if re.search(rf"\b{word}\b\s*[-]?\s*(?:{BED_WORDS})\b", stripped,
                     re.IGNORECASE):
            return n

    # anything measured in square metres, riyals, floors and so on is not a
    # bedroom count, however it is written
    if NOT_BEDROOMS.search(stripped):
        return None

    # a bare count where the whole cell is a number: "2", "0"
    m = re.match(r"^\s*(\d{1,2})\s*$", stripped)
    if m:
        return _sane(int(m.group(1)))

    # "2+1" — the first figure is the bedrooms, the second the living rooms
    m = re.match(r"^\s*(\d{1,2})\s*\+\s*\d{1,2}\s*$", stripped)
    if m:
        return _sane(int(m.group(1)))

    # a lone digit next to nothing else meaningful: "3 R", "4 rm"
    m = re.match(r"^\s*(\d{1,2})\s*(?:r|rm|rms|غ)?\s*$", stripped, re.IGNORECASE)
    if m:
        return _sane(int(m.group(1)))

    return None


def _sane(n):
    """A flat with forty bedrooms means the column was the wrong one."""
    return n if n is not None and 0 <= n <= MAX_BEDROOMS else None


def describe_extras(value):
    """The extra rooms mentioned, so they land in features instead of vanishing."""
    text = clean(value)
    if not text:
        return ""
    found = []
    for m in EXTRAS.finditer(text):
        word = m.group(1).strip().title()
        word = {"Off": "Office", "Maid": "Maid's room",
                "Maids": "Maid's room", "Driver": "Driver's room"}.get(word, word)
        if word not in found:
            found.append(word)
    return ", ".join(found)


BATH_WORDS = r"bathrooms?|bath\s*rooms?|bathrms?|baths?|washrooms?|wc|w\.c\.|ba\b|حمام(?:ات)?"


def parse_bathrooms(value):
    text = clean(value)
    if not text:
        return None
    m = re.search(rf"(\d+)\s*[-+/]?\s*(?:{BATH_WORDS})", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(rf"(?:{BATH_WORDS})\s*[:=-]?\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    for word, n in WORD_NUMBERS.items():
        if re.search(rf"\b{word}\b\s*[-]?\s*(?:{BATH_WORDS})", text, re.IGNORECASE):
            return n
    return None


def parse_number(value):
    """Pulls a figure out of '9000 qrs', 'QR 8,500', '113 sqm', 8500."""
    if isinstance(value, (int, float)):
        return float(value)
    text = clean(value)
    if not text:
        return None
    text = text.replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


UNIT_CODE = re.compile(r"[A-Z]{2,}\d*-[A-Z0-9]+-[A-Z0-9]+-([A-Z]*\d+[A-Z]*)",
                       re.IGNORECASE)


def parse_unit(value):
    """Unit numbers arrive in several disguises.

    'ARPQ02-B00-F01-A101' -> A101      (the last segment is the door number)
    '311 / Balcony'       -> 311
    'Villa No. A-15 (inside)' -> A-15
    'Flat No. 48'         -> 48
    """
    text = clean(value)
    if not text:
        return ""

    m = UNIT_CODE.search(text)
    if m:
        return m.group(1).upper()

    # strip a leading label
    text = re.sub(r"^(villa|flat|apartment|apt|unit|shop|office)\s*(no\.?|#|number)?\s*",
                  "", text, flags=re.IGNORECASE).strip()
    # drop anything after a separator: "311 / Balcony", "1707 No Balcony"
    text = re.split(r"\s*[/|,]\s*|\s{2,}", text)[0].strip()
    text = re.sub(r"\((.*?)\)", "", text).strip()
    m = re.match(r"^([A-Za-z]?[-\s]?\d+[A-Za-z]?(?:-\d+)?)", text)
    if m:
        return m.group(1).replace(" ", "").upper()
    return text[:20]


FLOOR_WORDS = {
    "ground": "G", "gf": "G", "g": "G", "mezzanine": "M", "mezz": "M",
    "basement": "B", "penthouse": "PH", "roof": "R",
}

ORDINAL = re.compile(r"^\s*(\d{1,3})\s*(?:st|nd|rd|th)?\s*(?:floor|flr|level)?\s*$",
                     re.IGNORECASE)
FLOOR_IN_CODE = re.compile(r"-F(\d{1,3})-", re.IGNORECASE)


def parse_floor(value, unit_code=""):
    """'1st', 'Ground', 'G', 'B1', 'Floor 12', or hidden inside a unit code
    like ARPQ02-B00-F01-A101, where F01 is the first floor."""
    text = clean(value)
    if not text:
        m = FLOOR_IN_CODE.search(clean(unit_code))
        return str(int(m.group(1))) if m else ""

    low = text.lower().strip(" .-")
    if low in FLOOR_WORDS:
        return FLOOR_WORDS[low]
    for word, short in FLOOR_WORDS.items():
        if re.fullmatch(rf"{word}\s*floor", low):
            return short

    m = ORDINAL.match(text)
    if m:
        return str(int(m.group(1)))
    m = re.search(r"(?:floor|flr|level|storey|story)\s*[:.\-]?\s*(\d{1,3})",
                  text, re.IGNORECASE)
    if m:
        return str(int(m.group(1)))
    m = re.match(r"^\s*([BM]\s?\d{1,2}|PH\d?)\s*$", text, re.IGNORECASE)
    if m:
        return m.group(1).upper().replace(" ", "")
    return text[:12]


STATUS_WORDS = [
    (("vacant", "available", "ready", "free", "rfo"), "Available"),
    (("booked", "reserved", "on hold", "hold", "under offer"), "Reserved"),
    (("rented", "leased", "occupied", "let"), "Rented"),
    (("sold",), "Sold"),
]


def parse_status(value, default="Available"):
    text = clean(value).lower()
    if not text:
        return default
    for words, status in STATUS_WORDS:
        if any(w in text for w in words):
            return status
    return default


TYPE_WORDS = [
    (("villa", "compound", "townhouse"), "Villa"),
    (("office", "commercial office"), "Office"),
    (("retail", "shop", "showroom", "commercial", "warehouse"), "Commercial"),
    (("land", "plot"), "Land"),
    (("apartment", "flat", "studio", "bhk", "br", "penthouse", "residential"),
     "Apartment"),
]


def parse_type(*values, default="Apartment"):
    joined = " ".join(clean(v).lower() for v in values if clean(v))
    for words, kind in TYPE_WORDS:
        if any(w in joined for w in words):
            return kind
    return default


MAP_RE = re.compile(r"https?://\S*(?:google\.[a-z.]+/maps|maps\.app\.goo\.gl|"
                    r"goo\.gl/maps|maps\.google)\S*", re.IGNORECASE)


def find_map_link(*values):
    for v in values:
        text = clean(v)
        m = MAP_RE.search(text)
        if m:
            return m.group(0).rstrip(").,;")
    return ""


# -------------------------------------------------------- layout detection
def score_header_row(cells):
    """How much does this row look like a set of column headings?"""
    score = 0
    filled = 0
    for cell in cells:
        text = clean(cell).lower()
        if not text:
            continue
        filled += 1
        if len(text) > 45:                 # a sentence, not a heading
            score -= 2
            continue
        for hint in HEADER_HINTS:
            if hint in text or text in hint:
                score += 3
                break
        else:
            if not re.match(r"^[\d.,\s]+$", text):
                score += 0.5               # a short word is plausible
    if filled < 2:
        return -10
    return score


def detect_header_row(ws, limit=20, col_range=None):
    c_start, c_end = col_range or (1, min(ws.max_column, 25))
    best, best_score = 1, -99
    for r in range(1, min(ws.max_row, limit) + 1):
        cells = [ws.cell(row=r, column=c).value
                 for c in range(c_start, c_end + 1)]
        s = score_header_row(cells)
        if s > best_score:
            best, best_score = r, s
    return best


def find_column_blocks(ws, gap=2, scan_rows=60, max_col=60, max_block_width=25):
    """Partners sometimes paste two buildings' lists side by side on one
    sheet instead of stacking them, so the second table's columns start
    partway across the row rather than at column A. Treated as one table,
    that column offset gets read as extra columns of the first table and
    the second table's own header is read as a data row — its fields never
    get mapped, so most of it goes missing.

    Two independent signals catch that, since either can show up on its own:

    1. Columns: a run of `gap` or more columns that are blank across every
       one of the first `scan_rows` rows is a divider between tables (a
       single blank spacer column inside one table is normal and doesn't
       split anything on its own).
    2. Headers: the header row's own labels starting over — the same first
       heading ("Floor", "Unit No.", ...) appearing again further across the
       same row — is the tell for two tables separated by only a single
       spacer column, which the blank-run check alone wouldn't catch (seen
       in real partner files: a "Studio" block and a "One Bedroom" block,
       each headed Floor/Unit No./Rate/Status, one column apart).

    A normal single-table sheet trips neither signal and returns one block
    spanning the whole width, so this changes nothing for the common case.
    """
    ncols = min(ws.max_column, max_col)
    if ncols < 1:
        return [(1, 1)]
    last_row = min(ws.max_row, scan_rows) or 1

    # Signal 1: which columns fall inside a run of `gap`-or-more blank ones —
    # those columns belong to neither table and are carved out entirely.
    empty = [all(not clean(ws.cell(row=r, column=c).value)
                 for r in range(1, last_row + 1))
             for c in range(1, ncols + 1)]
    in_gap = [False] * ncols
    col = 1
    for is_empty, group in itertools.groupby(empty):
        length = sum(1 for _ in group)
        if is_empty and length >= gap:
            for c in range(col, col + length):
                in_gap[c - 1] = True
        col += length

    # Signal 2: columns where the header row's own labels start over — a new
    # table's header beginning, even a single column after the last one ends.
    header_row = detect_header_row(ws, col_range=(1, ncols))
    headers = [clean(ws.cell(row=header_row, column=c).value).lower()
               for c in range(1, ncols + 1)]
    repeat_starts = set()
    first = headers[0] if headers else ""
    second = headers[1] if len(headers) > 1 else ""
    if first:
        for i in range(2, len(headers)):
            if headers[i] != first:
                continue
            nxt = headers[i + 1] if i + 1 < len(headers) else ""
            if second and nxt and second != nxt:
                continue                          # looks coincidental
            repeat_starts.add(i + 1)              # 1-based column of the repeat

    blocks = []
    cur_start = None
    for c in range(1, ncols + 1):
        if in_gap[c - 1]:
            if cur_start is not None:
                blocks.append((cur_start, c - 1))
                cur_start = None
            continue
        if c in repeat_starts and cur_start is not None:
            blocks.append((cur_start, c - 1))
            cur_start = c
        elif cur_start is None:
            cur_start = c
    if cur_start is not None:
        blocks.append((cur_start, ncols))
    if not blocks:
        blocks = [(1, ncols)]

    # A sliver with almost nothing in it is stray notes, not a second table.
    real = []
    for s, e in blocks:
        filled = sum(1 for r in range(1, last_row + 1) for c in range(s, e + 1)
                     if clean(ws.cell(row=r, column=c).value))
        if filled >= 3:
            real.append((s, min(e, s + max_block_width - 1)))
    return real or blocks


# Fields where several columns should be joined together rather than one
# winning. A partner may split an address or a description across columns.
JOINABLE = {"title", "description", "features", "address", "area",
            "building_no"}


def as_columns(value):
    """Mapping values may be a single column or several. Always return a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [int(v) for v in value if str(v).strip().isdigit() and int(v) > 0]
    return [int(value)] if str(value).strip().isdigit() and int(value) > 0 else []


def guess_mapping(headers):
    """headers: list of header strings. Returns {field: column index (1-based)}."""
    mapping = {}
    used = set()
    # longest synonyms first, so "unit size" wins over "unit"
    ordered = sorted(
        ((field, syn) for field, syns in SYNONYMS.items() for syn in syns),
        key=lambda pair: -len(pair[1]))

    for field, syn in ordered:
        if field in mapping:
            continue
        for i, head in enumerate(headers, start=1):
            if i in used:
                continue
            text = clean(head).lower()
            if not text:
                continue
            if text == syn or text.startswith(syn) or syn in text:
                mapping[field] = i
                used.add(i)
                break
    # second pass: extra columns for fields that can join several
    for field, syn in ordered:
        if field not in MULTI:
            continue
        for i, head in enumerate(headers, start=1):
            if i in used:
                continue
            text = clean(head).lower()
            if text and (text == syn or text.startswith(syn) or syn in text):
                current = as_columns(mapping.get(field))
                mapping[field] = current + [i] if current else i
                used.add(i)
    return mapping


UNIT_LABEL = re.compile(r"^\s*(villa|flat|apartment|apt|unit|shop|office|studio)\b",
                        re.IGNORECASE)


def infer_from_values(headers, rows, mapping):
    """Some columns have no heading at all — the Pearl list keeps bedroom
    descriptions ('Studio', '1br+Off') under a blank header. Judge those by
    what is in them instead."""
    used = {c for v in mapping.values() for c in as_columns(v)}
    for i in range(1, len(headers) + 1):
        if i in used:
            continue
        sample = [clean(r[i - 1]) for r in rows[:25] if i <= len(r)]
        sample = [v for v in sample if v]
        if len(sample) < 2:
            continue

        if "bedrooms" not in mapping:
            hits = sum(1 for v in sample
                       if re.search(r"studio|\d\s*(br|bhk|bed)", v, re.IGNORECASE))
            if hits >= max(2, len(sample) * 0.6):
                mapping["bedrooms"] = i
                used.add(i)
                continue

        if "status" not in mapping:
            words = {w for group, _ in STATUS_WORDS for w in group}
            hits = sum(1 for v in sample if v.lower() in words)
            if hits >= max(2, len(sample) * 0.7):
                mapping["status"] = i
                used.add(i)
                continue

        # A column of door numbers under a vague heading ('Room', '#'):
        # mostly 2-4 digit numbers, all different, and not just 1, 2, 3...
        # counting down the rows, which is a row counter rather than a flat.
        if "unit_no" not in mapping:
            nums = [v for v in sample if re.fullmatch(r"[A-Za-z]?\d{2,4}[A-Za-z]?", v)]
            if len(nums) >= max(3, len(sample) * 0.8) and len(set(nums)) == len(nums):
                digits = [int(re.sub(r"\D", "", v)) for v in nums]
                counting = digits == list(range(digits[0], digits[0] + len(digits)))
                if not counting:
                    mapping["unit_no"] = i
                    used.add(i)
    return mapping


STRONG = ("building", "tower", "compound", "block", "project")
WEAK = ("residence", "villa", "apartments", "gardens", "plaza")


TITLE_NOISE = re.compile(
    r"\b(availability|vacant|vacancy|property|properties|available)?\s*"
    r"(list(ing)?s?|rates?|offers?)\b.*$|\bcoming soon\b\s*-?|"
    r"\byearly contract.*$|\bbroker offer.*$|\(.*?\)|\d{1,2}[-./]\d{1,2}[-./]\d{2,4}",
    re.IGNORECASE)
NOT_A_BUILDING = ("including", "included", "utilit", "wifi", "internet", "promotion",
                  "free", "updated", "note", "commission", "broker", "why ", "starting",
                  "available units", "residential", "commercial", "compounds", "flats",
                  "villas", "prorated", "excluding", "subject to", "contract")


def clean_title(text):
    """'Retaj La Plage Availability List' -> 'Retaj La Plage';
    'RETAJ BAYWALK RESIDENCE | YEARLY CONTRACT OFFER' -> 'Retaj Baywalk Residence'."""
    text = clean(text)
    text = re.split(r"\s*[|•]\s*", text)[0]
    text = TITLE_NOISE.sub("", text)
    return normalize.tidy_name(text.strip(" -–:"))


def split_building_area(text):
    """'Al Darwish Tower - West Bay' -> ('Al Darwish Tower', 'West Bay')."""
    text = clean(text)
    if not text:
        return "", ""
    parts = re.split(r"\s+[-–,]\s+|\s*,\s*", text)
    if len(parts) > 1:
        tail = normalize.find_area(parts[-1])
        if tail and not normalize.find_area(" ".join(parts[:-1])):
            return " - ".join(parts[:-1]).strip(), tail
    return text, normalize.find_area(text) or ""


def guess_context(ws, header_row, col_range=None):
    """Partners often name the building in a title row above the table rather
    than giving it a column. Rows nearest the table win, and a line saying
    'Building' beats a general banner."""
    c_start, c_end = col_range or (1, min(ws.max_column, 8))
    c_end = min(c_end, c_start + 7)
    best, best_score = "", 0
    first_text_row = next((r for r in range(1, header_row)
                           if any(clean(ws.cell(row=r, column=c).value)
                                  for c in range(c_start, c_end + 1))), 0)
    for r in range(header_row - 1, 0, -1):
        for c in range(c_start, c_end + 1):
            raw = clean(ws.cell(row=r, column=c).value)
            if not raw or raw.startswith(("✓", "*", "-", "•")):
                continue
            text = clean_title(raw)
            if not text or len(text) < 4 or len(text) > 60:
                continue
            if re.match(r"^[\d\s./-]+$", text):
                continue
            low = text.lower()
            if any(w in low for w in NOT_A_BUILDING):
                continue                      # a banner, not a building
            if parse_bedrooms(text) is not None and len(text) < 25:
                continue                      # a 'Studio' / 'One Bedroom' heading
            if re.fullmatch(r"[a-z]+\s+\d{4}|\d+\s*(weeks?|months?|days?)", low):
                continue                      # 'October 2026', '2 Weeks'
            score = 1
            if r == first_text_row:
                score += 1.5                  # the sheet's own title line
            if any(w in low for w in STRONG):
                score = 4
            elif any(w in low for w in WEAK):
                score = 2
            score += (header_row - r) * 0.01   # nearer the table is better
            if score > best_score:
                best, best_score = text, score
    return best


BANNER_SKIP_WORDS = ("list", "availability", "rates", "including", "coming soon",
                     "updated", "note", "free", "promotion", "prorated",
                     "applicable")


def guess_banner_bedrooms(ws, header_row, col_range, scan_rows=6):
    """Some lists group units under a section heading that names the bedroom
    count instead of giving each row its own bedrooms column — 'Studio' or
    'One Bedroom' sitting above a block of otherwise identical Floor / Unit
    No. / Rate / Status columns (seen in a real partner file: Retaj La
    Plage). When a row's own cells give no bedroom count, this is the
    fallback for that whole block: look up from the header, within this
    block's own columns, for a short label that itself reads as a bedroom
    count.

    Only short labels count, and only the row nearest the header wins — a
    long promotional line two rows further up ('One Month Free - One
    Bedroom Apartments Only - Prorated Applicable') mentions a bedroom
    count too, but it is a whole-sheet note, not a heading for this
    specific block, so it is skipped even though a nearer, shorter label
    would have matched.
    """
    c_start, c_end = col_range
    top = max(header_row - scan_rows, 0)
    for r in range(header_row - 1, top, -1):
        for c in range(c_start, c_end + 1):
            text = clean(ws.cell(row=r, column=c).value)
            if not text or len(text) > 30:
                continue
            low = text.lower()
            if any(w in low for w in BANNER_SKIP_WORDS):
                continue
            beds = parse_bedrooms(text)
            if beds is not None:
                return beds, text
    return None, ""


def read_sheet(ws, header_row=None, col_range=None):
    """Everything we know about one sheet, before any decisions are made."""
    c_start, c_end = col_range or (1, min(ws.max_column, 25))
    header_row = header_row or detect_header_row(ws, col_range=(c_start, c_end))
    headers = [clean(ws.cell(row=header_row, column=c).value)
               for c in range(c_start, c_end + 1)]
    rows, links = [], []
    for r in range(header_row + 1, ws.max_row + 1):
        values, targets = [], []
        for c in range(c_start, c_end + 1):
            cell = ws.cell(row=r, column=c)
            values.append(cell.value)
            # A partner often writes "Compound - Google Maps" with the real
            # address hidden behind it, so read the link as well as the text.
            if cell.hyperlink is not None and cell.hyperlink.target:
                targets.append(cell.hyperlink.target)
        if not any(clean(v) for v in values):
            continue
        # a heading merged over two rows repeats itself once the merge is
        # filled; that second copy is not a listing
        same = sum(1 for v, h in zip(values, headers) if clean(v) and clean(v) == h)
        if same >= 2:
            continue
        rows.append(values)
        links.append(targets)
    mapping = infer_from_values(headers, rows, guess_mapping(headers))
    banner_beds, banner_label = guess_banner_bedrooms(ws, header_row, (c_start, c_end))
    context = (guess_context(ws, header_row, col_range=(c_start, c_end))
               or guess_context(ws, header_row, col_range=(1, min(ws.max_column, 25))))
    building, area_hint = split_building_area(context)
    notes = sheet_notes(ws, header_row)
    if not area_hint:
        # 'The Pearl-Qatar • Ready-to-move-in 1BR Apartments' under the title.
        # Only lines above the table: a 'PEARL PROPERTY' heading halfway down
        # applies to the rows under it, not to the whole sheet.
        above = sheet_notes(ws, header_row, above_only=True)
        area_hint = normalize.find_area(*above[:6]) or ""
    return {"header_row": header_row, "headers": headers, "rows": rows,
            "links": links, "mapping": mapping,
            "building": building, "area_hint": area_hint, "notes": notes,
            "notes_bedrooms": notes_bedrooms(notes),
            "context": context,
            "col_range": (c_start, c_end),
            "banner_bedrooms": banner_beds, "banner_bedrooms_label": banner_label}


def sheet_notes(ws, header_row, max_col=25, above_only=False):
    """Every line of text written around the table rather than in it: the
    title, 'Including Utilities Only No WIFI', 'One Month Free - One Bedroom
    Apartments Only', a ticked list of what the rent covers, a footnote saying
    'Non Commissionable'. These apply to every unit on the sheet, and used to
    be thrown away."""
    lines = []
    width = min(ws.max_column, max_col)
    for r in range(1, ws.max_row + 1):
        if r == header_row:
            continue
        texts = [clean(ws.cell(row=r, column=c).value) for c in range(1, width + 1)]
        texts = [t for t in texts if t]
        if not texts:
            continue
        if above_only and r > header_row:
            break
        if r < header_row:
            lines.extend(t for t in texts if not re.fullmatch(r"[\d.,\s]+", t))
        elif (len(texts) == 1 and len(texts[0]) > 6
              and not re.search(r"\d{3,}", re.sub(r"\b20\d\d\b", "", texts[0]))):
            lines.append(texts[0])              # a footnote under the table
    seen, out = set(), []
    for line in lines:
        if line.lower() not in seen:
            seen.add(line.lower())
            out.append(line)
    return out


def notes_bedrooms(notes):
    """'Ready-to-move-in 1BR Apartments' in a title means every unit on the
    sheet is a one-bedroom. Used only when nothing nearer says otherwise, and
    only if the notes name a single bedroom count."""
    found = set()
    for line in notes:
        if re.search(r"\bonly\b|free|promotion", line, re.IGNORECASE):
            continue
        for m in re.finditer(r"\b(studio|\d\s*-?\s*(?:br|bhk|bed(?:room)?s?))\b",
                             line, re.IGNORECASE):
            beds = parse_bedrooms(m.group(1))
            if beds is not None:
                found.add(beds)
    return found.pop() if len(found) == 1 else None


OFFER_WORDS = re.compile(r"free|promotion|promo|offer|discount|commission|"
                         r"valid|till|until|prorated", re.IGNORECASE)


def read_sheet_blocks(ws, header_row=None):
    """Like read_sheet, but first checks whether the sheet actually holds more
    than one table placed side by side (see find_column_blocks) and reads
    each as its own table, with its own header row and column mapping, so a
    second table's data isn't dropped or blended into the first table's
    rows. Returns a list of infos — almost every sheet yields exactly one,
    identical to what read_sheet(ws) would have returned on its own.

    A header row chosen by hand only ever applies to the first table; later
    tables keep using their own auto-detected header, since there is no way
    for a single override to mean two different rows.
    """
    blocks = find_column_blocks(ws)
    if len(blocks) <= 1:
        return [read_sheet(ws, header_row=header_row)]
    infos = [read_sheet(ws, header_row=header_row, col_range=blocks[0])]
    infos += [read_sheet(ws, col_range=b) for b in blocks[1:]]
    return infos


# ------------------------------------------------------------- extraction
# Buildings whose district is well known, for when a partner's file never
# says. Anything learned from listings already in the CRM is checked first
# (see views_imports.known_buildings), so this list only needs the names a
# new partner might send before we have ever stored them.
KNOWN_BUILDINGS = {
    "west walk": "Al Waab",
    "giardino": "The Pearl",
    "regency pearl": "The Pearl",
    "floresta garden": "The Pearl",
}

UNIT_IN_NAME = re.compile(r"^(.*?[A-Za-z].*?)\s+([A-Za-z]?\d{2,4}[A-Za-z]?)$")


def building_area(building, known):
    """District for a building we have seen before, or one that names it."""
    key = clean(building).lower()
    if not key:
        return ""
    if known and key in known:
        return known[key]
    hit = normalize.find_area(building)
    if hit:
        return hit
    for name, area in KNOWN_BUILDINGS.items():
        if name in key:
            return area
    return ""


def extract(sheet, mapping, defaults, fill_down=True, fill_numbers=False):
    """Turn raw rows into listings ready for review, every one in the same
    standard shape whatever the partner's layout:

      * building and flat number separated ('Bilal Tower 603' -> Bilal Tower, 603)
      * district matched to one spelling from areas.py, never left as a zone
        number or 'Ain Khaled (Keys with security)'
      * furnishing, view, balcony and bills boiled down to a fixed list
        (see normalize.py), read from the row, its section heading and the
        notes written around the table
      * one title format for every listing (normalize.standard_title)

    fill_down copies a blank building, location or description from the row
    above — the usual shape when a partner lists several units under one
    heading. fill_numbers does the same for rent, bedrooms and size, which is
    right for hierarchical lists and wrong for flat ones, so it is off by
    default.
    """
    out = []
    carried = {}
    seen_in_file = set()
    headers = sheet.get("headers") or []
    notes = sheet.get("notes") or []
    known = defaults.get("known_buildings") or {}
    inherit = ["building_no", "area", "map_url", "prop_type"]
    if fill_numbers:
        inherit += ["price", "bedrooms", "bathrooms", "size_sqm", "description"]

    def cell(values, field):
        """One field may draw on more than one column. Text fields join what
        they find; everything else takes the first column that has a value."""
        picked = []
        for idx in as_columns(mapping.get(field)):
            if idx <= len(values) and clean(values[idx - 1]):
                picked.append(values[idx - 1])
        if not picked:
            return None
        if (field in JOINABLE or field in MULTI) and len(picked) > 1:
            seen, parts = set(), []
            for v in picked:
                text = clean(v)
                if text.lower() not in seen:
                    seen.add(text.lower())
                    parts.append(text)
            return " · ".join(parts)
        return picked[0]

    def labelled(values, field):
        """'WIFI: Yes', 'Street: 920' — the heading matters when the cell is
        only a yes, a no or a bare number."""
        out_ = []
        for idx in as_columns(mapping.get(field)):
            if idx <= len(values) and clean(values[idx - 1]):
                head = headers[idx - 1] if idx - 1 < len(headers) else ""
                out_.append((clean(head), clean(values[idx - 1])))
        return out_

    # how often each name appears, so 'Bilal Tower 603' / 'Bilal Tower 601'
    # can be told apart from a building genuinely called 'Khalid Building 2'
    has_unit_column = bool(as_columns(mapping.get("unit_no")))
    base_counts = {}
    if not has_unit_column:
        for values in sheet["rows"]:
            m = UNIT_IN_NAME.match(clean(cell(values, "building_no")))
            if m:
                k = m.group(1).strip().lower()
                base_counts[k] = base_counts.get(k, 0) + 1

    # notes around the table that apply to every unit on the sheet
    # (a banner such as 'X | YEARLY CONTRACT OFFER FOR BROKERS' is the title,
    # not a note; it has already given us the building and the area)
    sheet_offer = [n for n in notes if OFFER_WORDS.search(n) and 15 <= len(n) < 200
                   and not re.search(r"[|•]", n)]
    group_text = ""                # a section heading's own description

    all_links = sheet.get("links") or [[] for _ in sheet["rows"]]
    for index, values in enumerate(sheet["rows"]):
        row_links = all_links[index] if index < len(all_links) else []
        raw = {f: cell(values, f) for f, _ in FIELDS}
        own_building = clean(raw.get("building_no"))
        own_title = clean(raw.get("title"))
        own_unit = parse_unit(raw.get("unit_no"))
        own_price = parse_number(raw.get("price"))
        own_size = parse_number(raw.get("size_sqm"))

        if fill_down:
            for field in inherit:
                if clean(raw.get(field)):
                    carried[field] = raw[field]
                elif field in carried:
                    raw[field] = carried[field]

        # A row with no unit, no price and no size is a section heading or a
        # footnote — unless its text names a unit ("Villa No. A-16").
        looks_like_unit = bool(UNIT_LABEL.match(own_title))
        if (not own_unit and own_price is None and own_size is None
                and not looks_like_unit):
            heading = own_title or own_building
            if heading:
                carried["building_no"] = heading
                raw["building_no"] = heading
            row_text = " ".join(clean(v) for v in values if clean(v))
            hint = normalize.find_area(row_text)
            if hint:
                carried["area_hint"] = hint
            group_text = joined(raw.get("description"), raw.get("features"))
            heading_link = find_map_link(*row_links, *values)
            if heading_link:
                carried["map_url"] = heading_link
            continue

        # ---------------------------------------------------- building, flat
        building = clean(raw.get("building_no")) or defaults.get("building_no", "")
        unit = parse_unit(raw.get("unit_no"))
        title_text = clean(raw.get("title"))
        if not unit and not has_unit_column and building:
            m = UNIT_IN_NAME.match(building)
            if m and (len(re.sub(r"\D", "", m.group(2))) >= 3
                      or base_counts.get(m.group(1).strip().lower(), 0) >= 2):
                building, unit = m.group(1).strip(), m.group(2).upper()
        if not unit and title_text:
            unit = parse_unit(title_text)
        building = normalize.tidy_name(building)

        # ------------------------------------------------------- the numbers
        price = parse_number(raw.get("price"))
        beds = parse_bedrooms(raw.get("bedrooms"))
        if beds is None:
            beds = parse_bedrooms(raw.get("prop_type"))
        if beds is None and title_text and not UNIT_LABEL.match(title_text):
            beds = parse_bedrooms(title_text)
        if beds is None:
            beds = defaults.get("bedrooms")          # a 'Studio' heading above
        if beds is None:
            beds = sheet.get("notes_bedrooms")       # '1BR Apartments' in the title
        size = parse_number(raw.get("size_sqm"))

        prop_type = parse_type(raw.get("prop_type"), raw.get("bedrooms"),
                               title_text, building,
                               default=defaults.get("prop_type", "Apartment"))

        found_extras = [clean(raw.get("extras"))]
        for source in (raw.get("bedrooms"), title_text, raw.get("prop_type")):
            more = describe_extras(source)
            if more:
                found_extras.append(more)
        seen, parts = set(), []
        for chunk in found_extras:
            for piece in re.split(r"[,;·]", chunk or ""):
                piece = piece.strip()
                if piece.lower() in ("balcony", "terrace"):
                    continue                     # balcony has its own field now
                if piece and piece.lower() not in seen:
                    seen.add(piece.lower())
                    parts.append(piece)
        extras_text = ", ".join(parts)

        # -------------------------------------- the standard descriptive fields
        features = clean(raw.get("features"))
        view_col = clean(raw.get("view"))
        # 'Caretaker No: Ganesh 6622 3999', 'MAINTINANCE STATUS: RFO' — a bare
        # name or code means nothing later without the heading it sat under
        desc_bits = []
        for head, val in labelled(values, "description"):
            if re.search(r"caretaker|contact|keys|maint", head, re.IGNORECASE):
                desc_bits.append(f"{normalize.tidy_name(head)}: {val}")
            else:
                desc_bits.append(val)
        description = " · ".join(dict.fromkeys(desc_bits))
        unit_raw = clean(raw.get("unit_no"))
        furnishing = normalize.parse_furnishing(
            raw.get("furnishing"), raw.get("prop_type"), raw.get("bedrooms"),
            title_text, features, unit_raw, description, group_text, *notes)
        # amenities ('Car Park, Gym, Pool') are not what the window looks onto,
        # so they are only read for a view when the file has no view column
        view = normalize.parse_view(view_col) if view_col else normalize.parse_view(
            re.sub(r"car\s*park\w*|parking|gym|swimming pool|pool access", " ",
                   features, flags=re.IGNORECASE))
        balcony = normalize.parse_balcony(
            unit_raw, view_col, features, description,
            column_value=raw.get("balcony"))
        bill_bits = [f"{h}: {v}" for h, v in labelled(values, "bills")]
        bills = normalize.parse_bills(*bill_bits, features, description,
                                      group_text, *notes)

        # ------------------------------------------------------ address, area
        address_parts = []
        area, zone = normalize.canonical_area(raw.get("area"))
        for head, val in labelled(values, "address"):
            if re.fullmatch(r"[\d\s/-]+", val):
                label = re.sub(r"\s*(#|no\.?|number|name)\s*$", "", head,
                               flags=re.IGNORECASE).strip() or head
                address_parts.append(f"{label} {val}")
            else:
                address_parts.append(val)
        if zone:
            address_parts.append(zone)
        address = ", ".join(address_parts)

        keys_note = ""
        area_raw = clean(raw.get("area"))
        m = re.search(r"\((.*?)\)", area_raw)
        if m and re.search(r"key|caretaker|security|contact|call", m.group(1), re.I):
            keys_note = m.group(1).strip()

        area_from_street = False
        if not area:
            area = (defaults.get("area")
                    or building_area(building, known)
                    or carried.get("area_hint")
                    or sheet.get("area_hint")
                    or defaults.get("file_area")
                    or "")
        if not area and address:
            # a street name is a weaker clue than anything above — 'Al Nasr
            # Street' runs through more than one district — so say so
            area = normalize.find_area(address) or ""
            area_from_street = bool(area)
        area = normalize.find_area(area) or area

        # --------------------------------------------------------- status
        status_raw = clean(raw.get("status"))
        status = parse_status(status_raw, defaults.get("status", "Available"))
        status_note = ""
        if status_raw and status_raw.lower() not in {
                w for group, _ in STATUS_WORDS for w in group}:
            if parse_status(status_raw, default="") == "":
                status_note = status_raw         # 'Coming soon 1st of SEP'

        # ---------------------------------------------------- description
        offers = []
        for head, val in labelled(values, "offer"):
            low = val.lower()
            if low in normalize.YES:
                offers.append(head)
            elif low not in normalize.NO:
                offers.append(f"{head}: {val}")
        desc_parts = [description]
        if group_text and group_text != description:
            desc_parts.append(group_text)
        if status_note:
            desc_parts.append(status_note)
        if keys_note:
            desc_parts.append(f"Keys: {keys_note}")
        if offers:
            desc_parts.append("Offer: " + ", ".join(offers))
        if sheet_offer:
            desc_parts.append("Partner notes: " + " · ".join(sheet_offer))
        full_description = "\n".join(p for p in desc_parts if p)

        listing = {
            "unit_no": unit,
            "building_no": building,
            "floor_no": parse_floor(raw.get("floor_no"), unit_raw or title_text),
            "prop_type": prop_type,
            "bedrooms": beds,
            "bathrooms": parse_bathrooms(raw.get("bathrooms")) or
                         parse_bathrooms(raw.get("bedrooms")),
            "size_sqm": size,
            "price": price or 0,
            "status": status,
            "area": area,
            "address": address,
            "description": full_description,
            "features": features,
            "furnishing": furnishing,
            "view": view,
            "balcony": balcony,
            "bills": bills,
            "extras": extras_text,
            "map_url": find_map_link(raw.get("map_url"), *row_links, *values),
            "listing_type": defaults.get("listing_type", "Rent"),
        }
        listing["title"] = normalize.standard_title(prop_type, beds, building, unit, area)

        # Flag anything a person should look at before it is saved.
        issues = []
        if not unit and not building:
            issues.append("no building or flat number")
        if not listing["price"]:
            issues.append("no price")
        if listing["bedrooms"] is None and listing["prop_type"] == "Apartment":
            issues.append("no bedroom count")
        if not area:
            issues.append("no area")
        elif area not in areas_module.AREAS:
            issues.append("area not recognised")
        elif area_from_street:
            issues.append("area guessed from the street name — check it")
        if clean(raw.get("map_url")) and not listing["map_url"]:
            issues.append("map link not recognised")
        listing["issues"] = issues

        key = (building.strip().lower(), unit.strip().lower())
        if key in seen_in_file and any(key):
            listing["issues"] = issues + ["appears twice in this file"]
        seen_in_file.add(key)

        out.append(listing)
    return out


def open_workbook(path):
    wb = load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        fill_vertical_merges(ws)
    return wb


def fill_vertical_merges(ws):
    """A partner who merges the rent cell down three rows means all three
    villas share that rent. openpyxl only keeps the value in the top cell, so
    the other two used to arrive with no price at all. Copy it down.

    Only merges that span rows are filled. A title merged across the width of
    the sheet is left alone, otherwise its text would land in every column and
    look like a row of headings."""
    for rng in list(ws.merged_cells.ranges):
        if rng.max_row == rng.min_row:
            continue
        value = ws.cell(row=rng.min_row, column=rng.min_col).value
        link = ws.cell(row=rng.min_row, column=rng.min_col).hyperlink
        ws.unmerge_cells(str(rng))
        for r in range(rng.min_row, rng.max_row + 1):
            cell = ws.cell(row=r, column=rng.min_col)
            cell.value = value
            if link is not None and cell.hyperlink is None:
                cell.hyperlink = link.target
