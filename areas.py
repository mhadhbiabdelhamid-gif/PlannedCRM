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

# canonical English -> (Arabic, extra spellings seen in the wild)
AREAS = {
    "Al Sadd":            ("السد", ["sadd"]),
    "West Bay":           ("الخليج الغربي", ["westbay", "west bay lagoon"]),
    "Al Dafna":           ("الدفنة", ["dafna"]),
    "The Pearl":          ("اللؤلؤة", ["pearl", "pearl qatar", "the pearl island"]),
    "Porto Arabia":       ("بورتو أرابيا", []),
    "Viva Bahriya":       ("فيفا بحرية", []),
    "Qanat Quartier":     ("قناة كوارتييه", ["qanat"]),
    "Lusail":             ("لوسيل", []),
    "Fox Hills":          ("فوكس هيلز", ["foxhills"]),
    "Al Waab":            ("الوعب", ["waab"]),
    "Fereej Bin Mahmoud": ("فريج بن محمود", ["bin mahmoud", "bin mahmood"]),
    "Fereej Kulaib":      ("فريج كليب", ["kulaib", "kleib", "freej kulaib"]),
    "Fereej Abdul Aziz":  ("فريج عبد العزيز", ["abdul aziz", "abdulaziz"]),
    "Al Mansoura":        ("المنصورة", ["mansoura", "mansura"]),
    "Najma":              ("نجمة", []),
    "Umm Ghuwailina":     ("أم غويلينة", ["umm ghuwailina", "umm gwailina"]),
    "Al Muntazah":        ("المنتزه", ["muntazah"]),
    "Old Airport":        ("المطار القديم", ["old airport area", "matar qadeem"]),
    "Al Hilal":           ("الهلال", ["hilal"]),
    "Al Thumama":         ("الثمامة", ["thumama"]),
    "Ain Khaled":         ("عين خالد", []),
    "Abu Hamour":         ("أبو هامور", ["abu hamor"]),
    "Al Aziziya":         ("العزيزية", ["aziziya", "azizia"]),
    "Al Gharrafa":        ("الغرافة", ["gharrafa", "gharafa"]),
    "Al Rayyan":          ("الريان", ["rayyan", "new al rayyan"]),
    "Al Duhail":          ("الدحيل", ["duhail"]),
    "Madinat Khalifa":    ("مدينة خليفة", ["khalifa city", "madinat khalifa north",
                                            "madinat khalifa south"]),
    "Onaiza":             ("عنيزة", ["unaiza"]),
    "Msheireb":           ("مشيرب", ["mushayrib", "msheireb downtown"]),
    "Al Bidda":           ("البدع", ["bidda"]),
    "Al Mirqab":          ("المرقاب", ["mirqab", "al mirqab al jadeed"]),
    "Al Nasr":            ("النصر", ["nasr"]),
    "Bin Omran":          ("بن عمران", ["fereej bin omran"]),
    "Al Messila":         ("المسيلة", ["messila", "musaila"]),
    "Al Luqta":           ("اللقطة", ["luqta"]),
    "Nuaija":             ("النعيجة", ["nuaija", "nuaija area"]),
    "Mesaimeer":          ("مسيمير", ["msaimeer"]),
    "Rawdat Al Khail":    ("روضة الخيل", ["rawdat alkhail"]),
    "Umm Lekhba":         ("أم لخبا", ["umm lakhba"]),
    "Izghawa":            ("إزغوى", ["izghawa", "izghava"]),
    "Al Markhiya":        ("المرخية", ["markhiya"]),
    "Muaither":           ("معيذر", ["moaither", "muaither south"]),
    "Al Kheesa":          ("الخيسة", ["kheesa"]),
    "Umm Salal":          ("أم صلال", ["umm salal ali", "umm salal mohammed"]),
    "Al Sailiya":         ("السيلية", ["sailiya", "new al sailiya"]),
    "Wadi Al Sail":       ("وادي السيل", []),
    "Bu Sidra":           ("بو سدرة", ["abu sidra"]),
    "Al Ghanim":          ("الغانم", ["old al ghanim", "ghanim"]),
    "Doha Jadeed":        ("الدوحة الجديدة", ["new doha"]),
    "Legtaifiya":         ("لقطيفية", ["lagtaifiya"]),
    "Barwa City":         ("مدينة بروة", ["barwa"]),
    "Al Wakrah":          ("الوكرة", ["wakrah", "wakra"]),
    "Al Wukair":          ("الوكير", ["wukair"]),
    "Al Khor":            ("الخور", ["khor"]),
    "Al Thakhira":        ("الذخيرة", ["thakhira"]),
    "Simaisma":           ("سميسمة", []),
    "Al Sakhama":         ("السخامة", ["sakhama"]),
    "Muraikh":            ("المريخ", ["al muraikh"]),
    "Al Themaid":         ("الثميد", []),
    "Old Al Ghanim":      ("الغانم القديم", []),
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
