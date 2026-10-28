"""Official Qatari sources for market updates, news and tips.

Market figures, news and tips on the page come only from official sources:
government sites (*.gov.qa), the Qatar News Agency (QNA, the state news
agency), the National Planning Council and similar. Nothing is taken from
property portals, blogs or other agencies.

Two ways in:

* Latest items, checked automatically: QNA's local-economy and Qatar news
  feeds (filtered to real estate: the weekly Aqarat and Ministry of Justice
  bulletins, building permits, new rules...) and the news of the Real Estate
  Regulatory Authority (Aqarat) from its own website. Kept for 3 hours.
* Any link from an official site, pasted by hand. If the server can't open
  it, the text can be pasted instead.

fetch_article() returns the article's own words; the post is then written
from that text only, and its figures are checked against it.
"""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha1

# Hosts that count as official (the host itself or any sub-domain of it).
OFFICIAL_DOMAINS = (
    "gov.qa",            # every ministry and authority: aqarat, moj, mme, psa...
    "qna.org.qa",        # Qatar News Agency, the state news agency
    "npc.qa",            # National Planning Council
    "kahramaa.qa",       # electricity and water
    "almeezan.qa",       # Qatar Legal Portal (laws)
    "hukoomi.qa",
)

SOURCE_NAMES = {        # host -> (Arabic, English) for credits
    "aqarat.gov.qa": ("الهيئة العامة لتنظيم القطاع العقاري (عقارات)",
                      "Real Estate Regulatory Authority (Aqarat)"),
    "moj.gov.qa": ("وزارة العدل", "Ministry of Justice"),
    "mme.gov.qa": ("وزارة البلدية", "Ministry of Municipality"),
    "psa.gov.qa": ("المجلس الوطني للتخطيط", "National Planning Council"),
    "npc.qa": ("المجلس الوطني للتخطيط", "National Planning Council"),
    "qcb.gov.qa": ("مصرف قطر المركزي", "Qatar Central Bank"),
    "qna.org.qa": ("وكالة الأنباء القطرية (قنا)", "Qatar News Agency (QNA)"),
    "kahramaa.qa": ("كهرماء", "Kahramaa"),
    "almeezan.qa": ("الميزان - البوابة القانونية القطرية", "Al Meezan, Qatar Legal Portal"),
}

FEEDS = [
    {"key": "qna-economy", "type": "rss", "filter": True,
     "url": "https://qna.org.qa/ar-QA/Pages/RSS-Feeds/Economy-Local",
     "name_ar": "قنا · اقتصاد محلي", "name_en": "QNA · Local economy"},
    {"key": "qna-qatar", "type": "rss", "filter": True,
     "url": "https://qna.org.qa/ar-QA/Pages/RSS-Feeds/Qatar",
     "name_ar": "قنا · قطر", "name_en": "QNA · Qatar"},
    {"key": "aqarat", "type": "wp", "filter": False,
     "url": "https://www.aqarat.gov.qa/wp-json/wp/v2/posts?per_page=15"
            "&_fields=id,date,link,title,excerpt",
     "name_ar": "عقارات · أخبار الهيئة", "name_en": "Aqarat news"},
]

# What makes a QNA item about real estate, and what makes it a company's
# share-price or profit story instead (not what the page is for).
REAL_ESTATE = re.compile(
    r"عقار|عقارات|الإيجار|إيجار|ايجار|تأجير|المستأجر|السكني|الوحدات السكنية|"
    r"تصاريح البناء|رخص البناء|التسجيل العقاري|الوساطة العقارية|المطور|"
    r"التملك|تملك غير القطريين|الرهن|مخطط|الأراضي|real estate|property|"
    r"rent|lease|building permit", re.I)
NOT_FOR_US = re.compile(r"أرباح|ارباح|بورصة|الأسهم|الاسهم|اكتتاب|سندات|صكوك")
FIGURES = re.compile(r"\d[\d,.]*\s*(?:مليون|مليار|ألف)?\s*(?:ريال|بالمئة|في المئة|%)")
MARKET_WORDS = re.compile(r"تداول|مبيعات|المبيعات|صفقات|عقود|قيمة|تصاريح|مؤشر|الرهن")

CACHE_KEY = "official_items"
CACHE_HOURS = 3
UA = "Mozilla/5.0 (compatible; PlannedCRM/1.0; +https://plannedrealestate.com)"


