"""
Qatar area names in both scripts, plus the official 2015-census zone
numbers each district falls under (per the Qatar Zones spreadsheet — see
DISTRICT_ZONES / ZONE_DISTRICTS below).

The same district turns up in listings as "Sadd", "Al-Sadd", "AL SADD" and
"السد". This maps all of them onto one canonical pair so posts print the right
form in each language and duplicate detection stops treating them as different
places. It also means a search box can take a zone number ("2"), an Arabic
name, or a near-miss spelling ("musheireb") and still land on the right place.

    from areas import lookup
    lookup("al-sadd")   -> {"en": "Al Sadd", "ar": "السد"}
    lookup("فريج كليب") -> {"en": "Fereej Kulaib", "ar": "فريج كليب"}
    lookup("nowhere")   -> None

    variants("2")          -> every spelling of every district in Zone 2
    variants("musheireb")  -> every spelling of Msheireb (fuzzy match)
"""
import difflib
import re
import unicodedata

# canonical English -> (Arabic, extra spellings seen in the wild)
#
# The first ~60 entries were curated and checked by hand. Everything from
# "--- Zone-sourced districts below ---" on was pulled from the Qatar Zones
# spreadsheet (2015 census / Wikipedia / seekqatar.com); the English names and
# zone numbers there are authoritative, but the Arabic is a best-effort
# transliteration following the same conventions as the curated set, NOT
# independently verified — worth a native-speaker spot-check before treating
# it as gospel, especially the more obscure industrial/desert zones.
AREAS = {
    "Al Sadd":             ("السد", ["sadd"]),
    "West Bay":            ("الخليج الغربي", ["westbay", "west bay lagoon"]),
    "Al Dafna":            ("الدفنة", ["dafna"]),
    "The Pearl":           ("اللؤلؤة", ["pearl", "pearl qatar", "the pearl island"]),
    "Porto Arabia":        ("بورتو أرابيا", []),
    "Viva Bahriya":        ("فيفا بحرية", []),
    "Qanat Quartier":      ("قناة كوارتييه", ["qanat"]),
    "Lusail":              ("لوسيل", []),
    "Fox Hills":           ("فوكس هيلز", ["foxhills"]),
    "Al Waab":             ("الوعب", ["waab"]),
    "Fereej Bin Mahmoud":  ("فريج بن محمود", ["bin mahmoud", "bin mahmood"]),
    "Fereej Kulaib":       ("فريج كليب", ["kulaib", "kleib", "freej kulaib"]),
    "Fereej Abdul Aziz":   ("فريج عبد العزيز", ["abdul aziz", "abdulaziz", "Fereej Abdel Aziz"]),
    "Al Mansoura":         ("المنصورة", ["mansoura", "mansura"]),
    "Najma":               ("نجمة", []),
    "Umm Ghuwailina":      ("أم غويلينة", ["umm ghuwailina", "umm gwailina"]),
    "Al Muntazah":         ("المنتزه", ["muntazah"]),
    "Old Airport":         ("المطار القديم", ["old airport area", "matar qadeem"]),
    "Al Hilal":            ("الهلال", ["hilal"]),
    "Al Thumama":          ("الثمامة", ["thumama"]),
    "Ain Khaled":          ("عين خالد", []),
    "Abu Hamour":          ("أبو هامور", ["abu hamor"]),
    "Al Aziziya":          ("العزيزية", ["aziziya", "azizia"]),
    "Al Gharrafa":         ("الغرافة", ["gharrafa", "gharafa"]),
    "Al Rayyan":           ("الريان", ["rayyan", "new al rayyan"]),
    "Al Duhail":           ("الدحيل", ["duhail"]),
    "Madinat Khalifa":     ("مدينة خليفة", ["khalifa city", "madinat khalifa north", "madinat khalifa south"]),
    "Onaiza":              ("عنيزة", ["unaiza"]),
    "Msheireb":            ("مشيرب", ["mushayrib", "msheireb downtown"]),
    "Al Bidda":            ("البدع", ["bidda"]),
    "Al Mirqab":           ("المرقاب", ["mirqab", "al mirqab al jadeed"]),
    "Al Nasr":             ("النصر", ["nasr", "Fereej Al Nasr"]),
    "Bin Omran":           ("بن عمران", ["fereej bin omran"]),
    "Al Messila":          ("المسيلة", ["messila", "musaila"]),
    "Al Luqta":            ("اللقطة", ["luqta"]),
    "Nuaija":              ("النعيجة", ["nuaija", "nuaija area"]),
    "Mesaimeer":           ("مسيمير", ["msaimeer"]),
    "Rawdat Al Khail":     ("روضة الخيل", ["rawdat alkhail"]),
    "Umm Lekhba":          ("أم لخبا", ["umm lakhba"]),
    "Izghawa":             ("إزغوى", ["izghawa", "izghava"]),
    "Al Markhiya":         ("المرخية", ["markhiya"]),
    "Muaither":            ("معيذر", ["moaither", "muaither south"]),
    "Al Kheesa":           ("الخيسة", ["kheesa"]),
    "Umm Salal":           ("أم صلال", ["umm salal ali", "umm salal mohammed"]),
    "Al Sailiya":          ("السيلية", ["sailiya", "new al sailiya"]),
    "Wadi Al Sail":        ("وادي السيل", []),
    "Bu Sidra":            ("بو سدرة", ["abu sidra"]),
    "Al Ghanim":           ("الغانم", ["old al ghanim", "ghanim"]),
    "Doha Jadeed":         ("الدوحة الجديدة", ["new doha", "Ad Dawhah al Jadidah"]),
    "Legtaifiya":          ("لقطيفية", ["lagtaifiya"]),
    "Barwa City":          ("مدينة بروة", ["barwa"]),
    "Al Wakrah":           ("الوكرة", ["wakrah", "wakra"]),
    "Al Wukair":           ("الوكير", ["wukair"]),
    "Al Khor":             ("الخور", ["khor"]),
    "Al Thakhira":         ("الذخيرة", ["thakhira"]),
    "Simaisma":            ("سميسمة", []),
    "Al Sakhama":          ("السخامة", ["sakhama"]),
    "Muraikh":             ("المريخ", ["al muraikh"]),
    "Al Themaid":          ("الثميد", []),
    "Old Al Ghanim":       ("الغانم القديم", []),

    # --- Zone-sourced districts below (best-effort Arabic, see note above) ---
    "Abu Dhalouf":         ("أبو ظلوف", []),
    "Abu Samra":           ("أبو سمرة", []),
    "Ain Sinan":           ("عين سنان", []),
    "Al Daayen":           ("الذعاين", []),
    "Al Ebb":              ("العب", []),
    "Al Egla":             ("العقلة", []),
    "Al Ghuwariyah":       ("الغويرية", []),
    "Al Jasrah":           ("الجسرة", []),
    "Al Jemailiya":        ("الجميلية", []),
    "Al Jeryan":           ("الجريان", []),
    "Al Karaana":          ("الكرعانة", []),
    "Al Kharaitiyat":      ("الخريطيات", []),
    "Al Kharayej":         ("الخرائج", []),
    "Al Kharrara":         ("الخرارة", []),
    "Al Khor City":        ("مدينة الخور", []),
    "Al Khulaifat":        ("الخليفات", []),
    "Al Mamoura":          ("المعمورة", []),
    "Al Mashaf":           ("المشاف", []),
    "Al Masrouhiya":       ("المسروحية", []),
    "Al Mearad":           ("المعراض", []),
    "Al Najada":           ("النجادة", []),
    "Al Nasraniya":        ("النصرانية", []),
    "Al Qassar":           ("القصار", []),
    "Al Rufaa":            ("الرفاع", []),
    "Al Seej":             ("السيج", []),
    "Al Shagub":           ("الشقب", []),
    "Al Shahaniya City":   ("مدينة الشحانية", []),
    "Al Souq":             ("السوق", []),
    "Al Tarfa":            ("الطرفة", []),
    "Al Utouriya":         ("العطورية", []),
    "Al Wajbah":           ("الوجبة", []),
    "Ar Ru'ays":           ("الرويس", []),
    "As Salatah":          ("السلطة", []),
    "Baaya":               ("بعيا", []),
    "Bani Hajer":          ("بني هاجر", []),
    "Barahat Al Jufairi":  ("براحة الجفيري", []),
    "Bu Fasseela":         ("بو فصيلة", []),
    "Bu Samra":            ("بو سمرة", []),
    "Dahl Al Hamam":       ("دحل الحمام", []),
    "Doha International Airport": ("مطار الدوحة الدولي", []),
    "Doha Port":           ("ميناء الدوحة", []),
    "Dukhan":              ("دخان", []),
    "Fereej Al Amir":      ("فريج الأمير", []),
    "Fereej Al Asiri":     ("فريج العسيري", []),
    "Fereej Al Asmakh":    ("فريج الأسمخ", []),
    "Fereej Al Manaseer":  ("فريج المناصير", []),
    "Fereej Al Murra":     ("فريج المرة", []),
    "Fereej Al Soudan":    ("فريج السودان", []),
    "Fereej Al Zaeem":     ("فريج الزعيم", []),
    "Fereej Bin Dirham":   ("فريج بن درهم", []),
    "Fereej Mohammed Bin Jasim": ("فريج محمد بن جاسم", []),
    "Fuwairit":            ("فويرط", []),
    "Gharrafat Al Rayyan": ("غرافة الريان", []),
    "Hamad Medical City":  ("مدينة حمد الطبية", []),
    "Hazm Al Markhiya":    ("حزم المرخية", []),
    "Industrial Area":     ("المنطقة الصناعية", []),
    "Jabal Thuaileb":      ("جبل ثعيلب", []),
    "Jelaiah":             ("جليعة", []),
    "Jeryan Jenaihat":     ("جريان جناحات", []),
    "Jeryan Nejaima":      ("جريان نجيمة", []),
    "Khor Al Adaid":       ("خور العديد", []),
    "Leabaib":             ("لعبيب", []),
    "Lebday":              ("لبدي", []),
    "Lejbailat":           ("لجبيلات", []),
    "Luaib":               ("لعيب", []),
    "Madinat Al Kaaban":   ("مدينة الكعبان", []),
    "Madinat ash Shamal":  ("مدينة الشمال", []),
    "Mebaireek":           ("مبيرك", []),
    "Mehairja":            ("مهيرجة", []),
    "Mesaieed":            ("مسيعيد", []),
    "Mesaieed Industrial Area": ("منطقة مسيعيد الصناعية", []),
    "New Al Hitmi":        ("الهتمي الجديد", []),
    "New Al Mirqab":       ("المرقاب الجديد", []),
    "New Fereej Al Ghanim": ("فريج الغانم الجديد", []),
    "New Fereej Al Khulaifat": ("فريج الخليفات الجديد", []),
    "New Salatah":         ("السلطة الجديدة", []),
    "Old Al Hitmi":        ("الهتمي القديم", []),
    "Old Al Rayyan":       ("الريان القديم", []),
    "Ras Abu Aboud":       ("رأس أبو عبود", []),
    "Ras Laffan":          ("رأس لفان", []),
    "Rawdat Al Hamama":    ("روضة الحمامة", []),
    "Rawdat Egdaim":       ("روضة اقدعيم", []),
    "Rawdat Rashed":       ("روضة راشد", []),
    "Rumeilah":            ("الرميلة", []),
    "Saina Al-Humaidi":    ("سينا الحميدي", []),
    "Sawda Natheel":       ("سودة نذيل", []),
    "Shagra":              ("شقرا", []),
    "Umm Al Amad":         ("أم العمد", []),
    "Umm Al Seneem":       ("أم السنيم", []),
    "Umm Bab":             ("أم باب", []),
    "Umm Birka":           ("أم بركة", []),
    "Umm Ebairiya":        ("أم عبيرية", []),
    "Umm Qarn":            ("أم قرن", []),
    "Wadi Al Banat":       ("وادي البنات", []),
    "Wadi Al Wasaah":      ("وادي الوسيع", []),
    "Wadi Lusail":         ("وادي لوسيل", []),
    "Zubarah":             ("الزبارة", []),
}

