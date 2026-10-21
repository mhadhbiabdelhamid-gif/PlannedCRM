"""
Qatar area names in both scripts.

The same district turns up in listings as "Sadd", "Al-Sadd", "AL SADD" and
"السد". This maps all of them onto one canonical pair so posts print the right
form in each language and duplicate detection stops treating them as different
places.

    from areas import lookup
    lookup("al-sadd")   -> {"en": "Al Sadd", "ar": "السد"}
    lookup("فريج كليب") -> {"en": "Fereej Kulaib", "ar": "فريج كليب"}
    lookup("nowhere")   -> None
"""
import re
import unicodedata

# Every district in Qatar, grouped by municipality. Built from the official
# zone list (98 zones in 8 municipalities), plus the development names the
# market actually uses — The Pearl's quarters, Lusail's districts — listed
# under the district they sit in, so they roll up onto its card.
#
# Each entry: (English, Arabic, [other spellings], parent or None)
# Add a missing place here and it appears in every area dropdown at once.
MUNICIPALITIES = [
    ("Doha", "الدوحة", [
        ("West Bay",            "الخليج الغربي", ["westbay", "west bay lagoon"], None),
        ("Al Dafna",            "الدفنة", ["dafna"], "West Bay"),
        ("Diplomatic Area",     "المنطقة الدبلوماسية", ["diplomatic district"], "West Bay"),
        ("The Pearl",           "اللؤلؤة", ["pearl", "pearl qatar", "the pearl island",
                                            "the pearl qatar", "pearl island"], None),
        ("Porto Arabia",        "بورتو أرابيا", ["porto arabia pearl"], "The Pearl"),
        ("Viva Bahriya",        "فيفا بحرية", ["viva bahriyah"], "The Pearl"),
        ("Qanat Quartier",      "قناة كوارتييه", ["qanat"], "The Pearl"),
        ("Medina Centrale",     "مدينة سنترال", ["medina central"], "The Pearl"),
        ("Abraj Quartier",      "أبراج كوارتييه", ["abraj"], "The Pearl"),
        ("La Croisette",        "لا كروازيت", ["croisette"], "The Pearl"),
        ("Giardino Village",    "جاردينو", ["giardino"], "The Pearl"),
        ("Floresta Gardens",    "فلوريستا جاردنز", ["floresta"], "The Pearl"),
        ("Gewan Island",        "جزيرة جيوان", ["gewan"], "The Pearl"),
        ("Legtaifiya",          "لقطيفية", ["lagtaifiya", "leqtaifiya", "katara"], None),
        ("Onaiza",              "عنيزة", ["unaiza"], None),
        ("Al Gassar",           "القصار", ["qassar", "al qassar", "gassar"], None),
        ("Lejbailat",           "لجبيلات", ["lejbailat"], None),
        ("Hazm Al Markhiya",    "حزم المرخية", ["hazm"], None),
        ("Al Markhiya",         "المرخية", ["markhiya"], None),
        ("Al Tarfa",            "الطرفة", ["tarfa"], None),
        ("Jelaiah",             "الجليعة", ["jelaiah"], None),
        ("Jeryan Nejaima",      "جريان نجيمة", ["nejaima"], None),
        ("Al Duhail",           "الدحيل", ["duhail"], None),
        ("Umm Lekhba",          "أم لخبا", ["umm lakhba"], None),
        ("Madinat Khalifa",     "مدينة خليفة", ["khalifa city", "madinath khalifa", "madinat khalifa north",
                                                "madinat khalifa south"], None),
        ("Dahl Al Hamam",       "دحل الحمام", ["dahl al hamam"], None),
        ("Fereej Kulaib",       "فريج كليب", ["kulaib", "kleib", "freej kulaib", "kluaib",
                                              "fereej kluaib", "fereej kulaib"], None),
        ("Al Messila",          "المسيلة", ["messila", "musaila"], None),
        ("Bin Omran",           "بن عمران", ["fereej bin omran", "bin omran"], None),
        ("Hamad Medical City",  "مدينة حمد الطبية", ["hamad hospital"], None),
        ("Al Sadd",             "السد", ["sadd", "alsadd"], None),
        ("Al Nasr",             "النصر", ["nasr", "nasser", "al nasser", "fereej al nasr", "freej al nasr"], None),
        ("Al Mirqab",           "المرقاب", ["mirqab", "al mirqab al jadeed", "new al mirqab"], None),
        ("Al Salata",           "السلطة", ["salata", "as salatah", "old salata"], None),
        ("New Salata",          "السلطة الجديدة", ["new salatah", "salata al jadeeda",
                                                   "salata jadeeda"], None),
        ("Al Hitmi",            "الهتمي", ["old al hitmi", "new al hitmi", "hitmi"], None),
        ("Al Rufaa",            "الرفاع", ["rufaa"], None),
        ("Fereej Bin Mahmoud",  "فريج بن محمود", ["bin mahmoud", "bin mahmood"], None),
        ("Rawdat Al Khail",     "روضة الخيل", ["rawdat alkhail"], None),
        ("Fereej Bin Durham",   "فريج بن درهم", ["bin durham", "bin dirham"], None),
        ("Al Mansoura",         "المنصورة", ["mansoura", "mansura"], None),
        ("Al Muntazah",         "المنتزه", ["muntazah"], None),
        ("Fereej Abdul Aziz",   "فريج عبد العزيز", ["abdul aziz", "abdulaziz",
                                                    "fereej abdel aziz"], None),
        ("Doha Jadeed",         "الدوحة الجديدة", ["new doha", "doha al jadeeda", "doha al jadeed"], None),
        ("Najma",               "نجمة", [], None),
        ("Umm Ghuwailina",      "أم غويلينة", ["umm gwailina"], None),
        ("Al Khulaifat",        "الخليفات", ["khulaifat"], None),
        ("Ras Abu Aboud",       "رأس أبو عبود", ["ras abu abboud"], None),
        ("Old Airport",         "المطار القديم", ["old airport area", "matar qadeem",
                                                  "al matar al qadeem"], None),
        ("Nuaija",              "النعيجة", ["nuaija area"], None),
        ("Al Hilal",            "الهلال", ["hilal"], None),
        ("Al Thumama",          "الثمامة", ["thumama"], None),
        ("Msheireb",            "مشيرب", ["mushayrib", "msheireb downtown", "mushereib",
                                          "msheirib", "musheireb"], None),
        ("Fereej Mohammed Bin Jasim", "فريج محمد بن جاسم", ["mohammed bin jasim"], None),
        ("Al Najada",           "النجادة", ["najada"], None),
        ("Barahat Al Jufairi",  "براحة الجفيري", ["jufairi"], None),
        ("Fereej Al Asmakh",    "فريج الأصمخ", ["asmakh"], None),
        ("Old Al Ghanim",       "الغانم القديم", ["old al ghanim"], None),
        ("Souq Waqif",          "سوق واقف", ["al souq", "souq"], None),
        ("Al Bidda",            "البدع", ["bidda"], None),
        ("Al Jasra",            "الجسرة", ["jasra", "al jasrah"], None),
        ("Wadi Al Sail",        "وادي السيل", [], None),
        ("Rumeilah",            "الرميلة", ["rumailah"], None),
        ("Doha Port",           "ميناء الدوحة", ["old doha port", "mina district"], None),
        ("Industrial Area",     "المنطقة الصناعية", ["industrial area doha", "sanaya"], None),
        ("Ras Abu Fontas",      "رأس أبو فنطاس", ["ras bufontas"], None),
        ("Wholesale Market",    "سوق الجملة", ["wholesale market area"], None),
    ]),
    ("Al Rayyan", "الريان", [
        ("Al Waab",             "الوعب", ["waab", "west walk"], None),
        ("Al Aziziya",          "العزيزية", ["aziziya", "azizia"], None),
        ("Al Ghanim",           "الغانم", ["new al ghanim", "fereej al ghanim", "ghanim"], None),
        ("Fereej Al Murra",     "فريج المرة", ["al murra"], None),
        ("Fereej Al Manaseer",  "فريج المناصير", ["manaseer"], None),
        ("Fereej Al Soudan",    "فريج السودان", ["al soudan", "al sudan"], None),
        ("Bu Sidra",            "بو سدرة", ["abu sidra"], None),
        ("Muaither",            "معيذر", ["moaither", "muaither south", "muaither north"], None),
        ("Al Sailiya",          "السيلية", ["sailiya", "new al sailiya"], None),
        ("Al Mearad",           "المعراض", ["mearad"], None),
        ("Abu Hamour",          "أبو هامور", ["abu hamor", "bu hamour"], None),
        ("Mesaimeer",           "مسيمير", ["msaimeer"], None),
        ("Barwa City",          "مدينة بروة", ["barwa"], None),
        ("Ain Khaled",          "عين خالد", ["ain khalid"], None),
        ("Al Mamoura",          "المعمورة", ["mamoura"], None),
        ("Fereej Al Asiri",     "فريج العسيري", ["asiri"], None),
        ("Umm Al Seneem",       "أم السنيم", ["umm al seneem"], None),
        ("Al Rayyan",           "الريان", ["rayyan", "new al rayyan", "old al rayyan"], None),
        ("Al Luqta",            "اللقطة", ["luqta"], None),
        ("Al Shagub",           "الشقب", ["shagub"], None),
        ("Fereej Al Zaeem",     "فريج الزعيم", ["al zaeem"], None),
        ("Education City",      "المدينة التعليمية", ["education city"], None),
        ("Al Wajbah",           "الوجبة", ["wajbah"], None),
        ("Fereej Al Amir",      "فريج الأمير", ["al amir"], None),
        ("Muraikh",             "المريخ", ["al muraikh"], None),
        ("Baaya",               "بعيع", ["baaya"], None),
        ("Mehairja",            "محيرجة", ["mehairja"], None),
        ("Al Gharrafa",         "الغرافة", ["gharrafa", "gharafa"], None),
        ("Gharrafat Al Rayyan", "غرافة الريان", ["gharrafat al rayyan"], None),
        ("Izghawa",             "إزغوى", ["izghava"], None),
        ("Bani Hajer",          "بني هاجر", ["bani hajar"], None),
        ("Al Seej",             "السيج", ["seej"], None),
        ("Rawdat Egdaim",       "روضة اقديم", ["rawdat egdaim"], None),
        ("Al Themaid",          "الثميد", ["themaid"], None),
        ("Mebaireek",           "مبيريك", ["mebaireek"], None),
        ("Al Karaana",          "الكرعانة", ["karaana"], None),
        ("Abu Samra",           "أبو سمرة", ["abu samra"], None),
    ]),
    ("Al Daayen", "الضعاين", [
        ("Lusail",              "لوسيل", ["lusail city"], None),
        ("Fox Hills",           "فوكس هيلز", ["foxhills"], "Lusail"),
        ("Marina District",     "مارينا لوسيل", ["lusail marina", "marina lusail"], "Lusail"),
        ("Al Erkyah",           "العرقية", ["erkiyah", "al erkiyah", "erkyah"], "Lusail"),
        ("Energy City",         "مدينة الطاقة", ["energy city lusail"], "Lusail"),
        ("Qetaifan Island",     "جزيرة قطيفان", ["qetaifan", "qetaifan island north"], "Lusail"),
        ("Lusail Waterfront",   "الواجهة البحرية لوسيل", ["waterfront lusail"], "Lusail"),
        ("Seef Lusail",         "سيف لوسيل", [], "Lusail"),
        ("Katara Hills",        "كتارا هيلز", [], "Lusail"),
        ("Al Kharayej",         "الخرايج", ["kharayej"], "Lusail"),
        ("Jabal Thuaileb",      "جبل ثعيلب", ["thuaileb"], None),
        ("Wadi Al Banat",       "وادي البنات", [], None),
        ("Al Kheesa",           "الخيسة", ["kheesa"], None),
        ("Rawdat Al Hamama",    "روضة الحمامة", ["rawdat al hamama"], None),
        ("Umm Qarn",            "أم قرن", ["umm garn"], None),
        ("Al Sakhama",          "السخامة", ["sakhama"], None),
        ("Al Ebb",              "العب", ["ebb"], None),
        ("Leabaib",             "لعبيب", ["leabaib"], None),
        ("Jeryan Jenaihat",     "جريان جنيحات", ["jenaihat"], None),
        ("Al Masrouhiya",       "المسروحية", ["masrouhiya"], None),
        ("Wadi Lusail",         "وادي لوسيل", [], None),
    ]),
    ("Umm Salal", "أم صلال", [
        ("Umm Salal",           "أم صلال", ["umm salal ali", "umm salal mohammed"], None),
        ("Al Kharaitiyat",      "الخريطيات", ["kharaitiyat"], None),
        ("Bu Fasseela",         "بو فسيلة", ["bu fasela"], None),
        ("Umm Al Amad",         "أم العمد", ["umm al amad"], None),
        ("Umm Ebairiya",        "أم عبيرية", ["umm ebairiya"], None),
    ]),
    ("Al Wakrah", "الوكرة", [
        ("Al Wakrah",           "الوكرة", ["wakrah", "wakra"], None),
        ("Al Wukair",           "الوكير", ["wukair"], None),
        ("Al Mashaf",           "المشاف", ["mashaf"], None),
        ("Birkat Al Awamer",    "بركة العوامر", ["birkat al awamer"], None),
        ("Mesaieed",            "مسيعيد", ["mesaieed city", "umm said"], None),
        ("Al Kharrara",         "الخرارة", ["kharrara"], None),
        ("Sealine",             "سيلين", ["sealine beach"], None),
        ("Khor Al Adaid",       "خور العديد", ["inland sea"], None),
    ]),
    ("Al Khor", "الخور", [
        ("Al Khor",             "الخور", ["khor", "al khor city"], None),
        ("Simaisma",            "سميسمة", [], None),
        ("Al Thakhira",         "الذخيرة", ["thakhira"], None),
        ("Ras Laffan",          "رأس لفان", ["ras laffan"], None),
        ("Al Ghuwariyah",       "الغويرية", ["ghuwariyah"], None),
        ("Umm Birka",           "أم بركة", ["umm birka"], None),
    ]),
    ("Al Shamal", "الشمال", [
        ("Madinat Al Shamal",   "مدينة الشمال", ["al shamal", "madinat ash shamal"], None),
        ("Al Ruwais",           "الرويس", ["ruwais", "ar ruways"], None),
        ("Fuwayrit",            "فويرط", ["fuwairit"], None),
        ("Ain Sinan",           "عين سنان", [], None),
        ("Abu Dhalouf",         "أبو ظلوف", [], None),
        ("Zubarah",             "الزبارة", ["al zubarah"], None),
        ("Madinat Al Kaaban",   "مدينة الكعبان", ["al kaaban"], None),
    ]),
    ("Al Shahaniya", "الشحانية", [
        ("Al Shahaniya",        "الشحانية", ["shahaniya", "al sheehaniya"], None),
        ("Dukhan",              "دخان", [], None),
        ("Al Utouriya",         "العطورية", ["utouriya"], None),
        ("Al Jemailiya",        "الجميلية", ["jemailiya"], None),
        ("Rawdat Rashed",       "روضة راشد", [], None),
        ("Umm Bab",             "أم باب", [], None),
        ("Al Nasraniya",        "النصرانية", ["nasraniya"], None),
    ]),
]