# ----------------------------------------------------------------- basics
def host_of(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_official(url):
    host = host_of(url)
    if not host or not url.lower().startswith(("https://", "http://")):
        return False
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


def source_names(url):
    host = host_of(url)
    for h, names in SOURCE_NAMES.items():
        if host == h or host.endswith("." + h):
            return names
    return (host, host)


def _quote_url(url):
    """Arabic letters in a link must be percent-encoded before sending."""
    p = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(p.path), safe="/%-._~")
    query = urllib.parse.quote(urllib.parse.unquote(p.query), safe="=&%-._~,")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, query, ""))


def get(url, timeout=20):
    req = urllib.request.Request(_quote_url(url), headers={
        "User-Agent": UA, "Accept-Language": "ar,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(3_000_000)
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{host_of(url)} answered with error {exc.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Couldn't reach {host_of(url)} from the server "
                           f"({getattr(exc, 'reason', exc)}).")
    return raw.decode(charset, "replace")


def _text(fragment):
    """HTML fragment -> plain text, one paragraph per line."""
    fragment = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    lines = [re.sub(r"[ \t ]+", " ", html.unescape(l)).strip()
             for l in fragment.split("\n")]
    return "\n".join(l for l in lines if l)


def _meta(page, prop):
    m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]*>' % re.escape(prop), page, re.I) \
        or re.search(r'<meta[^>]+content="[^"]*"[^>]+(?:property|name)="%s"[^>]*>'
                     % re.escape(prop), page, re.I)
    if not m:
        return ""
    c = re.search(r'content="([^"]*)"', m.group(0))
    return html.unescape(c.group(1)).strip() if c else ""


def _item_id(link):
    return sha1(link.encode("utf-8")).hexdigest()[:12]


def _suggest(text):
    return "market" if FIGURES.search(text) and MARKET_WORDS.search(text) else "news"


# ------------------------------------------------------------- the feeds
def _read_rss(feed):
    root = ET.fromstring(get(feed["url"]).encode("utf-8"))
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = _text(it.findtext("description") or "")
        if not (title and link):
            continue
        blob = f"{title} {desc}"
        if feed["filter"] and (not REAL_ESTATE.search(blob) or NOT_FOR_US.search(title)):
            continue
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
            when = when.replace(tzinfo=None).isoformat(timespec="minutes")
        except (TypeError, ValueError):
            when = ""
        items.append({"title": html.unescape(title), "summary": desc[:400],
                      "link": link, "date": when})
    return items


def _read_wp(feed):
    data = json.loads(get(feed["url"]))
    items = []
    for p in data:
        link = p.get("link", "")
        if "/en/" in link:              # the Arabic version of each is enough
            continue
        items.append({"title": _text((p.get("title") or {}).get("rendered", "")),
                      "summary": _text((p.get("excerpt") or {}).get("rendered", ""))[:400],
                      "link": link, "date": (p.get("date") or "")[:16]})
    return items


def discover(cfg_get, cfg_set, force=False):
    """Latest real-estate items from the feeds: (items, problems).

    cfg_get / cfg_set read and write the social settings table, where the
    list is kept for CACHE_HOURS so the page opens quickly."""
    try:
        cached = json.loads(cfg_get(CACHE_KEY) or "{}")
    except ValueError:
        cached = {}
    fresh = time.time() - cached.get("at", 0) < CACHE_HOURS * 3600
    if cached and fresh and not force:
        return cached.get("items", []), cached.get("problems", [])
    items, problems, seen = [], [], set()
    for feed in FEEDS:
        try:
            found = _read_rss(feed) if feed["type"] == "rss" else _read_wp(feed)
        except (RuntimeError, ET.ParseError, ValueError) as exc:
            problems.append(f"{feed['name_en']}: {exc}")
            continue
        for it in found:
            if it["link"] in seen:
                continue
            seen.add(it["link"])
            it.update(id=_item_id(it["link"]), feed=feed["key"],
                      feed_ar=feed["name_ar"], feed_en=feed["name_en"],
                      suggest=_suggest(it["title"] + " " + it["summary"]))
            items.append(it)
    # The feeds only reach back a day or two, so what was found before is
    # kept (for 45 days): the weekly bulletins stay on the list all week.
    cutoff = (datetime.utcnow() - timedelta(days=45)).isoformat(timespec="minutes")
    for old in cached.get("items", []):
        if old.get("link") not in seen and (old.get("date") or "") >= cutoff:
            seen.add(old["link"])
            items.append(old)
    items.sort(key=lambda i: i["date"], reverse=True)
    items = items[:60]
    if items or not cached:
        cfg_set(CACHE_KEY, json.dumps({"at": time.time(), "items": items,
                                       "problems": problems},
                                      ensure_ascii=False))
    elif cached:                        # keep the old list if all failed
        items = cached.get("items", [])
    return items, problems