# canonical English district -> sorted list of zone numbers it falls under.
# A district can span more than one zone ("Al Bidda" is Zones 2 and 12).
DISTRICT_ZONES = {
    "Abu Dhalouf": [78],
    "Abu Hamour": [56],
    "Abu Samra": [96],
    "Ain Khaled": [56],
    "Ain Sinan": [77],
    "Al Aziziya": [55],
    "Al Bidda": [2, 12],
    "Al Daayen": [70],
    "Al Dafna": [61],
    "Al Duhail": [30],
    "Al Ebb": [70],
    "Al Egla": [69],
    "Al Ghanim": [6, 16],
    "Al Gharrafa": [51],
    "Al Ghuwariyah": [76],
    "Al Hilal": [42],
    "Al Jasrah": [1],
    "Al Jemailiya": [73],
    "Al Jeryan": [74],
    "Al Karaana": [83],
    "Al Kharaitiyat": [71],
    "Al Kharayej": [69],
    "Al Kharrara": [95],
    "Al Kheesa": [70],
    "Al Khor City": [74],
    "Al Khulaifat": [28],
    "Al Luqta": [52],
    "Al Mamoura": [56],
    "Al Mansoura": [25],
    "Al Markhiya": [33],
    "Al Mashaf": [91],
    "Al Masrouhiya": [70],
    "Al Mearad": [55],
    "Al Messila": [36],
    "Al Mirqab": [18],
    "Al Najada": [5],
    "Al Nasr": [39],
    "Al Nasraniya": [85],
    "Al Qassar": [61, 66],
    "Al Rayyan": [53],
    "Al Rufaa": [17],
    "Al Sadd": [38, 39],
    "Al Sailiya": [55],
    "Al Sakhama": [70],
    "Al Seej": [51],
    "Al Shagub": [52],
    "Al Shahaniya City": [80],
    "Al Souq": [7],
    "Al Tarfa": [68],
    "Al Thakhira": [75],
    "Al Themaid": [51],
    "Al Thumama": [46, 47, 91],
    "Al Utouriya": [72],
    "Al Waab": [55],
    "Al Wajbah": [53],
    "Al Wakrah": [90],
    "Al Wukair": [91],
    "Ar Ru'ays": [79],
    "As Salatah": [18],
    "Baaya": [54],
    "Bani Hajer": [51],
    "Barahat Al Jufairi": [5],
    "Bin Omran": [37],
    "Bu Fasseela": [71],
    "Bu Samra": [56],
    "Bu Sidra": [55],
    "Dahl Al Hamam": [32],
    "Doha International Airport": [48, 49],
    "Doha Jadeed": [15],
    "Doha Port": [19],
    "Dukhan": [86],
    "Fereej Abdul Aziz": [14],
    "Fereej Al Amir": [54],
    "Fereej Al Asiri": [56],
    "Fereej Al Asmakh": [5],
    "Fereej Al Manaseer": [55],
    "Fereej Al Murra": [55],
    "Fereej Al Soudan": [54, 55],
    "Fereej Al Zaeem": [52],
    "Fereej Bin Dirham": [25],
    "Fereej Bin Mahmoud": [22, 23],
    "Fereej Kulaib": [35],
    "Fereej Mohammed Bin Jasim": [3],
    "Fuwairit": [77],
    "Gharrafat Al Rayyan": [51],
    "Hamad Medical City": [37],
    "Hazm Al Markhiya": [67],
    "Industrial Area": [57],
    "Izghawa": [51, 71],
    "Jabal Thuaileb": [69],
    "Jelaiah": [68],
    "Jeryan Jenaihat": [70],
    "Jeryan Nejaima": [68],
    "Khor Al Adaid": [98],
    "Leabaib": [70],
    "Lebday": [52],
    "Legtaifiya": [66],
    "Lejbailat": [64],
    "Luaib": [54],
    "Lusail": [69, 70],
    "Madinat Al Kaaban": [77],
    "Madinat ash Shamal": [79],
    "Madinat Khalifa": [32, 34],
    "Mebaireek": [81],
    "Mehairja": [54],
    "Mesaieed": [92],
    "Mesaieed Industrial Area": [93],
    "Mesaimeer": [56],
    "Msheireb": [3, 4, 13],
    "Muaither": [53, 55],
    "Muraikh": [54],
    "Najma": [26],
    "New Al Hitmi": [37],
    "New Al Mirqab": [39],
    "New Fereej Al Ghanim": [55],
    "New Fereej Al Khulaifat": [56],
    "New Salatah": [40],
    "Nuaija": [41, 43, 44],
    "Old Airport": [45],
    "Old Al Hitmi": [17],
    "Old Al Rayyan": [52],
    "Onaiza": [63, 65, 66],
    "Ras Abu Aboud": [28],
    "Ras Laffan": [75],
    "Rawdat Al Hamama": [70],
    "Rawdat Al Khail": [24],
    "Rawdat Egdaim": [51],
    "Rawdat Rashed": [82],
    "Rumeilah": [11, 21],
    "Saina Al-Humaidi": [71],
    "Sawda Natheel": [97],
    "Shagra": [94],
    "Simaisma": [74],
    "Umm Al Amad": [71],
    "Umm Al Seneem": [56],
    "Umm Bab": [84],
    "Umm Birka": [75],
    "Umm Ebairiya": [71],
    "Umm Ghuwailina": [27],
    "Umm Lekhba": [31],
    "Umm Qarn": [70],
    "Umm Salal": [71],
    "Wadi Al Banat": [69],
    "Wadi Al Sail": [10, 20],
    "Wadi Al Wasaah": [70],
    "Wadi Lusail": [70],
    "Zubarah": [78],
}

