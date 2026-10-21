"""
Readers for partners whose lists always come in the same layout.

The general importer (importer.py) guesses a sheet's layout from one header
row. Some partners send a layout it can't guess safely — this module holds a
reader for each of those, recognised by the file's own headers, so their
lists import the same way every time.

    fmt = detect(ws)               # None, or the format's name
    listings = read(ws, defaults)  # same shape importer.extract() returns

------------------------------------------------------------------------------
CAP vacant list  ("Current Vacant list as of DD-MM-YYYY.xlsx")
------------------------------------------------------------------------------
Two sheets ("Current vacant list", "BRAND NEW PROPERTIES"), each a stack of
small tables, one per building:

    Available 01 Bedroom Apartments for family or Single      <- banner
    Property | Location | Type | Description | Monthly Rent | ...  <- header
    Flat # 109 (CAP120 DJ) | Doha Jadeed | Flat | (Fully Furnished) 1 Bedroom...
    ...
    (blank row, then the next building's header row)

  * Property: "Flat # 109 (CAP120 DJ)" -> flat 109 in building "CAP120 DJ".
    The code's last part is the district (DJ = Doha Jadeed, ANS = Al Nasr).
  * The header row repeats for every building and its columns shift (some
    blocks have no "Contract Processing Charge", "Kahrama Limit" or
    "PHOTOS"), so every block is read by its own header names.
  * Location links to Google Maps, PHOTOS links to a Drive folder.
  * A yellow row is a unit with a temporary two-day booking -> Reserved.
"""
import re

import areas
import importer
import normalize

clean = importer.clean

# --------------------------------------------------------------- CAP format
CAP = "CAP vacant list"

# the last part of a building code -> its district
CAP_DISTRICTS = {
    "DJ": "Doha Jadeed", "MDR": "Muaither", "MSR": "Al Mansoura", "NJM": "Najma",
    "MKL": "Madinat Khalifa", "ANS": "Al Nasr", "OAP": "Old Airport",
    "LSL": "Lusail", "MTZ": "Al Muntazah", "ASD": "Al Sadd", "MSB": "Msheireb",
}

# header text -> field, matched on lower-case words so "Booking  Validity"
# and "Kahrama Limit" still land in the right place
CAP_COLUMNS = [
    ("property", "property"), ("location", "location"), ("type", "type"),
    ("description", "description"), ("monthly rent", "rent"),
    ("security deposit", "security_deposit"), ("kahramaa deposit", "kahramaa_deposit"),
    ("contract processing", "processing"), ("utilities", "utilities"),
    ("amenities", "amenities"), ("photos", "photos"), ("contact person", "contact"),
    ("kahrama limit", "kahramaa_limit"), ("commission", "commission"),
    ("start date", "start"), ("booking", "booking_validity"),
]

PROPERTY_CELL = re.compile(r"^\s*(flat|office|shop|villa|studio)\s*#?\s*(.*?)\s*\((.+?)\)\s*$",
                           re.IGNORECASE)


def _header_map(cells):
    """{field: column index} for one header row, or None if it isn't one."""
    found = {}
    for idx, value in enumerate(cells):
        text = re.sub(r"\s+", " ", clean(value).lower())
        for key, field in CAP_COLUMNS:
            if text.startswith(key) and field not in found:
                found[field] = idx
                break
    if {"property", "location", "description", "rent"} <= set(found):
        return found
    return None


def _is_cap(ws):
    hits = 0
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 60), max_col=20,
                            values_only=True):
        cols = _header_map(row)
        if cols and "kahramaa_deposit" in cols:
            hits += 1
    return hits >= 1