# ---------------------------------------------------------- one article
def fetch_article(url):
    """{'title', 'text', 'date', 'url', 'source_ar', 'source_en'} from an
    official page. Raises RuntimeError with a plain reason."""
    if not is_official(url):
        raise RuntimeError("That link isn't from an official Qatari source "
                           "(a .gov.qa site, QNA or the Planning Council).")
    page = get(url)
    title = _meta(page, "og:title") or _text(
        (re.search(r"(?is)<title>(.*?)</title>", page) or [None, ""])[1])
    body = ""
    m = re.search(r'(?is)<div[^>]+id="news-reader"[^>]*>(.*?)</div>', page)  # QNA
    if m:
        body = _text(m.group(1))
    if not body:
        m = re.search(r'(?is)<div[^>]+class="[^"]*(?:entry-content|post-content|'
                      r'article-body|news-details?)[^"]*"[^>]*>(.*?)</(?:div|article)>',
                      page)
        if m:
            body = _text(m.group(1))
    if not body:                         # any page: its real paragraphs
        paras = [_text(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", page)]
        body = "\n".join(p for p in paras if len(p) > 40)
    if not body:
        body = _meta(page, "og:description") or _meta(page, "description")
    if len(body) < 60:
        raise RuntimeError("The page opened, but no article text was found on it. "
                           "Paste the text instead.")
    date = ""
    d = re.search(r"/(20\d\d)-(\d{1,2})/(\d{1,2})/", url)     # QNA links
    if d:
        date = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}"
    ar, en = issuer_names(body, url)
    return {"title": title.strip(), "text": body[:9000], "date": date, "url": url,
            "source_ar": ar, "source_en": en}


# Who actually issued the information (QNA reports for ministries and
# authorities): credited as "issuer · QNA".
ISSUERS = [
    (r"الهيئة العامة لتنظيم القطاع العقاري|[\"«“]عقارات[\"»”]|^عقارات:", "aqarat.gov.qa"),
    (r"وزارة العدل|إدارة التسجيل العقاري", "moj.gov.qa"),
    (r"وزارة البلدية", "mme.gov.qa"),
    (r"المجلس الوطني للتخطيط|جهاز التخطيط والإحصاء", "npc.qa"),
    (r"مصرف قطر المركزي", "qcb.gov.qa"),
]


def issuer_names(text, url):
    ar, en = source_names(url)
    if host_of(url).endswith("qna.org.qa"):
        # The issuer named first in the opening lines is the one reporting.
        head = text[:600]
        found = [(m.start(), host) for pattern, host in ISSUERS
                 for m in [re.search(pattern, head, re.M)] if m]
        if found:
            iar, ien = SOURCE_NAMES[min(found)[1]]
            return f"{iar} · قنا", f"{ien} via QNA"
    return ar, en


def pasted_article(url, text):
    """The same shape as fetch_article(), from text pasted by hand."""
    text = (text or "").strip()
    if not is_official(url):
        raise RuntimeError("Give the link of the official page the text comes "
                           "from (a .gov.qa site, QNA or the Planning Council).")
    if len(text) < 60:
        raise RuntimeError("Paste the full text of the announcement.")
    ar, en = issuer_names(text, url)
    first = text.split("\n", 1)[0][:160]
    return {"title": first, "text": text[:9000], "date": "", "url": url,
            "source_ar": ar, "source_en": en}


# ------------------------------------------------- figure check (no guesses)
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def numbers_in(text):
    """Every number in a text, normalised: '1,234.5' and '1234.5' match."""
    text = str(text or "").translate(_AR_DIGITS)
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def unsupported_numbers(claimed, source_text):
    """Numbers in `claimed` that do not appear in the source text."""
    have = numbers_in(source_text)
    return sorted(n for n in numbers_in(claimed) if n not in have)


def stamp_now():
    return datetime.utcnow().isoformat(timespec="minutes")