# zone number -> sorted list of canonical English district names in it. A
# zone can hold several districts ("Zone 51" is seven of them).
ZONE_DISTRICTS = {
    1: ["Al Jasrah"],
    2: ["Al Bidda"],
    3: ["Fereej Mohammed Bin Jasim", "Msheireb"],
    4: ["Msheireb"],
    5: ["Al Najada", "Barahat Al Jufairi", "Fereej Al Asmakh"],
    6: ["Al Ghanim"],
    7: ["Al Souq"],
    10: ["Wadi Al Sail"],
    11: ["Rumeilah"],
    12: ["Al Bidda"],
    13: ["Msheireb"],
    14: ["Fereej Abdul Aziz"],
    15: ["Doha Jadeed"],
    16: ["Al Ghanim"],
    17: ["Al Rufaa", "Old Al Hitmi"],
    18: ["Al Mirqab", "As Salatah"],
    19: ["Doha Port"],
    20: ["Wadi Al Sail"],
    21: ["Rumeilah"],
    22: ["Fereej Bin Mahmoud"],
    23: ["Fereej Bin Mahmoud"],
    24: ["Rawdat Al Khail"],
    25: ["Al Mansoura", "Fereej Bin Dirham"],
    26: ["Najma"],
    27: ["Umm Ghuwailina"],
    28: ["Al Khulaifat", "Ras Abu Aboud"],
    30: ["Al Duhail"],
    31: ["Umm Lekhba"],
    32: ["Dahl Al Hamam", "Madinat Khalifa"],
    33: ["Al Markhiya"],
    34: ["Madinat Khalifa"],
    35: ["Fereej Kulaib"],
    36: ["Al Messila"],
    37: ["Bin Omran", "Hamad Medical City", "New Al Hitmi"],
    38: ["Al Sadd"],
    39: ["Al Nasr", "Al Sadd", "New Al Mirqab"],
    40: ["New Salatah"],
    41: ["Nuaija"],
    42: ["Al Hilal"],
    43: ["Nuaija"],
    44: ["Nuaija"],
    45: ["Old Airport"],
    46: ["Al Thumama"],
    47: ["Al Thumama"],
    48: ["Doha International Airport"],
    49: ["Doha International Airport"],
    51: ["Al Gharrafa", "Al Seej", "Al Themaid", "Bani Hajer", "Gharrafat Al Rayyan", "Izghawa", "Rawdat Egdaim"],
    52: ["Al Luqta", "Al Shagub", "Fereej Al Zaeem", "Lebday", "Old Al Rayyan"],
    53: ["Al Rayyan", "Al Wajbah", "Muaither"],
    54: ["Baaya", "Fereej Al Amir", "Fereej Al Soudan", "Luaib", "Mehairja", "Muraikh"],
    55: ["Al Aziziya", "Al Mearad", "Al Sailiya", "Al Waab", "Bu Sidra", "Fereej Al Manaseer", "Fereej Al Murra", "Fereej Al Soudan", "Muaither", "New Fereej Al Ghanim"],
    56: ["Abu Hamour", "Ain Khaled", "Al Mamoura", "Bu Samra", "Fereej Al Asiri", "Mesaimeer", "New Fereej Al Khulaifat", "Umm Al Seneem"],
    57: ["Industrial Area"],
    61: ["Al Dafna", "Al Qassar"],
    63: ["Onaiza"],
    64: ["Lejbailat"],
    65: ["Onaiza"],
    66: ["Al Qassar", "Legtaifiya", "Onaiza"],
    67: ["Hazm Al Markhiya"],
    68: ["Al Tarfa", "Jelaiah", "Jeryan Nejaima"],
    69: ["Al Egla", "Al Kharayej", "Jabal Thuaileb", "Lusail", "Wadi Al Banat"],
    70: ["Al Daayen", "Al Ebb", "Al Kheesa", "Al Masrouhiya", "Al Sakhama", "Jeryan Jenaihat", "Leabaib", "Lusail", "Rawdat Al Hamama", "Umm Qarn", "Wadi Al Wasaah", "Wadi Lusail"],
    71: ["Al Kharaitiyat", "Bu Fasseela", "Izghawa", "Saina Al-Humaidi", "Umm Al Amad", "Umm Ebairiya", "Umm Salal"],
    72: ["Al Utouriya"],
    73: ["Al Jemailiya"],
    74: ["Al Jeryan", "Al Khor City", "Simaisma"],
    75: ["Al Thakhira", "Ras Laffan", "Umm Birka"],
    76: ["Al Ghuwariyah"],
    77: ["Ain Sinan", "Fuwairit", "Madinat Al Kaaban"],
    78: ["Abu Dhalouf", "Zubarah"],
    79: ["Ar Ru'ays", "Madinat ash Shamal"],
    80: ["Al Shahaniya City"],
    81: ["Mebaireek"],
    82: ["Rawdat Rashed"],
    83: ["Al Karaana"],
    84: ["Umm Bab"],
    85: ["Al Nasraniya"],
    86: ["Dukhan"],
    90: ["Al Wakrah"],
    91: ["Al Mashaf", "Al Thumama", "Al Wukair"],
    92: ["Mesaieed"],
    93: ["Mesaieed Industrial Area"],
    94: ["Shagra"],
    95: ["Al Kharrara"],
    96: ["Abu Samra"],
    97: ["Sawda Natheel"],
    98: ["Khor Al Adaid"],
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
    s = re.sub(r"[^\w؀-ۿ]+", " ", s)   # punctuation to space
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

_ALL_KEYS = list(_INDEX.keys())

_ZONE_PHRASE = re.compile(r"^(?:zone|z|منطقة)\s*#?\s*(\d{1,3})$")


def _zone_number(text):
    """A zone number out of "2", "zone 2", "Zone #2", "z2", or "منطقة 2" —
    or None if the text isn't naming a zone at all.
    """
    if not text:
        return None
    s = unicodedata.normalize("NFKC", str(text)).strip().lower()
    if s.isdigit():
        return int(s)
    m = _ZONE_PHRASE.match(s)
    return int(m.group(1)) if m else None

# Minimum difflib ratio before a fuzzy hit counts as a match. Tuned so a
# typo/near-miss spelling ("musheireb" for "Msheireb") still lands, without
# two unrelated short place names accidentally matching each other.
_FUZZY_CUTOFF = 0.72


def _fuzzy_key(key):
    """Best matching known key for a normalized query that isn't an exact
    hit — first by plain substring containment (handles a truncated or
    padded spelling), then by edit-distance similarity (handles a genuine
    typo). Returns None if nothing is close enough to trust.
    """
    if not key or len(key) < 5:
        # Below 5 chars a substring/edit-distance match is too likely to be
        # a false positive (e.g. "Doha" — the whole city — shouldn't get
        # narrowed down to the one small "Doha Jadeed" district just
        # because its alias contains "doha").
        return None
    contains = [k for k in _ALL_KEYS if len(k) >= 3 and (key in k or k in key)]
    if contains:
        # prefer the closest length match, so "sadd" doesn't prefer a much
        # longer unrelated key that merely happens to contain it
        return min(contains, key=lambda k: abs(len(k) - len(key)))
    close = difflib.get_close_matches(key, _ALL_KEYS, n=1, cutoff=_FUZZY_CUTOFF)
    return close[0] if close else None


def lookup(text):
    """Canonical pair for an area name — an exact spelling, a zone number
    ("2"), or a close-enough near-miss spelling — or None if nothing is
    recognisable.
    """
    key = _key(text)
    if key in _INDEX:
        return _INDEX[key]
    zone = _zone_number(text)
    if zone is not None:
        districts = ZONE_DISTRICTS.get(zone)
        return _INDEX.get(_key(districts[0])) if districts else None
    fkey = _fuzzy_key(key)
    return _INDEX.get(fkey) if fkey else None


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


def zones_for(text):
    """Zone numbers a place name falls under, or [] if it isn't recognised
    or has no zone data (a custom/free-text location isn't a real district).
    """
    hit = lookup(text)
    return DISTRICT_ZONES.get(hit["en"], []) if hit else []


def _variants_for_en(en):
    ar, aliases = AREAS[en]
    seen = []
    for name in [en, ar] + list(aliases):
        if name not in seen:
            seen.append(name)
    return seen


def variants(text):
    """Every spelling on record for an area — English name, Arabic name,
    every alias, and (for a zone number) every district in that zone — so a
    search for one spelling, one zone number, or a near-miss typo can be
    widened to match a listing saved under any of the others. Empty list if
    nothing is recognised, so callers can fall back to a plain match on the
    raw text.
    """
    key = _key(text)
    zone = _zone_number(text)
    if zone is not None:
        districts = ZONE_DISTRICTS.get(zone)
        if not districts:
            return []
        seen = []
        for d in districts:
            for name in _variants_for_en(d):
                if name not in seen:
                    seen.append(name)
        return seen
    hit = _INDEX.get(key)
    if not hit:
        fkey = _fuzzy_key(key)
        hit = _INDEX.get(fkey) if fkey else None
    if not hit:
        return []
    return _variants_for_en(hit["en"])