# canonical English -> (Arabic, extra spellings seen in the wild)
AREAS = {}
# district -> the bigger district it sits inside (Porto Arabia -> The Pearl)
PARENT = {}
# district -> its municipality (English)
MUNICIPALITY = {}
for _muni, _muni_ar, _places in MUNICIPALITIES:
    for _en, _ar, _aliases, _parent in _places:
        AREAS[_en] = (_ar, _aliases)
        MUNICIPALITY[_en] = _muni
        if _parent:
            PARENT[_en] = _parent

# Qatar zone number -> the main district in it, for a listing whose only
# location is a bare zone number. Zones split between several districts map
# to the one most listings mean.
ZONES = {
    1: "Al Jasra", 2: "Al Bidda", 3: "Msheireb", 4: "Msheireb", 5: "Al Najada",
    6: "Old Al Ghanim", 7: "Souq Waqif", 10: "Wadi Al Sail", 11: "Rumeilah",
    12: "Al Bidda", 13: "Msheireb", 14: "Fereej Abdul Aziz", 15: "Doha Jadeed",
    16: "Old Al Ghanim", 17: "Al Hitmi", 18: "Al Salata", 19: "Doha Port",
    20: "Wadi Al Sail", 21: "Rumeilah", 22: "Fereej Bin Mahmoud",
    23: "Fereej Bin Mahmoud", 24: "Rawdat Al Khail", 25: "Al Mansoura",
    26: "Najma", 27: "Umm Ghuwailina", 28: "Al Khulaifat", 30: "Al Duhail",
    31: "Umm Lekhba", 32: "Madinat Khalifa", 33: "Al Markhiya",
    34: "Madinat Khalifa", 35: "Fereej Kulaib", 36: "Al Messila", 37: "Bin Omran",
    38: "Al Sadd", 39: "Al Sadd", 40: "New Salata", 41: "Nuaija", 42: "Al Hilal",
    43: "Nuaija", 44: "Nuaija", 45: "Old Airport", 46: "Al Thumama",
    47: "Al Thumama", 49: "Ras Abu Fontas", 50: "Al Thumama",
    51: "Al Gharrafa", 52: "Al Luqta", 53: "Al Rayyan", 54: "Muraikh",
    55: "Al Waab", 56: "Abu Hamour", 57: "Industrial Area", 58: "Wholesale Market",
    61: "Al Dafna", 63: "Onaiza", 64: "Lejbailat", 65: "Onaiza", 66: "The Pearl",
    67: "Hazm Al Markhiya", 68: "Jelaiah", 69: "Lusail", 70: "Lusail",
    71: "Umm Salal", 72: "Al Utouriya", 73: "Al Jemailiya", 74: "Al Khor",
    75: "Al Thakhira", 76: "Al Ghuwariyah", 77: "Fuwayrit", 78: "Abu Dhalouf",
    79: "Madinat Al Shamal", 80: "Al Shahaniya", 81: "Mebaireek",
    82: "Rawdat Rashed", 83: "Al Karaana", 84: "Umm Bab", 85: "Al Nasraniya",
    86: "Dukhan", 90: "Al Wakrah", 91: "Al Wukair", 92: "Mesaieed",
    93: "Mesaieed", 95: "Al Kharrara", 96: "Abu Samra", 98: "Khor Al Adaid",
}


