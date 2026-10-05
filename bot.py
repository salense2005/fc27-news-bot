#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LILSNEWS - EA SPORTS FC 27 Telegram news bot (rewritten, drop-in bot.py)

Pipeline:
  Google News RSS -> FC 27 filter -> dedup/clustering -> article extraction
  (+ photo search: og:image / twitter:image / JSON-LD / article <img> / RSS)
  -> Gemini (JSON, Russian posts, SOURCE IDs) -> post validation
  -> duplicate validation -> Telegram (ONE message: photo + caption, or text only)
  -> memory (published_news.json)

If Gemini is unavailable (429 / timeout / 5xx / garbage), a fallback builds a
Russian post from the article text (machine translation) and validates it with
exactly the same rules. If a good post cannot be built, the material is skipped.

Environment variables (unchanged):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY
Secrets are never printed to the log.

Run: python bot.py
"""

import base64
import calendar
import difflib
import faulthandler
import html
import json
import os
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from urllib.parse import quote, quote_plus, urljoin, urlparse, urlunparse, parse_qsl, urlencode

import requests
import feedparser
from bs4 import BeautifulSoup


# ============================================================
# CONFIG (environment variables - names must not change)
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()


# ============================================================
# SETTINGS
# ============================================================

MEMORY_FILE = "published_news.json"
MAX_MEMORY_ITEMS = 500

CHECK_INTERVAL = 5 * 60                 # pause between cycles
CYCLE_WATCHDOG_SECONDS = 20 * 60        # dump stack traces into the log if a cycle hangs

# --- RSS ---
MAX_RSS_ITEMS_PER_QUERY = 10
RSS_WHEN = "3d"                         # Google News "when:" operator ("" disables)
MAX_ARTICLE_AGE_HOURS = 96              # extra local age filter (if RSS has a date)
RSS_TIMEOUT = (5, 12)                   # (connect, read) seconds

# --- selection / fetching ---
MAX_CANDIDATES = 30
MAX_ARTICLES_TO_FETCH = 18
FETCH_WORKERS = 5
ARTICLE_TIMEOUT = (5, 8)                # (connect, read)
ARTICLE_TOTAL_TIMEOUT = 20              # whole download budget per page, seconds
MAX_HTML_BYTES = 1_500_000
PREPARE_TIMEOUT = 150                   # whole parallel fetch stage, seconds
GOOGLE_TIMEOUT = (5, 8)
MIN_FULL_ARTICLE_CHARS = 500
MIN_RSS_SUMMARY_LENGTH = 60
MAX_ARTICLE_CHARS = 9000

# --- Gemini ---
# gemini-3.8-flash is listed in the current Gemini API docs (generateContent, v1beta).
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]   # second one = automatic backup
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT = (10, 120)
GEMINI_RETRIES = 2                      # retries for 5xx / timeouts
GEMINI_MAX_OUTPUT_TOKENS = 8192
GEMINI_DEFAULT_COOLDOWN = 60 * 60       # after 429 / bad key
GEMINI_MIN_COOLDOWN = 10 * 60
MAX_ARTICLES_FOR_GEMINI = 12
GEMINI_CHARS_PER_ARTICLE = 4500
REPAIR_LIMIT_PER_CYCLE = 3              # extra "rewrite in Russian" calls per cycle

# --- posts ---
MAX_POSTS_PER_CYCLE = 5
MIN_BODY_LENGTH = 110
MIN_SENTENCES = 2
MIN_SENTENCE_LENGTH = 20
MAX_POST_LENGTH = 1500                  # Telegram hard limit is 4096
STRICT_NUMBER_CHECK = True              # numbers in a post must exist in the source text

# --- channel footer (clickable word at the very end of every post) ---
CHANNEL_FOOTER_TEXT = "Lilsalense"
CHANNEL_URL = "https://t.me/Lilsalense"

# --- fallback ---
FALLBACK_TRANSLATE = True               # unofficial free translate endpoint, may break
FALLBACK_MAX_SENTENCES = 4
FALLBACK_MAX_BODY_CHARS = 650

# --- photos (free: taken from the source article itself, NO image generation) ---
IMAGES_ENABLED = True
IMAGE_TIMEOUT = (5, 10)                 # (connect, read) seconds for one image request
IMAGE_TOTAL_TIMEOUT = 15                # whole download budget per image, seconds
MAX_IMAGE_BYTES = 9_000_000             # Telegram photo limit is 10 MB
MIN_IMAGE_BYTES = 5_000                 # smaller = icon / tracking pixel
MIN_IMAGE_WIDTH = 300
MIN_IMAGE_HEIGHT = 150
MAX_IMAGE_CANDIDATES = 8                # image URLs remembered per article
MAX_IMAGE_ATTEMPTS = 6                  # download attempts per post
TELEGRAM_CAPTION_LIMIT = 1024           # Telegram hard limit for a photo caption

# --- skip cache (RAM only, NOT the published memory) ---
SKIP_TTL_REVIEWED = 3 * 3600            # Gemini reviewed it and did not publish it
SKIP_TTL_NO_CONTENT = 1 * 3600          # nothing usable could be extracted

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SEARCH_QUERIES = [
    "EA FC 27 SBC",
    "EA FC 27 Ultimate Team cards",
    "EA FC 27 players leaked",
    "EA FC 27 ratings",
    "EA FC 27 promo",
    "EA FC 27 meta",
    "EA FC 27 tactics",
    "EA FC 27 gameplay",
    "EA FC 27 patch",
    "EA FC 27 objective",
    "EA FC 27 evolution",
    "EA FC 27 pro players",
    "EA FC 27 Ultimate Team news",
    "EA FC 27 Team 2",
    "EA FC 27 update",
]

CATEGORY_LABELS = {
    "META": "🔥 META",
    "GAMEPLAY": "🎮 GAMEPLAY",
    "PATCH": "🛠 ПАТЧ",
    "SBC": "🃏 SBC",
    "PROMO": "🟣 PROMO",
    "RUMOR": "⚠️ СЛУХ",
    "PLAYERS": "⭐ PLAYERS",
    "RATINGS": "📊 RATINGS",
    "OBJECTIVE": "🎯 OBJECTIVE",
    "EVOLUTION": "🔄 EVOLUTION",
    "NEWS": "📰 NEWS",
}

CATEGORY_ALIASES = {
    "ПАТЧ": "PATCH", "UPDATE": "PATCH", "ОБНОВЛЕНИЕ": "PATCH",
    "СЛУХ": "RUMOR", "LEAK": "RUMOR", "LEAKS": "RUMOR", "СЛУХИ": "RUMOR",
    "PLAYER": "PLAYERS", "CARDS": "PLAYERS", "CARD": "PLAYERS",
    "RATING": "RATINGS", "OBJECTIVES": "OBJECTIVE", "EVOLUTIONS": "EVOLUTION",
}


# ============================================================
# LOGGING (never prints secrets)
# ============================================================

def redact(text):
    s = str(text)
    for secret in (TELEGRAM_TOKEN, GEMINI_API_KEY):
        if secret and len(secret) >= 6:
            s = s.replace(secret, "***")
    s = re.sub(r"bot\d{5,}:[A-Za-z0-9_-]{20,}", "bot***", s)
    s = re.sub(r"(key=)[A-Za-z0-9_-]{20,}", r"\1***", s)
    return s


def log(tag, message=""):
    print(redact(f"[{tag}] {message}"), flush=True)


def warn(tag, message=""):
    print(redact(f"[WARN][{tag}] {message}"), flush=True)


def error(tag, message=""):
    print(redact(f"[ERROR][{tag}] {message}"), flush=True)


def banner(text):
    print("", flush=True)
    print("================================", flush=True)
    print(text, flush=True)
    print("================================", flush=True)


def err_text(exc):
    return redact(f"{type(exc).__name__}: {exc}")


# ============================================================
# TEXT UTILS
# ============================================================

STOPWORDS = set("""
the a an and or of to in on for with at by from as is are was were be been it its this that these those
new news ea sports sport fc 27 fifa ultimate team has have had will can could may might into out up
about after before over under more most all any also than then now just how what when where which who
and но или для при что это как его ее их она они был была были есть будет также уже еще после перед
над под про все всех всё так вот там тут чем чтобы если когда где который которая которые
""".split())


def now_ts():
    return int(time.time())


def normalize_whitespace(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_text(text):
    if not text:
        return ""
    text = html.unescape(str(text))
    for tag in ("script", "style", "noscript"):
        text = re.sub(rf"<{tag}.*?</{tag}>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_whitespace(text)


def normalize_title(title):
    t = clean_text(title).lower()
    t = re.sub(r"\b(fc\s*27|ea\s*sports|ea)\b", " ", t)
    t = re.sub(r"[^a-zа-яё0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _stem(word):
    if len(word) > 5 and re.match(r"[а-яё]", word):
        return word[:5]
    return word


def content_tokens(text):
    t = clean_text(text).lower()
    t = re.sub(r"[^a-zа-яё0-9]+", " ", t)
    out = set()
    for w in t.split():
        if w in STOPWORDS:
            continue
        if len(w) < 3 and not (w.isdigit() and len(w) >= 2):
            continue
        out.add(_stem(w))
    return out


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similar_titles(a, b, threshold):
    """Fuzzy title match that refuses to match very short/generic titles."""
    ta, tb = content_tokens(a), content_tokens(b)
    if len(ta) < 3 or len(tb) < 3:
        return False
    if normalize_title(a) == normalize_title(b):
        return True
    return jaccard(ta, tb) >= threshold


def canonical_url(url):
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        query = [
            (k, v) for k, v in parse_qsl(p.query)
            if not (k.lower().startswith("utm_") or k.lower() in
                    {"fbclid", "gclid", "oc", "hl", "gl", "ceid", "ref", "source"})
        ]
        path = p.path.rstrip("/")
        return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(query), ""))
    except Exception:
        return url.strip().lower()


def split_sentences(text):
    parts = []
    for para in re.split(r"\n+", str(text or "")):
        para = para.strip()
        if not para:
            continue
        pieces = re.split(r'(?<=[.!?…])\s+(?=[«"“(\[]?[A-ZА-ЯЁ0-9])', para)
        parts.extend(p.strip() for p in pieces if p.strip())
    return parts


def is_fc27_title(title):
    low = title.lower()
    forbidden = ["fc 26", "fc26", "fifa 26", "fc 25", "fc25", "fifa 25", "fifa 24", "fc 24"]
    if any(w in low for w in forbidden):
        return False
    return "fc 27" in low or "fc27" in low


# ============================================================
# LANGUAGE CHECK (local)
# ============================================================

def language_stats(text):
    t = re.sub(r"https?://\S+", " ", str(text or ""))
    t = re.sub(r"\b[A-Z0-9]{2,6}\b", " ", t)          # SBC, OVR, TOTY, FUT, PS5...
    t = re.sub(r"\bFC\s?\d+\b", " ", t, flags=re.I)
    t = re.sub(r"\b(?:EA\s+SPORTS|Ultimate\s+Team)\b", " ", t, flags=re.I)
    cyr = len(re.findall(r"[А-Яа-яЁё]", t))
    lat = len(re.findall(r"[A-Za-z]", t))
    return cyr, lat


def is_russian(text, min_ratio, min_cyr):
    cyr, lat = language_stats(text)
    if cyr < min_cyr:
        return False
    return cyr / float(cyr + lat or 1) >= min_ratio


# ============================================================
# HTTP HELPERS
# ============================================================

def fetch_html(url, total_timeout=ARTICLE_TOTAL_TIMEOUT, max_bytes=MAX_HTML_BYTES):
    """Streamed download with a hard total deadline. Returns (final_url, status, text|None)."""
    deadline = time.monotonic() + total_timeout
    with requests.get(url, headers=HEADERS, timeout=ARTICLE_TIMEOUT,
                      stream=True, allow_redirects=True) as resp:
        status = resp.status_code
        final_url = resp.url or url
        ctype = (resp.headers.get("content-type") or "").lower()
        if status != 200:
            return final_url, status, None
        if ctype and "html" not in ctype and "xml" not in ctype:
            return final_url, status, None
        chunks, size = [], 0
        for chunk in resp.iter_content(chunk_size=16384):
            if not chunk:
                continue
            chunks.append(chunk)
            size += len(chunk)
            if size >= max_bytes:
                break
            if time.monotonic() > deadline:
                raise requests.Timeout("total download deadline exceeded")
        raw = b"".join(chunks)
        m = re.search(r"charset=([\w-]+)", ctype)
        encoding = m.group(1) if m else "utf-8"
        try:
            return final_url, status, raw.decode(encoding, errors="replace")
        except LookupError:
            return final_url, status, raw.decode("utf-8", errors="replace")


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method):
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"


def test_telegram():
    log("TELEGRAM", "Testing connection...")
    for attempt in range(1, 4):
        try:
            resp = requests.get(telegram_url("getMe"), timeout=(5, 15))
            log("TELEGRAM", f"getMe HTTP {resp.status_code}")
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                name = data.get("result", {}).get("username", "unknown")
                log("TELEGRAM", f"Connection OK: @{name}")
                return True
            error("TELEGRAM", f"getMe rejected: {data.get('description', data)}")
            if resp.status_code in (401, 404):
                return False          # wrong token - retrying is pointless
        except Exception as exc:
            error("TELEGRAM", f"getMe failed (attempt {attempt}/3): {err_text(exc)}")
        time.sleep(3 * attempt)
    return False


def send_telegram(message, parse_mode=None, plain_fallback=None):
    """Raises on any failure. Returns True only if Telegram confirmed ok=true."""
    if not message:
        raise ValueError("empty message")
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    for attempt in range(2):
        resp = requests.post(telegram_url("sendMessage"), json=payload, timeout=(5, 30))
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 429 and attempt == 0:
            wait = int(data.get("parameters", {}).get("retry_after", 5))
            wait = max(1, min(wait, 30))
            warn("TELEGRAM", f"Rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        if (resp.status_code == 400 and parse_mode and plain_fallback
                and "parse" in str(data.get("description", "")).lower()):
            warn("TELEGRAM", "HTML was rejected by Telegram - resending as plain text")
            return send_telegram(plain_fallback)
        if resp.status_code != 200:
            raise RuntimeError(f"Telegram HTTP {resp.status_code}: {data.get('description', resp.text[:200])}")
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return True
    raise RuntimeError("Telegram rate limit persisted")


def send_telegram_photo(image, caption, parse_mode=None, plain_fallback=None):
    """ONE Telegram post: photo + caption (sendPhoto). Raises on any failure."""
    if not image or not image.get("data"):
        raise ValueError("empty image")
    if not caption:
        raise ValueError("empty caption")
    payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    for attempt in range(2):
        files = {"photo": (f"photo.{image.get('ext', 'jpg')}", image["data"],
                           image.get("mime", "image/jpeg"))}
        resp = requests.post(telegram_url("sendPhoto"), data=payload, files=files, timeout=(5, 60))
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 429 and attempt == 0:
            wait = int(data.get("parameters", {}).get("retry_after", 5))
            wait = max(1, min(wait, 30))
            warn("TELEGRAM", f"Rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        if (resp.status_code == 400 and parse_mode and plain_fallback
                and "parse" in str(data.get("description", "")).lower()):
            warn("TELEGRAM", "HTML caption was rejected by Telegram - resending photo with plain caption")
            return send_telegram_photo(image, plain_fallback)
        if resp.status_code != 200:
            raise RuntimeError(f"Telegram photo HTTP {resp.status_code}: {data.get('description', resp.text[:200])}")
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return True
    raise RuntimeError("Telegram rate limit persisted")


# ============================================================
# MEMORY (published_news.json - ONLY really published posts)
# ============================================================

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        log("MEMORY", "File does not exist, starting empty")
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        warn("MEMORY", "Unexpected file format, starting empty")
    except Exception as exc:
        error("MEMORY", f"Read error: {err_text(exc)}")
        try:
            os.replace(MEMORY_FILE, MEMORY_FILE + ".broken")
            warn("MEMORY", f"Broken file moved to {MEMORY_FILE}.broken")
        except Exception:
            pass
    return []


def save_memory(memory):
    try:
        trimmed = memory[-MAX_MEMORY_ITEMS:]
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MEMORY_FILE)
        log("MEMORY", f"Saved ({len(trimmed)} items)")
    except Exception as exc:
        error("MEMORY", f"Save error: {err_text(exc)}")


def memory_url_set(memory):
    urls = set()
    for e in memory:
        for key in ("source_url", "rss_url", "link"):
            if e.get(key):
                urls.add(canonical_url(e[key]))
        for key in ("source_urls", "rss_urls"):
            for v in e.get(key) or []:
                if v:
                    urls.add(canonical_url(v))
    return urls


def memory_title_list(memory):
    titles = []
    for e in memory:
        for t in [e.get("title"), e.get("post_title")] + list(e.get("source_titles") or []):
            if t:
                titles.append(t)
    return titles


def remember_publication(memory, post):
    sources = post["sources"]
    primary = sources[0]
    memory.append({
        "topic": post["topic"],
        "title": primary.get("title", ""),
        "source_url": primary.get("url", ""),
        "rss_url": primary.get("rss_url", ""),
        "source_urls": sorted({canonical_url(u) for s in sources
                               for u in [s.get("url", ""), s.get("rss_url", "")] + list(s.get("alt_links", []))
                               if u}),
        "source_titles": [t for s in sources for t in [s.get("title", "")] + list(s.get("alt_titles", [])) if t],
        "post_title": post["title"],
        "category": post["category"],
        "post": post["text"],
        "timestamp": now_ts(),
    })
    return memory


# ============================================================
# SKIP CACHE (RAM only; reviewed-but-not-published materials)
# ============================================================

_skip_cache = {}


def _skip_keys(item):
    keys = [canonical_url(item.get("rss_url") or item.get("link") or "")]
    keys += [canonical_url(u) for u in item.get("alt_links", [])]
    return [k for k in keys if k]


def skip_item(item, ttl):
    expiry = time.time() + ttl
    for k in _skip_keys(item):
        _skip_cache[k] = expiry


def is_skipped(item):
    now = time.time()
    k = canonical_url(item.get("link") or item.get("rss_url") or "")
    exp = _skip_cache.get(k)
    if exp is None:
        return False
    if exp > now:
        return True
    _skip_cache.pop(k, None)
    return False


# ============================================================
# GOOGLE NEWS RSS
# ============================================================

def make_rss_url(query):
    q = query + (f" when:{RSS_WHEN}" if RSS_WHEN else "")
    return "https://news.google.com/rss/search?q=" + quote_plus(q) + "&hl=en-US&gl=US&ceid=US:en"


def strip_source_suffix(title, source_name):
    if source_name and title.lower().endswith(" - " + source_name.lower()):
        return title[: -(len(source_name) + 3)].strip()
    return title


def meaningful_summary(summary, raw_title, title, source_name):
    """Google News RSS summary is usually just 'Title  Publisher'. Return only real extra content."""
    s = clean_text(summary)
    if not s:
        return ""
    for chunk in (raw_title, title, source_name):
        if chunk:
            s = re.sub(re.escape(chunk), " ", s, flags=re.I)
    s = normalize_whitespace(s).strip(" -–—|·")
    return s if len(s) >= MIN_RSS_SUMMARY_LENGTH else ""


def rss_image_from_entry(entry):
    """Image URL offered by the RSS entry itself (media tags / enclosure / <img> in summary), or ''."""
    try:
        for key in ("media_content", "media_thumbnail"):
            for m in entry.get(key) or []:
                if isinstance(m, dict) and m.get("url"):
                    return str(m["url"])
        for group in ("links", "enclosures"):
            for l in entry.get(group) or []:
                if isinstance(l, dict) and str(l.get("type", "")).startswith("image/") and l.get("href"):
                    return str(l["href"])
        raw = entry.get("summary", "") or entry.get("description", "") or ""
        m = re.search(r"<img[^>]+src=[\"']([^\"']+)", raw, re.I)
        if m:
            return html.unescape(m.group(1))
    except Exception:
        pass
    return ""


def get_news():
    log("RSS", "Starting Google News RSS search...")
    items, seen = [], set()
    dropped_old = dropped_filter = 0
    now = time.time()

    for query in SEARCH_QUERIES:
        log("RSS", f"Searching: {query}")
        try:
            resp = requests.get(make_rss_url(query), headers=HEADERS, timeout=RSS_TIMEOUT)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as exc:
            error("RSS", f"'{query}': {err_text(exc)}")
            continue

        found = accepted = 0
        for entry in feed.entries[:MAX_RSS_ITEMS_PER_QUERY]:
            found += 1
            raw_title = clean_text(entry.get("title", ""))
            link = (entry.get("link") or "").strip()
            if not raw_title or not link:
                continue
            source_name = ""
            src = entry.get("source")
            if isinstance(src, dict):
                source_name = clean_text(src.get("title", ""))
            title = strip_source_suffix(raw_title, source_name)

            if not is_fc27_title(title):
                dropped_filter += 1
                continue

            published_ts = 0
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if parsed:
                try:
                    published_ts = calendar.timegm(parsed)
                except Exception:
                    published_ts = 0
            if published_ts and now - published_ts > MAX_ARTICLE_AGE_HOURS * 3600:
                dropped_old += 1
                continue

            key = canonical_url(link)
            if key in seen:
                continue
            seen.add(key)

            items.append({
                "title": title,
                "link": link,
                "summary": meaningful_summary(entry.get("summary", "") or entry.get("description", ""),
                                              raw_title, title, source_name),
                "published_ts": published_ts,
                "source_name": source_name,
                "rss_image": rss_image_from_entry(entry),
            })
            accepted += 1
        log("RSS", f"Found: {found}, accepted: {accepted}")

    log("RSS", f"Total unique: {len(items)} (dropped non-FC27: {dropped_filter}, too old: {dropped_old})")
    return items


# ============================================================
# PRIORITY / CLUSTERING / FRESHNESS
# ============================================================

PRIORITY_POINTS = [
    (r"\bsbc\b", 180), (r"\bnew sbc\b", 80), (r"\bmeta\b", 150), (r"\btactics?\b", 130),
    (r"\bgameplay\b", 125), (r"\bpatch\b", 125), (r"\bupdate\b", 90), (r"\bplayers?\b", 80),
    (r"\bcards?\b", 110), (r"\bratings?\b", 85), (r"\bpromo\b", 100), (r"\bteam [12]\b", 90),
    (r"\bleak(?:ed|s)?\b", 65), (r"\bobjectives?\b", 90), (r"\bevolutions?\b", 90),
    (r"\bupgrade\b", 70), (r"\bultimate team\b", 40), (r"\bpro players?\b", 120),
]


def calculate_priority(title):
    low = title.lower()
    return sum(points for pattern, points in PRIORITY_POINTS if re.search(pattern, low))


def cluster_news(items):
    """Merge near-identical titles (same event from several sites). Conservative on purpose;
    Gemini merges the semantic duplicates via source_ids."""
    clusters = []
    for item in items:
        merged = False
        for rep in clusters:
            if similar_titles(item["title"], rep["title"], 0.6):
                rep.setdefault("alt_links", []).append(item["link"])
                rep.setdefault("alt_titles", []).append(item["title"])
                if not rep.get("summary") and item.get("summary"):
                    rep["summary"] = item["summary"]
                if not rep.get("rss_image") and item.get("rss_image"):
                    rep["rss_image"] = item["rss_image"]
                merged = True
                break
        if not merged:
            item.setdefault("alt_links", [])
            item.setdefault("alt_titles", [])
            clusters.append(item)
    return clusters


def select_fresh_news(news, memory):
    mem_urls = memory_url_set(memory)
    mem_titles = memory_title_list(memory)
    fresh = []
    skipped = in_memory = 0

    for item in news:
        if is_skipped(item):
            skipped += 1
            continue
        if canonical_url(item["link"]) in mem_urls:
            in_memory += 1
            continue
        if any(similar_titles(item["title"], t, 0.85) for t in mem_titles):
            in_memory += 1
            continue
        item["priority"] = calculate_priority(item["title"])
        fresh.append(item)

    fresh.sort(key=lambda x: (x["priority"], x.get("published_ts", 0)), reverse=True)
    fresh = cluster_news(fresh)
    log("FILTER", f"Already published: {in_memory}, recently reviewed (skipped): {skipped}")
    log("FILTER", f"Fresh: {len(fresh)}")
    return fresh[:MAX_CANDIDATES]


# ============================================================
# GOOGLE NEWS URL RESOLUTION (best effort, never fatal)
# ============================================================

_resolved_cache = {}


def is_google_news_url(url):
    return "news.google.com" in (urlparse(url).netloc or "")


def google_article_id(url):
    m = re.search(r"/(?:rss/)?articles/([^/?#]+)", urlparse(url).path or "")
    return m.group(1) if m else ""


def _offline_decode(article_id):
    """Old-style Google News ids embed the URL inside base64."""
    try:
        raw = base64.urlsafe_b64decode(article_id + "=" * (-len(article_id) % 4))
        m = re.search(rb"https?://[\x21-\x7e]+", raw)
        if m:
            candidate = m.group(0).decode("ascii", errors="ignore")
            if candidate and not is_google_news_url(candidate):
                return candidate
    except Exception:
        pass
    return ""


def _unescape_url(s):
    s = s.replace("\\/", "/").replace("\\u003d", "=").replace("\\u0026", "&")
    return s


def _batchexecute_decode(article_id, page_text):
    sig = re.search(r'data-n-a-sg="([^"]+)"', page_text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page_text)
    if not sig or not ts:
        return ""
    inner = json.dumps([
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        article_id, int(ts.group(1)), sig.group(1),
    ])
    body = "f.req=" + quote(json.dumps([[["Fbv4je", inner, None, "generic"]]]))
    resp = requests.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        data=body, timeout=GOOGLE_TIMEOUT,
    )
    if resp.status_code != 200:
        return ""
    try:
        parsed = json.loads(resp.text.split("\n\n")[1])
        url = json.loads(parsed[0][2])[1]
        if isinstance(url, str) and url.startswith("http"):
            return url
    except Exception:
        pass
    m = re.search(r'garturlres\\*",\s*\\*"(https?:[^"\\]+)', resp.text)
    return _unescape_url(m.group(1)) if m else ""


def resolve_url(url):
    """Real publisher URL, or the original RSS URL if anything goes wrong."""
    if not url or not is_google_news_url(url):
        return url
    if url in _resolved_cache:
        return _resolved_cache[url]

    result = url
    try:
        article_id = google_article_id(url)
        found = _offline_decode(article_id) if article_id else ""
        if not found and article_id:
            for page_url in (url, f"https://news.google.com/articles/{article_id}"):
                try:
                    resp = requests.get(page_url, headers=HEADERS, timeout=GOOGLE_TIMEOUT, allow_redirects=True)
                except Exception:
                    continue
                if resp.url and not is_google_news_url(resp.url):
                    found = resp.url
                    break
                if resp.status_code == 200:
                    found = _batchexecute_decode(article_id, resp.text)
                    if found:
                        break
        if found and not is_google_news_url(found):
            result = found
    except Exception as exc:
        warn("FETCH", f"Google URL resolve failed: {err_text(exc)}")

    _resolved_cache[url] = result
    return result


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

NOISE_ATTR = re.compile(
    r"(cookie|consent|banner|newsletter|subscribe|related|recommend|share|social|comment|advert|"
    r"promo-box|sidebar|widget|popup|modal|menu|breadcrumb|footer|outbrain|taboola|sponsor|"
    r"paywall|signup|sign-up|author-box|tags-list)", re.I)

BOILERPLATE = re.compile(
    r"(read more|click here|click to|subscribe|sign up|sign in|log in|follow us|follow @|newsletter|"
    r"cookie|privacy policy|terms of (?:use|service)|all rights reserved|advertisement|sponsored|"
    r"share this|share on|related (?:articles|posts|stories)|you may also like|more from|"
    r"watch the latest|check out (?:our|the|more)|join our|download the app|copyright|©|"
    r"affiliate|we may earn|image credit|photo credit|read next|read also|"
    r"читайте (?:больше|также)|подписывайтесь|нажмите здесь)", re.I)

CONTAINER_SELECTORS = [
    "[itemprop='articleBody']", "article", ".article-body", ".article-content", ".article__body",
    ".article__content", ".post-content", ".post__content", ".entry-content", ".story-body",
    ".story-content", ".articleBody", ".articleText", ".article-text", ".content-body", "main",
]


def clean_article_text(text):
    """Drop boilerplate sentences / duplicate lines from extracted article text."""
    kept, seen = [], set()
    for sentence in split_sentences(text):
        s = normalize_whitespace(sentence)
        if len(s) < 25:
            continue
        if BOILERPLATE.search(s) and len(s) < 220:
            continue
        key = normalize_title(s)
        if key in seen:
            continue
        seen.add(key)
        kept.append(s)
    return " ".join(kept)


def _jsonld_bodies(soup):
    bodies = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue
        objects = []
        if isinstance(data, list):
            objects = data
        elif isinstance(data, dict):
            graph = data.get("@graph")
            objects = graph if isinstance(graph, list) else [data]
        for obj in objects:
            if isinstance(obj, dict) and obj.get("articleBody"):
                body = clean_text(obj["articleBody"])
                if len(body) >= 300:
                    bodies.append(body)
    return bodies


def _strip_noise(soup):
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside",
                     "form", "iframe", "svg", "button", "select", "template"]):
        try:
            tag.decompose()
        except Exception:
            pass
    to_remove = []
    for tag in soup.find_all(True):
        if tag.name in ("html", "body", "main", "article"):
            continue
        try:
            marker = " ".join(tag.get("class") or []) + " " + (tag.get("id") or "")
        except Exception:
            continue
        if marker.strip() and NOISE_ATTR.search(marker):
            to_remove.append(tag)
    for tag in to_remove:
        try:
            tag.decompose()
        except Exception:
            pass


def extract_article_text(soup):
    bodies = _jsonld_bodies(soup)
    if bodies:
        text = clean_article_text(max(bodies, key=len))
        if len(text) >= 300:
            return text

    meta = ""
    for attrs in ({"property": "og:description"}, {"name": "description"}, {"name": "twitter:description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            meta = clean_text(tag["content"])
            break

    _strip_noise(soup)

    candidates = []
    for selector in CONTAINER_SELECTORS:
        try:
            elements = soup.select(selector)
        except Exception:
            continue
        for el in elements:
            paras = []
            for p in el.find_all(["p", "h2", "h3", "li"]):
                t = clean_text(p.get_text(" ", strip=True))
                if len(t) >= 40:
                    paras.append(t)
            text = clean_article_text("\n".join(paras))
            if len(text) >= 300:
                candidates.append(text)
    if candidates:
        return max(candidates, key=len)

    paras = [clean_text(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
    text = clean_article_text("\n".join(t for t in paras if len(t) >= 40))
    if len(text) >= 300:
        return text

    return meta


def fetch_article(item):
    rss_url = item["link"]
    title = item["title"]
    summary = item.get("summary", "")
    log("FETCH", f"Article: {title[:90]}")

    real_url = resolve_url(rss_url)
    if real_url != rss_url:
        log("FETCH", f"Real URL: {real_url}")

    base = {
        "title": title,
        "rss_url": rss_url,
        "url": real_url,
        "priority": item.get("priority", 0),
        "published_ts": item.get("published_ts", 0),
        "alt_links": item.get("alt_links", []),
        "alt_titles": item.get("alt_titles", []),
        "summary": summary,
        "image_candidates": [],
        "image_url": "",
    }
    # image offered by the RSS entry = lowest-priority candidate (kept even if the page fails)
    rss_image = _clean_image_url(item.get("rss_image"), rss_url)
    if rss_image:
        base["image_candidates"] = [rss_image]
        base["image_url"] = rss_image

    if not is_google_news_url(real_url):
        try:
            final_url, status, page = fetch_html(real_url)
            if page:
                soup = BeautifulSoup(page, "html.parser")
                find_article_images(base, soup, final_url)      # BEFORE text extraction (it edits the soup)
                text = extract_article_text(soup)
                if len(text) >= MIN_FULL_ARTICLE_CHARS:
                    log("FETCH", f"Success: full article {len(text)} chars")
                    base.update({"url": final_url, "text": text[:MAX_ARTICLE_CHARS],
                                 "source_type": "full_article"})
                    return base
                log("FETCH", f"Article too short/empty ({len(text)} chars), HTTP {status}")
            else:
                log("FETCH", f"No usable HTML, HTTP {status}")
        except Exception as exc:
            warn("FETCH", f"Article download failed: {err_text(exc)}")
    else:
        log("FETCH", "Google URL not resolved - using RSS data only")

    if summary and len(summary) >= MIN_RSS_SUMMARY_LENGTH:
        log("FETCH", f"RSS fallback: summary {len(summary)} chars")
        base.update({"text": summary[:5000], "source_type": "rss_summary"})
        return base

    log("FETCH", "No content (title only) - will skip")
    base.update({"text": "", "source_type": "no_content"})
    return base


def prepare_articles(news):
    candidates = news[:MAX_ARTICLES_TO_FETCH]
    log("FETCH", f"Downloading {len(candidates)} articles (workers={FETCH_WORKERS})...")
    results = []
    executor = ThreadPoolExecutor(max_workers=max(1, min(FETCH_WORKERS, len(candidates))))
    futures = {executor.submit(fetch_article, item): item for item in candidates}
    try:
        for future in as_completed(futures, timeout=PREPARE_TIMEOUT):
            item = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                warn("FETCH", f"Worker failed for '{item['title'][:60]}': {err_text(exc)}")
    except FuturesTimeout:
        warn("FETCH", f"Stage timeout ({PREPARE_TIMEOUT}s) - continuing with what we have")
    finally:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)

    results.sort(key=lambda x: (x.get("priority", 0), x.get("published_ts", 0)), reverse=True)
    full = sum(1 for r in results if r["source_type"] == "full_article")
    log("FETCH", f"Prepared {len(results)} (full articles: {full})")
    return results


def choose_materials(articles):
    materials = []
    for a in articles:
        if a["source_type"] == "no_content" or len(a.get("text", "")) < 40:
            skip_item(a, SKIP_TTL_NO_CONTENT)
            continue
        materials.append(a)
    materials = materials[:MAX_ARTICLES_FOR_GEMINI]
    for index, m in enumerate(materials, 1):
        m["id"] = index
        m["gemini_text"] = m["text"][:GEMINI_CHARS_PER_ARTICLE]
    return materials


# ============================================================
# PHOTOS (taken from the source article itself - NO image generation)
# Priority: og:image > twitter:image > JSON-LD image > main <img> of the article > RSS image
# ============================================================

IMAGE_BAD_URL_RE = re.compile(
    r"(favicon|apple-touch-icon|/icons?/|sprite|logo|avatar|gravatar|placeholder|spacer|1x1|"
    r"/pixel|tracking|/ads?/|adserver|doubleclick|/badges?/|emoji|/banners?/)", re.I)
IMAGE_BAD_HOSTS = ("google.com", "gstatic.com")            # Google News / Google generic pictures
IMAGE_BAD_EXT = (".svg", ".gif", ".ico", ".avif", ".bmp", ".tif", ".tiff")
IMAGE_NOISE_ANCESTOR_RE = re.compile(
    r"(related|recommend|comment|sidebar|widget|popup|modal|menu|footer|outbrain|taboola|"
    r"sponsor|advert|author|newsletter|subscribe|social|share)", re.I)


def _clean_image_url(raw, base_url=""):
    """Absolute http(s) URL of a plausible content image, or '' (logos/icons/ads/svg are rejected)."""
    if not raw or not isinstance(raw, str):
        return ""
    u = html.unescape(raw).strip()
    if not u or u.lower().startswith("data:"):
        return ""
    try:
        if base_url:
            u = urljoin(base_url, u)
        p = urlparse(u)
    except Exception:
        return ""
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    host = p.netloc.lower().split(":")[0]
    if host.endswith(IMAGE_BAD_HOSTS):
        return ""
    if p.path.lower().endswith(IMAGE_BAD_EXT):
        return ""
    if IMAGE_BAD_URL_RE.search(u):
        return ""
    return u[:2000]


def _srcset_best(srcset):
    best, best_w = "", -1
    for part in re.split(r",\s+(?=\S)", str(srcset or "")):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0
        if len(bits) > 1:
            m = re.match(r"(\d+)", bits[1])
            w = int(m.group(1)) if m else 0
        if w > best_w:
            best, best_w = bits[0], w
    return best


def _jsonld_image_urls(soup):
    objs, queue, i = [], [], 0
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            queue.append(json.loads(script.string or script.get_text()))
        except Exception:
            continue
    while i < len(queue):
        cur = queue[i]
        i += 1
        if isinstance(cur, list):
            queue.extend(cur)
        elif isinstance(cur, dict):
            objs.append(cur)
            if isinstance(cur.get("@graph"), list):
                queue.extend(cur["@graph"])

    def types_of(o):
        t = o.get("@type")
        return [str(x) for x in (t if isinstance(t, list) else [t])] if t else []

    by_id = {}
    for o in objs:
        oid = o.get("@id")
        if oid and any("ImageObject" in t for t in types_of(o)):
            by_id[oid] = o.get("url") or o.get("contentUrl") or ""

    def pull(val, depth=0):
        out = []
        if depth > 3:
            return out
        if isinstance(val, str):
            out.append(val)
        elif isinstance(val, list):
            for v in val:
                out.extend(pull(v, depth + 1))
        elif isinstance(val, dict):
            u = val.get("url") or val.get("contentUrl")
            if u:
                out.extend(pull(u, depth + 1))
            elif val.get("@id") in by_id:
                out.append(by_id[val["@id"]])
        return out

    urls = []
    for o in objs:
        if any(("Article" in t or "Posting" in t or "WebPage" in t) for t in types_of(o)) and o.get("image"):
            urls.extend(pull(o["image"]))
    return urls


def _img_tag_url(img):
    for attr in ("data-src", "data-lazy-src", "data-original", "data-orig-file", "data-full-url"):
        if img.get(attr):
            return str(img.get(attr))
    for attr in ("data-srcset", "srcset"):
        if img.get(attr):
            best = _srcset_best(img.get(attr))
            if best:
                return best
    return str(img.get("src") or "")


def _dom_image_urls(soup):
    """<img> inside the article container (first ones in document order), skipping icons/ads/avatars."""
    containers = []
    for selector in CONTAINER_SELECTORS:
        try:
            containers.extend(soup.select(selector)[:3])
        except Exception:
            continue
    urls = []
    for el in containers:
        for img in el.find_all("img"):
            def num(attr):
                digits = re.sub(r"\D", "", str(img.get(attr) or ""))
                return int(digits) if digits else 0
            if (num("width") and num("width") < 200) or (num("height") and num("height") < 100):
                continue
            noisy = False
            for depth, parent in enumerate(img.parents):
                if depth > 8:
                    break
                if parent.name in ("header", "footer", "nav", "aside"):
                    noisy = True
                    break
                try:
                    marker = " ".join(parent.get("class") or []) + " " + (parent.get("id") or "")
                except Exception:
                    continue
                if marker.strip() and IMAGE_NOISE_ANCESTOR_RE.search(marker):
                    noisy = True
                    break
            if noisy:
                continue
            u = _img_tag_url(img)
            if u:
                urls.append(u)
            if len(urls) >= 4:
                return urls
    return urls


def extract_image_candidates(soup, base_url):
    """Ordered [(url, kind)] of likely main images of the article. Never raises."""
    found, seen = [], set()

    def add(raw, kind):
        u = _clean_image_url(raw, base_url)
        if u and u not in seen:
            seen.add(u)
            found.append((u, kind))

    def metas(keys, kind):
        for attr in ("property", "name", "itemprop"):
            for key in keys:
                for tag in soup.find_all("meta", attrs={attr: key}):
                    add(tag.get("content"), kind)

    steps = (
        lambda: metas(("og:image", "og:image:url", "og:image:secure_url"), "og:image"),
        lambda: metas(("twitter:image", "twitter:image:src"), "twitter:image"),
        lambda: [add(u, "json-ld") for u in _jsonld_image_urls(soup)],
        lambda: [add(tag.get("href"), "image_src") for tag in soup.find_all("link", rel="image_src")],
        lambda: metas(("image", "thumbnailUrl"), "itemprop"),
        lambda: [add(u, "article-img") for u in _dom_image_urls(soup)],
    )
    for step in steps:
        try:
            step()
        except Exception as exc:
            warn("IMAGE", f"Image extraction step failed (ignored): {err_text(exc)}")
    return found[:MAX_IMAGE_CANDIDATES]


def find_article_images(base, soup, page_url):
    """Store image URLs found on the article page into the article dict (RSS image stays as last choice)."""
    if not IMAGES_ENABLED:
        return
    try:
        log("IMAGE SEARCH", base.get("title", "")[:90])
        found = extract_image_candidates(soup, page_url)
        if not found:
            log("IMAGE SEARCH", "No image on the article page")
            return
        urls = [u for u, _ in found]
        rest = [u for u in base.get("image_candidates", []) if u not in urls]
        base["image_candidates"] = (urls + rest)[:MAX_IMAGE_CANDIDATES]
        base["image_url"] = base["image_candidates"][0]
        log("IMAGE FOUND", f"{found[0][0]} ({found[0][1]}, {len(found)} candidate(s))")
    except Exception as exc:
        warn("IMAGE", f"Image search failed (ignored): {err_text(exc)}")


def image_info(data):
    """(format, width, height) from the file header; width/height may be 0 if unknown. None = unsupported."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            w, h = struct.unpack(">II", data[16:24])
            return "png", w, h
        if data[:3] == b"\xff\xd8\xff":
            i, n = 2, len(data)
            while i + 9 < n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker == 0xFF:
                    i += 1
                    continue
                if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if marker == 0xDA:
                    break
                seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return "jpeg", w, h
                i += 2 + seglen
            return "jpeg", 0, 0
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return "webp", w & 0x3FFF, h & 0x3FFF
            if kind == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return "webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if kind == b"VP8X":
                return "webp", int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
            return "webp", 0, 0
    except Exception:
        return None
    return None