def _unit(text):
    """'03 D' -> '03D', 'A 12' -> 'A-12', 'S01' -> 'S-01', '05-B' -> '05-B'."""
    t = re.sub(r"\s+", " ", clean(text)).strip(" -").upper()
    m = re.fullmatch(r"([A-Z])\s*-?\s*(\d+)", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(\d+)\s+([A-Z])", t)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return t.replace(" ", "")


def _floor(unit):
    """Flat 109 is on floor 1, 1004 on floor 10; G-01 ground, M-02 mezzanine."""
    if re.fullmatch(r"\d{3,4}", unit):
        return str(int(unit[:-2]))
    if unit.startswith("G-"):
        return "G"
    if unit.startswith("M-"):
        return "M"
    return ""


def _building(code):
    """'CAP18 A NJM' and 'CAP18 A-NJM' are one building: settle on the dash."""
    t = re.sub(r"\s+", " ", clean(code)).upper().strip()
    t = re.sub(r"^(CAP\s*\d+)\s+([A-Z])[\s-]+([A-Z]{2,4})$", r"\1 \2-\3", t)
    t = re.sub(r"^CAP\s*(\d+)", r"CAP\1", t)
    return t.replace("CAP COMPLEX", "CAP Complex")


def _district(code):
    last = re.split(r"[\s-]", code)[-1].upper()
    return CAP_DISTRICTS.get(last)


def _yellow(cells):
    """Whether the row is highlighted yellow (a temporary booking)."""
    for c in cells[:6]:
        f = c.fill
        if f is None or f.fill_type is None:
            continue
        rgb = f.fgColor.rgb if f.fgColor.type == "rgb" else None
        if isinstance(rgb, str) and re.fullmatch(r"(FF)?FFFF(00|33|66|99|CC)", rgb.upper()):
            return True
        if f.fgColor.type == "indexed" and f.fgColor.indexed in (5, 13, 34, 43):
            return True
    return False


def _link(cell):
    return cell.hyperlink.target if cell is not None and cell.hyperlink and cell.hyperlink.target else ""


def _read_cap(ws, defaults):
    known = defaults.get("known_buildings") or {}
    brand_new = "brand new" in ws.title.lower()
    out, cols, seen = [], None, set()
    for cells in ws.iter_rows(min_row=1, max_col=min(ws.max_column, 25)):
        values = [c.value for c in cells]
        header = _header_map(values)
        if header:
            cols = header                     # each block brings its own columns
            continue
        if not cols:
            continue
        m = PROPERTY_CELL.match(clean(values[cols["property"]]))
        if not m:
            continue                          # banner, note, blank row

        def get(field):
            i = cols.get(field)
            return clean(values[i]) if i is not None and i < len(values) else ""

        def cell(field):
            i = cols.get(field)
            return cells[i] if i is not None and i < len(cells) else None

        kind, unit_text, code = m.groups()
        unit = _unit(unit_text)
        building = _building(code)
        desc = re.sub(r"\s+", " ", get("description").strip('" '))
        low = desc.lower()

        prop_type = "Office" if (kind.lower() == "office" or "office" in get("type").lower()) else "Apartment"
        beds = None
        if prop_type == "Apartment":
            beds = 0 if "studio" in low else importer.parse_bedrooms(desc)
        baths = importer.parse_bathrooms(desc)
        furnishing = normalize.parse_furnishing(desc)
        parking = ("One dedicated parking" if re.search(r"one\s+dedicated\s+parking", low)
                   else "No parking" if "without parking" in low else "")

        # district: what the CRM already knows for this building, then the
        # Location column, then the building code's own suffix
        location = get("location")
        area = (known.get(building.lower()) or normalize.find_area(location)
                or _district(building) or "")

        status = "Available"
        notes = []
        if _yellow(cells):
            status = "Reserved"
            notes.append("Temporary booking (2 days) on the partner's list")

        # Amenities: a list of facilities — or, on office rows, the size
        i = cols.get("amenities")
        amenities = str(values[i]) if i is not None and values[i] else ""
        size = None
        sq = re.search(r"(\d+(?:\.\d+)?)\s*(sqm|sq\.?\s*m|m2)", amenities, re.IGNORECASE)
        if sq:
            size = float(sq.group(1))
            amenities = amenities.replace(sq.group(0), "")
        amenities = re.sub(r"brand\s*new\s*!*", "", amenities, flags=re.IGNORECASE)
        amenities = ", ".join(p.strip(" ,()") for p in re.split(r"\s*\n\s*", amenities)
                              if p.strip(" ,()"))
        features = ", ".join(x for x in [
            amenities, parking, "Brand new building" if brand_new else ""] if x)

        detail = [
            ("Security deposit", get("security_deposit")),
            ("Kahramaa deposit", get("kahramaa_deposit")),
            ("Contract processing", get("processing")),
            ("Kahramaa limit", get("kahramaa_limit")),
            ("Commission", get("commission")),
            ("Available from", get("start")),
            ("Booking validity", get("booking_validity")),
            ("Keys / contact", get("contact")),
            ("Photos", _link(cell("photos"))),
        ]
        description = "\n".join(
            [desc] + notes + [f"{k}: {re.sub(r'\.0$', '', v)}" for k, v in detail if v])

        listing = {
            "unit_no": unit,
            "building_no": building,
            "floor_no": _floor(unit),
            "prop_type": prop_type,
            "bedrooms": beds,
            "bathrooms": baths,
            "size_sqm": size,
            "price": importer.parse_number(get("rent")) or 0,
            "status": status,
            "area": area,
            "address": location if not normalize.find_area(location) else "",
            "description": description,
            "features": features,
            "furnishing": furnishing,
            "view": None,
            "balcony": None,
            "bills": normalize.parse_bills(get("utilities")),
            "extras": "",
            "map_url": importer.find_map_link(_link(cell("location"))) or _link(cell("location")),
            "listing_type": "Rent",
        }
        listing["title"] = normalize.standard_title(prop_type, beds, building, unit, area)

        issues = []
        if not listing["price"]:
            issues.append("no price")
        if beds is None and prop_type == "Apartment":
            issues.append("no bedroom count")
        if not area:
            issues.append(f"area not recognised (“{location}”) — choose it")
        key = (building.lower(), unit.lower())
        if key in seen:
            issues.append("appears twice in this file")
        seen.add(key)
        listing["issues"] = issues
        out.append(listing)
    return out


# ------------------------------------------------------------------- public
FORMATS = [(CAP, _is_cap, _read_cap)]


def detect(ws):
    """The name of the known layout this sheet is in, or None."""
    for name, test, _reader in FORMATS:
        try:
            if test(ws):
                return name
        except Exception:                      # never let detection break an import
            continue
    return None


def read(ws, defaults):
    """Listings from a sheet in a known layout, or None if it isn't one."""
    for name, test, reader in FORMATS:
        if test(ws):
            return reader(ws, defaults)
    return None