# Arabic definite article and the filler words that vary between spellings.
_EN_NOISE = re.compile(r"^(al|el)[\s\-]+|^(the)\s+|\s+(area|district)$")
_AR_NOISE = re.compile(r"^(ال)")


def _key(text):
    """Reduce a name to something comparable across spellings."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).strip().lower()
    s = s.replace("ـ", "")                       # Arabic tatweel
    s = re.sub(r"[أإآ]", "ا", s)                 # alef variants
    s = re.sub(r"[ةه]$", "ه", s)                 # final ta marbuta
    s = re.sub(r"[ىي]", "ي", s)
    s = re.sub(r"[^\w\u0600-\u06FF]+", " ", s)   # punctuation to space
    s = re.sub(r"\s+", " ", s).strip()
    s = _EN_NOISE.sub("", s)
    s = _AR_NOISE.sub("", s)
    return s.strip()


# built once, so lookups are a dict hit rather than a scan
_INDEX = {}
for _en, (_ar, _aliases) in AREAS.items():
    _pair = {"en": _en, "ar": _ar}
    for _name in [_en, _ar] + list(_aliases):
        _INDEX.setdefault(_key(_name), _pair)


def lookup(text):
    """Canonical pair for an area name, or None if it isn't recognised."""
    return _INDEX.get(_key(text))