_IMAGE_FORMATS = {"jpeg": ("image/jpeg", "jpg"), "png": ("image/png", "png"), "webp": ("image/webp", "webp")}


def download_image(url, referer=""):
    """Download + validate one image. Returns {'data','mime','ext','width','height','url'} or None. Never raises."""
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8,*/*;q=0.5",
        "Accept-Language": HEADERS["Accept-Language"],
    }
    if referer:
        headers["Referer"] = referer
    try:
        deadline = time.monotonic() + IMAGE_TOTAL_TIMEOUT
        with requests.get(url, headers=headers, timeout=IMAGE_TIMEOUT, stream=True, allow_redirects=True) as resp:
            if resp.status_code != 200:
                log("IMAGE DOWNLOAD FAILED", f"{url} (HTTP {resp.status_code})")
                return None
            ctype = (resp.headers.get("content-type") or "").lower()
            if ctype and not (ctype.startswith("image/") or "octet-stream" in ctype):
                log("IMAGE DOWNLOAD FAILED", f"{url} (not an image: {ctype[:40]})")
                return None
            try:
                declared = int(resp.headers.get("content-length") or 0)
            except ValueError:
                declared = 0
            if declared > MAX_IMAGE_BYTES:
                log("IMAGE DOWNLOAD FAILED", f"{url} (too large: {declared} bytes)")
                return None
            chunks, size = [], 0
            for chunk in resp.iter_content(chunk_size=32768):
                if not chunk:
                    continue
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    log("IMAGE DOWNLOAD FAILED", f"{url} (too large)")
                    return None
                if time.monotonic() > deadline:
                    log("IMAGE DOWNLOAD FAILED", f"{url} (download deadline exceeded)")
                    return None
        data = b"".join(chunks)
    except Exception as exc:
        log("IMAGE DOWNLOAD FAILED", f"{url} ({err_text(exc)})")
        return None

    if len(data) < MIN_IMAGE_BYTES:
        log("IMAGE DOWNLOAD FAILED", f"{url} (too small: {len(data)} bytes)")
        return None
    info = image_info(data)
    if not info:
        log("IMAGE DOWNLOAD FAILED", f"{url} (unsupported format)")
        return None
    fmt, w, h = info
    if w and h:
        if w < MIN_IMAGE_WIDTH or h < MIN_IMAGE_HEIGHT:
            log("IMAGE DOWNLOAD FAILED", f"{url} (too small: {w}x{h})")
            return None
        if w + h > 10000 or max(w, h) / float(min(w, h)) > 20:
            log("IMAGE DOWNLOAD FAILED", f"{url} (size not accepted by Telegram: {w}x{h})")
            return None
    mime, ext = _IMAGE_FORMATS[fmt]
    log("IMAGE DOWNLOAD OK", f"{url} ({len(data) // 1024} KB, {w}x{h}, {fmt})")
    return {"data": data, "mime": mime, "ext": ext, "width": w, "height": h, "url": url}


def find_post_image(post):
    """First image of the post's sources (main source first) that downloads and passes validation, else None."""
    if not IMAGES_ENABLED:
        return None
    attempts, tried = 0, set()
    for src in post.get("sources", []):
        referer = src.get("url") or ""
        for url in src.get("image_candidates") or []:
            if attempts >= MAX_IMAGE_ATTEMPTS:
                break
            if url in tried:
                continue
            tried.add(url)
            attempts += 1
            log("IMAGE URL", url)
            image = download_image(url, referer)
            if image:
                return image
    if attempts == 0:
        log("IMAGE SEARCH", "No image for this post - text only")
    else:
        log("IMAGE SEARCH", "No usable image - text only")
    return None


def _tg_len(text):
    """Length the way Telegram counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def build_caption(post):
    """(html_caption, plain_caption) that fits into Telegram's 1024-character caption limit.
    The text is the normal post; if it is too long, whole trailing sentences are dropped. None = does not fit."""
    sentences = list(post.get("sentences") or [])
    if not sentences:
        return None
    total = len(sentences)
    n = total
    while n >= min(MIN_SENTENCES, total):
        _, text_html, plain_footer = format_post(post["category"], post["title"], sentences[:n])
        if _tg_len(plain_footer) <= TELEGRAM_CAPTION_LIMIT:       # plain_footer is the longer variant
            if n < total:
                log("IMAGE", f"Caption shortened to {n} of {total} sentences (Telegram limit {TELEGRAM_CAPTION_LIMIT})")
            return text_html, plain_footer
        n -= 1
    return None


# ============================================================
# GEMINI
# ============================================================

CATEGORY_KEYS = list(CATEGORY_LABELS.keys())

POSTS_SCHEMA = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_ids": {"type": "array", "items": {"type": "integer"}},
                    "category": {"type": "string", "enum": CATEGORY_KEYS},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "topic": {"type": "string"},
                },
                "required": ["source_ids", "category", "title", "body", "topic"],
            },
        }
    },
    "required": ["posts"],
}

REPAIR_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
    "required": ["title", "body"],
}

JSON_MODES = ["legacy", "plain"]   # "responseFormat" is rejected by the live API (HTTP 400)
_gemini = {"cooldown_until": 0.0, "mode_idx": 0, "model_idx": 0}


def gemini_available():
    if not GEMINI_API_KEY:
        return False
    remaining = _gemini["cooldown_until"] - time.time()
    if remaining > 0:
        log("GEMINI", f"Cooldown active: {int(remaining)}s left")
        return False
    return True


def set_gemini_cooldown(seconds, reason):
    seconds = max(GEMINI_MIN_COOLDOWN, int(seconds))
    _gemini["cooldown_until"] = time.time() + seconds
    warn("GEMINI", f"Cooldown activated for {seconds // 60} min ({reason})")


def _cooldown_from_429(text):
    if re.search(r"PerDay|per day|daily", text or "", re.I):
        return GEMINI_DEFAULT_COOLDOWN
    m = re.search(r'retryDelay"?\s*:\s*"?(\d+(?:\.\d+)?)s', text or "")
    if m:
        return float(m.group(1)) + 15
    return GEMINI_DEFAULT_COOLDOWN


def _build_payload(prompt, schema, mode):
    gen = {"temperature": 0.3, "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS}
    if mode == "responseFormat":
        gen["responseFormat"] = {"text": {"mimeType": "application/json", "schema": schema}}
    elif mode == "legacy":
        gen["responseMimeType"] = "application/json"
        gen["responseJsonSchema"] = schema
    return {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}


def _extract_gemini_text(data):
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        warn("GEMINI", f"Prompt blocked: {block}")
    parts = []
    for cand in data.get("candidates", []) or []:
        finish = cand.get("finishReason")
        if finish and finish not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
            warn("GEMINI", f"finishReason={finish}")
        for part in (cand.get("content") or {}).get("parts", []) or []:
            if part.get("thought"):
                continue
            if part.get("text"):
                parts.append(part["text"])
        if parts:
            break
    return "\n".join(parts).strip()


def gemini_generate(prompt, schema, purpose):
    """Returns response text or None. Handles model fallback, JSON modes, retries, cooldown."""
    if not gemini_available():
        return None

    models = GEMINI_MODELS
    model_idx = min(_gemini["model_idx"], len(models) - 1)
    saw_429 = False
    last_429_text = ""

    while model_idx < len(models):
        model = models[model_idx]
        url = GEMINI_ENDPOINT.format(model=model)
        server_failures = 0

        while True:
            mode = JSON_MODES[_gemini["mode_idx"]]
            log("GEMINI", f"Request ({purpose}) model={model} mode={mode}, prompt {len(prompt)} chars")
            try:
                resp = requests.post(
                    url,
                    headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                    json=_build_payload(prompt, schema, mode),
                    timeout=GEMINI_TIMEOUT,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                warn("GEMINI", f"Network/timeout: {err_text(exc)}")
                if server_failures < GEMINI_RETRIES:
                    server_failures += 1
                    time.sleep(3 * server_failures)
                    continue
                error("GEMINI", "Giving up after network errors")
                return None
            except Exception as exc:
                error("GEMINI", f"Request failed: {err_text(exc)}")
                return None

            status = resp.status_code
            log("GEMINI", f"HTTP {status}")

            if status == 200:
                try:
                    data = resp.json()
                except ValueError:
                    error("GEMINI", "Response is not JSON")
                    return None
                text = _extract_gemini_text(data)
                if not text:
                    error("GEMINI", "Empty response text")
                    return None
                log("GEMINI", f"Response received ({len(text)} chars)")
                _gemini["model_idx"] = model_idx
                return text

            body_text = resp.text[:1500]

            if status == 429:
                error("GEMINI", "HTTP 429 (quota / rate limit)")
                saw_429 = True
                last_429_text = body_text
                break                                   # try the next model once

            if status == 404:
                warn("GEMINI", f"Model '{model}' not available (404)")
                model_idx += 1
                if model_idx >= len(models):
                    error("GEMINI", "No Gemini model available")
                    return None
                break

            if status in (401, 403):
                error("GEMINI", f"HTTP {status}: key rejected or no access. {body_text[:300]}")
                set_gemini_cooldown(GEMINI_DEFAULT_COOLDOWN, f"HTTP {status}")
                return None

            if status == 400:
                lowered = body_text.lower()
                if "api key" in lowered or "api_key" in lowered:
                    error("GEMINI", "HTTP 400: invalid API key")
                    set_gemini_cooldown(GEMINI_DEFAULT_COOLDOWN, "invalid key")
                    return None
                if _gemini["mode_idx"] < len(JSON_MODES) - 1:
                    warn("GEMINI", f"HTTP 400 in mode '{mode}': {body_text[:200]} -> switching JSON mode")
                    _gemini["mode_idx"] += 1
                    continue
                error("GEMINI", f"HTTP 400: {body_text[:400]}")
                return None

            if status >= 500:
                if server_failures < GEMINI_RETRIES:
                    server_failures += 1
                    warn("GEMINI", f"Server error {status}, retry in {3 * server_failures}s")
                    time.sleep(3 * server_failures)
                    continue
                error("GEMINI", f"Server error {status}, giving up")
                return None

            error("GEMINI", f"Unexpected HTTP {status}: {body_text[:300]}")
            return None

        if saw_429 and status == 429:
            model_idx += 1

    if saw_429:
        set_gemini_cooldown(_cooldown_from_429(last_429_text), "HTTP 429 on all models")
    return None


def build_prompt(materials, memory):
    published = []
    for e in memory[-60:]:
        label = e.get("post_title") or e.get("topic") or e.get("title")
        if label:
            published.append(f"- {label}")
    published_text = "\n".join(published) if published else "(пока ничего)"

    blocks = []
    for m in materials:
        if m["source_type"] == "full_article":
            kind = "ПОЛНАЯ СТАТЬЯ"
        else:
            kind = "ТОЛЬКО КРАТКИЙ RSS-АНОНС (деталей мало, НИЧЕГО не додумывай)"
        blocks.append("\n".join([
            "===== МАТЕРИАЛ =====",
            f"ARTICLE_ID: {m['id']}",
            f"ТИП ИСТОЧНИКА: {kind}",
            f"ЗАГОЛОВОК ИСТОЧНИКА: {m['title']}",
            "ТЕКСТ:",
            m["gemini_text"],
        ]))

    rules = [
        "Ты - главный редактор русскоязычного Telegram-канала LilsNews об EA SPORTS FC 27 (Ultimate Team).",
        "Ниже материалы с идентификаторами ARTICLE_ID. Почти все они на английском. "
        "Выбери из них реально новые и полезные новости и напиши по каждой готовый пост НА РУССКОМ ЯЗЫКЕ.",
        "",
        "ЯЗЫК (самое важное):",
        "- Поля title и body пиши ТОЛЬКО на русском языке, даже если источник английский.",
        "- Латиницей можно оставлять только имена собственные: игроков, клубов, промо, режимов, "
        "официальные термины (FC 27, EA Sports, Ultimate Team, SBC, OVR, TOTY, Evolution).",
        "- Все предложения и объяснения - по-русски. Нельзя копировать английский текст источника, "
        "нельзя возвращать английский заголовок или английское резюме.",
        "",
        "ФАКТЫ (критично):",
        "- Используй ТОЛЬКО факты из переданных материалов. Запрещено использовать свои знания о FC 27.",
        "- Не придумывай OVR, цены, даты, награды, SBC, игроков, рейтинги, характеристики, формации, "
        "тактики, механики, изменения патча, промо, даты релиза. Нет в источнике - не пиши.",
        "- Слух или утечка остаётся слухом: пиши осторожно ('по данным источника', 'сообщается', "
        "'по слухам'). Не превращай слух в подтверждённый факт.",
        "- Если материал помечен как краткий RSS-анонс, используй только то, что прямо в нём написано.",
        "",
        "СТРУКТУРА КАЖДОГО ПОСТА:",
        "- title: информативный заголовок на русском, который сообщает суть новости "
        "(например: 'В FC 27 появился новый SBC с редкой наградой'), а не 'EA FC 27 SBC'.",
        "- body: 2-5 содержательных предложений с конкретными фактами из источника. Каждое предложение "
        "несёт новую информацию. Ничего не повторяй: ни предложения, ни смысл, ни заголовок.",
        "- topic: короткое название события на русском (3-8 слов).",
        "- category: ровно одно из: " + ", ".join(CATEGORY_KEYS) + ". META - мета, тактики, формации; "
        "GAMEPLAY - геймплей; PATCH - патчи и обновления; SBC - SBC; PROMO - промо и события; "
        "RUMOR - слухи и утечки; PLAYERS - карточки и игроки; RATINGS - рейтинги; OBJECTIVE - цели; "
        "EVOLUTION - Evolution; NEWS - прочее.",
        "- source_ids: список ARTICLE_ID, на которых основан пост. Первым укажи главный материал.",
        "",
        "ПРАВИЛА ВЫБОРА:",
        f"- От 1 до {MAX_POSTS_PER_CYCLE} постов, но только если это РАЗНЫЕ события. Не раздувай количество.",
        "- Несколько материалов об одном событии объединяй в ОДИН пост (укажи все их ARTICLE_ID). "
        "Разные события (SBC, патч, рейтинги, промо, карточка игрока) - разные посты.",
        "- Не пиши про темы из списка 'УЖЕ ОПУБЛИКОВАНО'.",
        "- Пропускай материалы без фактов: реклама, 'читайте больше', 'click here', 'watch the latest', "
        "'check out', SEO-мусор, общие рассуждения.",
        "- Если ни из одного материала нельзя сделать полноценную новость - верни пустой массив posts.",
        "",
        "ЗАПРЕЩЕНО В ТЕКСТЕ ПОСТА:",
        "- Ссылки, названия сайтов-источников, слова 'согласно статье', 'в материале', 'источник сообщает', "
        "вступления ('Вот новости'), текст от первого лица, упоминания ИИ, служебные пометки "
        "(POST, TOPIC, SOURCE_ID, ARTICLE_ID), markdown и эмодзи (эмодзи, оформление и подпись канала добавляет бот).",
        "",
        "ФОРМАТ ОТВЕТА: только валидный JSON без пояснений и без markdown - объект с полем posts "
        "(массив объектов с полями source_ids, category, title, body, topic).",
        "",
        "УЖЕ ОПУБЛИКОВАНО:",
        published_text,
        "",
        "МАТЕРИАЛЫ:",
        "",
        "\n\n".join(blocks),
        "",
        "Ещё раз: пост ТОЛЬКО на русском, ТОЛЬКО факты из материалов, без повторов.",
    ]
    return "\n".join(rules)


def build_repair_prompt(draft, material, reason):
    return "\n".join([
        "Перепиши Telegram-пост об EA SPORTS FC 27 полностью НА РУССКОМ ЯЗЫКЕ.",
        f"Проблема черновика: {reason}.",
        "Правила: только факты из материала ниже, ничего не придумывай; латиницей оставляй только имена "
        "собственные и официальные термины; заголовок на русском и по сути новости; в body 2-5 разных "
        "предложений без повторов; без ссылок, названий сайтов, markdown и вступлений.",
        "Верни только JSON-объект с полями title и body.",
        "",
        "ЧЕРНОВИК (title): " + str(draft.get("title", "")),
        "ЧЕРНОВИК (body): " + str(draft.get("body", "")),
        "",
        "МАТЕРИАЛ:",
        material["title"],
        material["gemini_text"][:3500],
    ])


# ============================================================
# ROBUST JSON PARSING OF GEMINI OUTPUT
# ============================================================

def _strip_fences(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json|JSON)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def extract_json(text):
    """Return parsed JSON (dict/list) from messy model output, or None."""
    t = _strip_fences(text)
    if not t:
        return None
    candidates = [t, re.sub(r",\s*([}\]])", r"\1", t)]
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            pass
    decoder = json.JSONDecoder()
    for source in candidates:
        for m in re.finditer(r"[\[{]", source):
            try:
                obj, _ = decoder.raw_decode(source[m.start():])
            except Exception:
                continue
            if isinstance(obj, (dict, list)) and obj:
                return obj
    return None


def parse_gemini_posts(text):
    """Returns list of normalized dicts, [] for a legitimate 'no news', None if unparsable."""
    if text is None:
        return None
    stripped = _strip_fences(text)
    if stripped.upper() == "NO_NEWS":
        return []
    obj = extract_json(text)
    if obj is None:
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get("posts"), list):
            raw_items = obj["posts"]
        elif any(k in obj for k in ("body", "title", "source_ids", "source_id")):
            raw_items = [obj]
        else:
            return None
    elif isinstance(obj, list):
        raw_items = obj
    else:
        return None

    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        ids = raw.get("source_ids", raw.get("source_id", raw.get("article_ids", raw.get("article_id"))))
        if isinstance(ids, (int, str)):
            ids = [ids]
        clean_ids = []
        for x in ids or []:
            try:
                clean_ids.append(int(str(x).strip()))
            except ValueError:
                continue
        items.append({
            "source_ids": clean_ids,
            "category": normalize_category(raw.get("category", "")),
            "title": str(raw.get("title", "") or ""),
            "body": str(raw.get("body", raw.get("text", "")) or ""),
            "topic": str(raw.get("topic", "") or ""),
        })
    return items


def normalize_category(raw):
    key = re.sub(r"[^A-Za-zА-Яа-я ]+", " ", str(raw or "")).strip().upper()
    key = key.split(" ")[-1] if key and key not in CATEGORY_LABELS and key not in CATEGORY_ALIASES else key
    key = CATEGORY_ALIASES.get(key, key)
    return key if key in CATEGORY_LABELS else "NEWS"


# ============================================================
# POST FORMATTING (emojis, bold title, clickable channel footer)
# ============================================================

EMOJI_RULES = [
    (r"награ|пакет|набор|приз|\bpack", "🎁"),
    (r"дн(?:я|ей|и)\b|недел|срок|в течение|до \d|часов|\bчас\b", "⏳"),
    (r"монет|цен[аеуы]|стоимост|coins", "💰"),
    (r"рейтинг|\bovr\b|оценк", "📈"),
    (r"патч|обновлен|исправл|\bбаг|фикс|ошибк", "🛠"),
    (r"слух|утечк|по данным|сообща|предположительно|по информации", "👀"),
    (r"тактик|формаци|\bмет[аыу]\b", "🧠"),
    (r"геймплей|механик|управлен|анимаци", "🎮"),
    (r"цел[иь]|задани|objective|милстоун", "🎯"),
    (r"эволюц|evolution", "🔄"),
    (r"\bsbc\b|состав|сквад|требовани", "🃏"),
    (r"выйд|вышел|вышла|вышло|релиз|появил|стартов|запуст|доступн|добавил|ввел|ввели", "✅"),
    (r"режим карьеры|карьер|career", "🏆"),
    (r"promo|промо|событи", "🟣"),
    (r"игрок|карточк|защитник|нападающ|полузащитник|вратар", "⭐"),
]
DEFAULT_EMOJIS = ["📌", "🔹", "💬", "➡️"]


def pick_emoji(sentence, used):
    low = sentence.lower()
    for pattern, emoji in EMOJI_RULES:
        if emoji not in used and re.search(pattern, low):
            return emoji
    for emoji in DEFAULT_EMOJIS:
        if emoji not in used:
            return emoji
    return "🔹"


def format_post(category, title, sentences):
    """Returns (plain_text_for_memory, html_for_telegram, plain_with_footer_for_fallback)."""
    used, lines = set(), []
    for sentence in sentences:
        s = re.sub(r"^[^\w«\"(\[]+", "", sentence).strip()      # drop emojis/bullets the model may have added
        emoji = pick_emoji(s, used)
        used.add(emoji)
        lines.append(f"{emoji} {s}")
    lead, rest = lines[0], lines[1:]
    body = lead + ("\n\n" + "\n".join(rest) if rest else "")
    header = f"📰 LILSNEWS | {CATEGORY_LABELS[category]}"

    plain = f"{header}\n\n{title}\n\n{body}"
    esc = lambda x: html.escape(x, quote=False)
    html_text = (f"{esc(header)}\n\n<b>{esc(title)}</b>\n\n{esc(body)}"
                 f"\n\n<a href=\"{CHANNEL_URL}\">{esc(CHANNEL_FOOTER_TEXT)}</a>")
    plain_footer = f"{plain}\n\n{CHANNEL_FOOTER_TEXT}: {CHANNEL_URL}"
    return plain, html_text, plain_footer


# ============================================================
# POST VALIDATION / POST-PROCESSING
# ============================================================

SERVICE_PATTERNS = [
    r"===\s*POST", r"\bPOST\s*\d+\b", r"\bTOPIC\s*:", r"SOURCE[_ ]?IDS?\s*:", r"ARTICLE[_ ]?ID",
    r"```", r"^\s*[\[{]", r'"\s*(?:title|body|topic|source_ids?|category)\s*"\s*:', r"\bNO_NEWS\b",
    r"\bas an ai\b", r"language model", r"языков\w+ модел", r"\bкак (?:ии|ай|искусственный)",
    r"\bвот (?:новост|пост|ваш)", r"^\s*конечно[,!]", r"согласно (?:статье|материал|источнику)",
    r"\bв (?:данной |этой |исходной )?(?:статье|материале)\b", r"по данным статьи",
    r"\b(?:ARTICLE|SOURCE)\b\s*\d",
]
SERVICE_RE = re.compile("|".join(SERVICE_PATTERNS), re.I | re.M)

JUNK_BODY_RE = re.compile(
    r"(читайте (?:больше|далее|также)|подписывайтесь|переходите по ссылке|нажмите (?:здесь|на ссылку)|"
    r"read more|click here|watch the latest|check out)", re.I)


def clean_post_text(text):
    t = str(text or "").replace("\r", "")
    t = re.sub(r"```(?:json|text|markdown)?", "", t)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"(?m)^\s*#{1,6}\s*", "", t)
    t = re.sub(r"(?mi)^\W*(?:📰\s*)?LILSNEWS\s*\|[^\n]*\n?", "", t)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n")]
    t = "\n".join(lines)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def strip_urls(text):
    t = re.sub(r"https?://\S+|www\.\S+", "", text)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def dedupe_sentences(sentences, reference_title=""):
    """Remove exact and near-duplicate sentences. Returns (unique_list, removed_count)."""
    unique, keys, token_sets = [], [], []
    removed = 0
    ref_norm = normalize_title(reference_title)
    ref_tokens = content_tokens(reference_title)
    for sentence in sentences:
        norm = normalize_title(sentence)
        if not norm:
            continue
        tokens = content_tokens(sentence)
        dup = False
        if ref_norm and unique == []:
            # first body sentence repeating the title is redundant
            if difflib.SequenceMatcher(None, norm, ref_norm).ratio() >= 0.85 or \
                    (len(tokens) >= 4 and jaccard(tokens, ref_tokens) >= 0.8):
                dup = True
        if not dup:
            for k, ts in zip(keys, token_sets):
                if norm == k or difflib.SequenceMatcher(None, norm, k).ratio() >= 0.86:
                    dup = True
                    break
                if len(tokens) >= 4 and len(ts) >= 4 and jaccard(tokens, ts) >= 0.8:
                    dup = True
                    break
        if dup:
            removed += 1
            continue
        unique.append(sentence)
        keys.append(norm)
        token_sets.append(tokens)
    return unique, removed


def numbers_in(text):
    return {re.sub(r"\D", "", n) for n in re.findall(r"\d+(?:[.,]\d+)*", str(text or ""))}


def numbers_supported(title, body, sources):
    blob = " ".join(s["title"] + " " + s.get("gemini_text", s.get("text", "")) for s in sources)
    allowed = numbers_in(blob)
    ignore = {"27", "26", "25"}
    bad = sorted(n for n in numbers_in(title + " " + body)
                 if len(n) >= 2 and n not in ignore and n not in allowed)
    return bad


def validate_item(item, materials_by_id):
    """Validate + post-process one Gemini item.
    Returns (post_dict | None, reason, detail). Reasons in REPAIRABLE can be retried via rewrite."""
    sources, seen_ids = [], set()
    for sid in item.get("source_ids", []):
        if sid in materials_by_id and sid not in seen_ids:
            seen_ids.add(sid)
            sources.append(materials_by_id[sid])
    if not sources:
        return None, "no_source", "source_ids do not match any material"

    raw_title, raw_body = item.get("title", ""), item.get("body", "")
    if SERVICE_RE.search(raw_title) or SERVICE_RE.search(raw_body):
        return None, "service_text", "service/meta text inside post"

    title = clean_post_text(raw_title).replace("\n", " ")
    title = strip_urls(title).strip(' "«»“”\'')
    body = strip_urls(clean_post_text(raw_body))

    if not title:
        return None, "no_title", ""
    if not body:
        return None, "no_body", ""
    if JUNK_BODY_RE.search(body):
        return None, "junk", "call-to-action / SEO text"

    # --- paragraphs -> sentences -> remove repeats ---
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    flat_sentences = []
    for p in paragraphs:
        flat_sentences.extend(split_sentences(p))
    total_before = len(flat_sentences)
    unique, removed = dedupe_sentences(flat_sentences, title)
    if removed:
        log("VALIDATE", f"Removed {removed} repeated sentence(s)")
    unique = [s for s in unique if len(s) >= MIN_SENTENCE_LENGTH]
    if len(unique) < MIN_SENTENCES:
        reason = "repetition" if total_before >= MIN_SENTENCES and removed else "few_sentences"
        return None, reason, f"{len(unique)} usable sentence(s)"

    # --- trim by whole sentences ---
    header_len = len(title) + 40
    while len(unique) > MIN_SENTENCES and header_len + sum(len(s) + 1 for s in unique) > MAX_POST_LENGTH:
        unique.pop()
    if len(unique) >= 3:
        body_final = unique[0] + "\n\n" + " ".join(unique[1:])
    else:
        body_final = " ".join(unique)

    # --- language first (so the log and the rewrite prompt name the real problem) ---
    if not is_russian(body_final, 0.55, 25):
        return None, "not_russian", "body is not mostly Russian"
    if not is_russian(title, 0.4, 6):
        return None, "not_russian", "title is not Russian"

    if len(body_final) < MIN_BODY_LENGTH:
        return None, "too_short", f"{len(body_final)} chars"
    if header_len + len(body_final) > 4000:
        return None, "too_long", ""

    # --- invented numbers ---
    if STRICT_NUMBER_CHECK:
        bad = numbers_supported(title, body_final, sources)
        if bad:
            return None, "unsupported_numbers", "not in source: " + ", ".join(bad[:5])

    category = normalize_category(item.get("category", ""))
    if SERVICE_RE.search(title + "\n" + body_final):
        return None, "service_text", "service text after processing"

    text, text_html, text_plain_footer = format_post(category, title, unique)

    topic = clean_text(item.get("topic", "")) or title
    return {
        "text": text, "text_html": text_html, "text_plain_footer": text_plain_footer,
        "title": title, "body": body_final, "category": category,
        "topic": topic[:150], "sources": sources,
        "sentences": list(unique),
    }, "ok", ""


REPAIRABLE = {"not_russian", "too_short", "few_sentences", "repetition"}


# ============================================================
# DUPLICATE VALIDATION (memory + current cycle)
# ============================================================

def duplicate_reason(post, memory, current_posts):
    mem_urls = memory_url_set(memory)
    for s in post["sources"]:
        for u in [s.get("url", ""), s.get("rss_url", "")] + list(s.get("alt_links", [])):
            if u and canonical_url(u) in mem_urls:
                return "source URL already published"
    mem_titles = memory_title_list(memory)
    for s in post["sources"]:
        for t in [s.get("title", "")] + list(s.get("alt_titles", [])):
            if t and any(similar_titles(t, old, 0.85) for old in mem_titles):
                return "source title already published"

    for e in memory[-150:]:
        for old in (e.get("post_title"), e.get("topic")):
            if old and (similar_titles(post["title"], old, 0.8) or similar_titles(post["topic"], old, 0.8)):
                return "same title/topic as published post"

    new_tokens = content_tokens(post["body"])
    if len(new_tokens) >= 8:
        for e in memory[-150:]:
            old_tokens = content_tokens(e.get("post", ""))
            if len(old_tokens) >= 8 and jaccard(new_tokens, old_tokens) >= 0.65:
                return "text too similar to a published post"

    for cur in current_posts:
        if any(canonical_url(a.get("url", "")) == canonical_url(b.get("url", ""))
               for a in post["sources"] for b in cur["sources"]):
            return "same source as another post in this cycle"
        if similar_titles(post["title"], cur["title"], 0.7) or similar_titles(post["topic"], cur["topic"], 0.7):
            return "same event as another post in this cycle"
        cur_tokens = content_tokens(cur["body"])
        if len(new_tokens) >= 8 and len(cur_tokens) >= 8 and jaccard(new_tokens, cur_tokens) >= 0.6:
            return "text too similar to another post in this cycle"
    return ""


# ============================================================
# PUBLISHING
# ============================================================

def publish_post(post, memory, current_posts):
    """Send to Telegram. Memory is updated ONLY after a confirmed successful send.
    With a photo: ONE message (photo + caption). Any photo problem -> plain text post as before."""
    log("PUBLISH", f"Sending: {post['title'][:90]}")

    sent = False
    image = None
    try:
        image = find_post_image(post)
    except Exception as exc:                       # an image problem must never stop publishing
        warn("IMAGE", f"Image search crashed (ignored): {err_text(exc)}")

    if image:
        caption = build_caption(post)
        if caption:
            try:
                send_telegram_photo(image, caption[0], parse_mode="HTML", plain_fallback=caption[1])
                log("TELEGRAM PHOTO SENT", post["title"][:90])
                sent = True
            except requests.ReadTimeout as exc:
                # The photo may already be delivered - a text fallback could create a duplicate.
                error("PUBLISH", f"Telegram photo timed out, will retry next cycle: {err_text(exc)}")
                return False
            except Exception as exc:
                warn("TELEGRAM", f"Telegram photo failed: {err_text(exc)}")
                log("TELEGRAM TEXT FALLBACK", post["title"][:90])
        else:
            log("TELEGRAM TEXT FALLBACK", "caption does not fit into 1024 characters")

    if not sent:
        try:
            send_telegram(post["text_html"], parse_mode="HTML", plain_fallback=post["text_plain_footer"])
        except Exception as exc:
            error("PUBLISH", f"Telegram send failed: {err_text(exc)}")
            return False
    log("PUBLISH", "Success")
    remember_publication(memory, post)
    save_memory(memory)
    current_posts.append(post)
    time.sleep(1.5)
    return True


def process_gemini_items(items, materials, memory):
    materials_by_id = {m["id"]: m for m in materials}
    current_posts, repairs_left = [], REPAIR_LIMIT_PER_CYCLE
    published = 0
    used_ids = set()

    log("PARSE", f"{len(items)} post(s) generated")
    for index, item in enumerate(items, 1):
        if published >= MAX_POSTS_PER_CYCLE:
            log("PUBLISH", f"Cycle limit reached ({MAX_POSTS_PER_CYCLE})")
            break
        used_ids.update(item.get("source_ids", []))

        post, reason, detail = validate_item(item, materials_by_id)

        if post is None and reason in REPAIRABLE and repairs_left > 0 and gemini_available():
            primary = next((materials_by_id[s] for s in item.get("source_ids", []) if s in materials_by_id), None)
            if primary:
                repairs_left -= 1
                warn("VALIDATE", f"Post {index} rejected ({reason}: {detail}) - asking Gemini to rewrite")
                text = gemini_generate(build_repair_prompt(item, primary, f"{reason} {detail}"),
                                       REPAIR_SCHEMA, "rewrite")
                obj = extract_json(text) if text else None
                if isinstance(obj, dict) and obj.get("body"):
                    fixed = dict(item)
                    fixed["title"] = str(obj.get("title") or item.get("title", ""))
                    fixed["body"] = str(obj.get("body", ""))
                    post, reason, detail = validate_item(fixed, materials_by_id)

        if post is None:
            warn("VALIDATE", f"Post {index} rejected: {reason} {detail}")
            for sid in item.get("source_ids", []):
                if sid in materials_by_id:
                    skip_item(materials_by_id[sid], SKIP_TTL_REVIEWED)
            continue
        log("VALIDATE", f"Post {index} OK ({post['category']})")

        why = duplicate_reason(post, memory, current_posts)
        if why:
            warn("VALIDATE", f"Post {index} is a duplicate: {why}")
            for s in post["sources"]:
                skip_item(s, SKIP_TTL_REVIEWED)
            continue

        if publish_post(post, memory, current_posts):
            published += 1
        # on Telegram failure: nothing is remembered and nothing is skipped -> retried next cycle

    for m in materials:
        if m["id"] not in used_ids:
            skip_item(m, SKIP_TTL_REVIEWED)
    return published


# ============================================================
# FALLBACK (no Gemini): extractive RU post via machine translation
# ============================================================

def category_for_title(title):
    low = title.lower()
    rules = [
        (r"\bsbc\b", "SBC"), (r"\b(patch|update|title update)\b", "PATCH"),
        (r"\b(meta|tactics?|formations?)\b", "META"), (r"\bgameplay\b", "GAMEPLAY"),
        (r"\b(leak|leaked|leaks|rumou?rs?)\b", "RUMOR"), (r"\bpromo\b", "PROMO"),
        (r"\bratings?\b", "RATINGS"), (r"\bobjectives?\b", "OBJECTIVE"),
        (r"\bevolutions?\b", "EVOLUTION"), (r"\b(players?|cards?)\b", "PLAYERS"),
    ]
    for pattern, key in rules:
        if re.search(pattern, low):
            return key
    return "NEWS"


def translate_to_russian(text):
    """Unofficial free Google Translate endpoint. Returns '' on any failure."""
    text = normalize_whitespace(text)
    if not text:
        return ""
    if is_russian(text, 0.6, 15):
        return text
    chunks, current = [], ""
    for sentence in split_sentences(text):
        if len(current) + len(sentence) > 1200 and current:
            chunks.append(current)
            current = ""
        current = (current + " " + sentence).strip()
    if current:
        chunks.append(current)
    out = []
    for chunk in chunks:
        try:
            resp = requests.post(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "ru", "dt": "t"},
                data={"q": chunk}, headers=HEADERS, timeout=(5, 15),
            )
            resp.raise_for_status()
            data = resp.json()
            out.append("".join(seg[0] for seg in data[0] if seg and seg[0]))
        except Exception as exc:
            warn("FALLBACK", f"Translation failed: {err_text(exc)}")
            return ""
    return normalize_whitespace(" ".join(out))


FACT_HINT = re.compile(
    r"(\d|\bsbc\b|patch|update|rating|promo|player|card|gameplay|evolution|objective|release|leak|"
    r"added|adds|introduc|available|reward|squad|pack|tactic|meta|season)", re.I)


def select_fallback_sentences(material):
    sentences = []
    for s in split_sentences(material.get("text", "")):
        if not (40 <= len(s) <= 320):
            continue
        if BOILERPLATE.search(s) or "http" in s.lower():
            continue
        if not FACT_HINT.search(s):
            continue
        sentences.append(s)
    unique, _ = dedupe_sentences(sentences, material.get("title", ""))
    chosen, total = [], 0
    for s in unique:
        if len(chosen) >= FALLBACK_MAX_SENTENCES or total + len(s) > FALLBACK_MAX_BODY_CHARS:
            break
        chosen.append(s)
        total += len(s)
    return chosen


def make_fallback_item(material):
    if not FALLBACK_TRANSLATE:
        return None
    sentences = select_fallback_sentences(material)
    if len(sentences) < MIN_SENTENCES:
        log("FALLBACK", f"Not enough facts, skipping: {material['title'][:70]}")
        return None
    title_ru = translate_to_russian(material["title"])
    body_ru = translate_to_russian(" ".join(sentences))
    if not title_ru or not body_ru:
        return None
    return {
        "source_ids": [material["id"]],
        "category": category_for_title(material["title"]),
        "title": title_ru,
        "body": body_ru,
        "topic": title_ru,
    }


def publish_fallback(materials, memory):
    warn("FALLBACK", "Publishing RSS/article-based posts")
    materials_by_id = {m["id"]: m for m in materials}
    current_posts, published = [], 0
    for m in materials:
        if published >= MAX_POSTS_PER_CYCLE:
            break
        item = make_fallback_item(m)
        if not item:
            skip_item(m, SKIP_TTL_REVIEWED)
            continue
        post, reason, detail = validate_item(item, materials_by_id)
        if post is None:
            warn("VALIDATE", f"Fallback post rejected: {reason} {detail}")
            skip_item(m, SKIP_TTL_REVIEWED)
            continue
        why = duplicate_reason(post, memory, current_posts)
        if why:
            warn("VALIDATE", f"Fallback post is a duplicate: {why}")
            skip_item(m, SKIP_TTL_REVIEWED)
            continue
        if publish_post(post, memory, current_posts):
            published += 1
    return published


# ============================================================
# MAIN CYCLE
# ============================================================

def run_cycle(memory):
    banner("NEWS CHECK STARTED")

    raw_news = get_news()
    if not raw_news:
        log("RSS", "No FC 27 news found")
        return memory

    fresh = select_fresh_news(raw_news, memory)
    if not fresh:
        log("FILTER", "No fresh news")
        return memory
    for i, item in enumerate(fresh, 1):
        log("FILTER", f"{i}. [{item['priority']}] {item['title'][:100]}")

    articles = prepare_articles(fresh)
    materials = choose_materials(articles)
    if not materials:
        log("FETCH", "No usable material this cycle")
        return memory

    items = None
    if gemini_available():
        log("GEMINI", f"Sending {len(materials)} materials")
        text = gemini_generate(build_prompt(materials, memory), POSTS_SCHEMA, "posts")
        if text is not None:
            items = parse_gemini_posts(text)
            if items is None:
                error("PARSE", "Could not parse Gemini response as JSON")
                log("PARSE", "Raw start: " + text[:300].replace("\n", " "))
    else:
        log("GEMINI", "Not available (no key or cooldown)")

    if items is None:
        published = publish_fallback(materials, memory)
        log("PUBLISH", f"Fallback published: {published}")
    elif not items:
        log("GEMINI", "No publishable news in these materials")
        for m in materials:
            skip_item(m, SKIP_TTL_REVIEWED)
        published = 0
    else:
        published = process_gemini_items(items, materials, memory)

    log("PUBLISH", f"Published this cycle: {published}")
    log("MEMORY", f"Items in memory: {len(memory)}")
    return memory


# ============================================================
# STARTUP
# ============================================================

def validate_config():
    log("CONFIG", "Checking environment variables...")
    missing = [name for name, value in (("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
                                        ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)) if not value]
    if missing:
        for name in missing:
            error("CONFIG", f"Missing environment variable: {name}")
        return False
    log("CONFIG", "TELEGRAM_TOKEN: set")
    log("CONFIG", "TELEGRAM_CHAT_ID: set")
    if GEMINI_API_KEY:
        log("CONFIG", "GEMINI_API_KEY: set")
    else:
        warn("CONFIG", "GEMINI_API_KEY is not set - fallback mode only")
    return True


def main():
    banner("LILSNEWS STARTED")
    log("START", f"Python {sys.version.split()[0]}, PID {os.getpid()}")
    log("START", f"Interval: {CHECK_INTERVAL // 60} min, max posts per cycle: {MAX_POSTS_PER_CYCLE}")
    log("START", f"Gemini models: {', '.join(GEMINI_MODELS)}")

    if not validate_config():
        error("CONFIG", "Fix the configuration first. Bot stopped.")
        sys.exit(1)

    memory = load_memory()
    log("MEMORY", f"Loaded: {len(memory)} published items")

    if not test_telegram():
        error("TELEGRAM", "Connection failed. Bot stopped.")
        sys.exit(1)

    log("START", "Initialization complete. Starting first news check...")

    while True:
        try:
            faulthandler.dump_traceback_later(CYCLE_WATCHDOG_SECONDS, repeat=False)
        except Exception:
            pass
        try:
            memory = run_cycle(memory)
        except KeyboardInterrupt:
            log("START", "Stopped by user")
            break
        except Exception as exc:
            error("CYCLE", f"Unexpected error: {err_text(exc)}")
        finally:
            try:
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass

        log("SLEEP", f"Waiting {CHECK_INTERVAL // 60} minutes...")
        try:
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            log("START", "Stopped by user")
            break


if __name__ == "__main__":
    main()