def in_arabic(text):
    hit = lookup(text)
    return hit["ar"] if hit else (text or "")


def in_english(text):
    hit = lookup(text)
    return hit["en"] if hit else (text or "")


def canonical(text):
    """English form for storage, so the database settles on one spelling."""
    hit = lookup(text)
    return hit["en"] if hit else (str(text).strip() if text else None)


def all_names():
    return sorted(AREAS)


def variants(text):
    """Every spelling on record for an area — English name, Arabic name, and
    every alias — so a search for one spelling can be widened to match a
    listing saved under any of the others. Empty list if the name isn't
    recognised, so callers can fall back to a plain match on the raw text.
    """
    hit = lookup(text)
    if not hit:
        return []
    places = [hit["en"]] + [c for c, parent in PARENT.items() if parent == hit["en"]]
    seen = []
    for place in places:
        ar, aliases = AREAS[place]
        for name in [place, ar] + list(aliases):
            if name not in seen:
                seen.append(name)
    return seen


def options():
    """The area list for a dropdown: one group per municipality, each place
    with its Arabic name and the district it sits in, children right after
    their parent so The Pearl's quarters read together."""
    out = []
    for muni, muni_ar, places in MUNICIPALITIES:
        tops = [p for p in places if not p[3]]
        rows = []
        for en, ar, _aliases, _parent in sorted(tops, key=lambda p: p[0].lower()):
            rows.append({"en": en, "ar": ar, "parent": None})
            for c_en, c_ar, _a, c_parent in sorted(places, key=lambda p: p[0].lower()):
                if c_parent == en:
                    rows.append({"en": c_en, "ar": c_ar, "parent": en})
        out.append({"en": muni, "ar": muni_ar, "places": rows})
    return out


def is_listed(name):
    """Whether a value is exactly one of the names in the master list."""
    return name in AREAS


def from_zone(text):
    """'Zone 38', '38' -> 'Al Sadd'; None for anything else."""
    m = re.fullmatch(r"\s*(?:zone\s*)?(\d{1,2})\s*", str(text or ""), re.IGNORECASE)
    return ZONES.get(int(m.group(1))) if m else None
