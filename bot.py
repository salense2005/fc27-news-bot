import os
import json
import time
import re
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup


# ============================================================
# LILSNEWS — EA SPORTS FC 27 TELEGRAM NEWS BOT
#
# STABLE VERSION
#
# ВАЖНО:
# Secrets в GitHub Actions должны называться:
#
# TELEGRAM_TOKEN
# TELEGRAM_CHAT_ID
# GEMINI_API_KEY
#
# НИЧЕГО В КОДЕ ВПИСЫВАТЬ НЕ НУЖНО.
# ============================================================


# ============================================================
# STARTUP DEBUG
# ============================================================

print("================================", flush=True)
print("LILSNEWS: BOT.PY STARTING", flush=True)
print("Python process started successfully.", flush=True)
print("================================", flush=True)


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()


# ============================================================
# MAIN SETTINGS
# ============================================================

MEMORY_FILE = "published_news.json"

# Проверка каждые 5 минут.
CHECK_INTERVAL = 5 * 60

# RSS.
MAX_RSS_ITEMS_PER_QUERY = 10

# Максимум кандидатов после фильтрации.
MAX_CANDIDATES = 30

# Сколько статей скачивать.
MAX_ARTICLES_TO_FETCH = 18

# Максимум постов за один цикл.
MAX_POSTS_PER_CYCLE = 5

# Максимум текста статьи для Gemini.
MAX_ARTICLE_CHARS = 9000

# Таймаут статьи.
ARTICLE_TIMEOUT = 10

# Таймаут Google News redirect.
GOOGLE_REDIRECT_TIMEOUT = 15

# Gemini.
GEMINI_MODEL = "gemini-3.8-flash"

# Повторные попытки.
GEMINI_RETRIES = 2

# После 429 Gemini ждём час.
GEMINI_COOLDOWN = 60 * 60

# Сколько записей хранить.
MAX_MEMORY_ITEMS = 500

# Минимальная длина RSS summary.
MIN_RSS_SUMMARY_LENGTH = 30

# Минимальная длина Telegram поста.
MIN_POST_LENGTH = 60

# Максимальная длина Telegram поста.
MAX_POST_LENGTH = 1500


# ============================================================
# GLOBAL STATE
# ============================================================

gemini_cooldown_until = 0


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
}


# ============================================================
# SEARCH QUERIES
# ============================================================

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


# ============================================================
# UTILS
# ============================================================

def log(message=""):
    """
    GitHub Actions-friendly print.
    flush=True гарантирует моментальный вывод.
    """
    print(message, flush=True)


def now_timestamp():
    return int(time.time())


def normalize_whitespace(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_text(text):
    if not text:
        return ""

    text = html.unescape(str(text))

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<noscript.*?</noscript>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(r"<[^>]+>", " ", text)

    return normalize_whitespace(text)


def normalize_title(title):
    title = clean_text(title).lower()

    title = re.sub(
        r"\b(fc\s*27|ea\s*sports|ea)\b",
        " ",
        title,
        flags=re.I
    )

    title = re.sub(
        r"[^a-zа-яё0-9 ]+",
        " ",
        title,
        flags=re.I
    )

    title = re.sub(r"\s+", " ", title).strip()

    return title


def title_words(title):
    return {
        word
        for word in normalize_title(title).split()
        if len(word) >= 3
    }


def title_similarity(a, b):
    words_a = title_words(a)
    words_b = title_words(b)

    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b

    return len(intersection) / max(
        len(words_a),
        len(words_b)
    )


# ============================================================
# FC 27 FILTER
# ============================================================

def is_fc27_title(title):
    low = title.lower()

    forbidden = [
        "fc 26",
        "fc26",
        "fifa 26",
        "fc 25",
        "fc25",
        "fifa 25",
    ]

    if any(word in low for word in forbidden):
        return False

    return (
        "fc 27" in low
        or "fc27" in low
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method):
    return (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/{method}"
    )


def test_telegram():
    log("--------------------------------")
    log("TESTING TELEGRAM CONNECTION...")

    try:
        response = requests.get(
            telegram_url("getMe"),
            timeout=15
        )

        log(
            f"Telegram HTTP status: "
            f"{response.status_code}"
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {data}"
            )

        bot_name = (
            data.get("result", {})
            .get("username", "unknown")
        )

        log(
            f"Telegram connection OK: @{bot_name}"
        )

        return True

    except Exception as error:
        log(
            f"Telegram connection FAILED: "
            f"{repr(error)}"
        )

        return False


def send_telegram(message):
    if not message:
        return False

    response = requests.post(
        telegram_url("sendMessage"),
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {data}"
        )

    return True


# ============================================================
# MEMORY
# ============================================================

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        log("Memory file does not exist. Starting empty.")
        return []

    try:
        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

            if isinstance(data, list):
                return data

    except Exception as error:
        log(
            f"Memory read error: {repr(error)}"
        )

    return []


def save_memory(memory):
    try:
        memory = memory[-MAX_MEMORY_ITEMS:]

        temp_file = MEMORY_FILE + ".tmp"

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                memory,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_file,
            MEMORY_FILE
        )

    except Exception as error:
        log(
            f"Memory save error: {repr(error)}"
        )


def memory_has_source(memory, url):
    if not url:
        return False

    url = url.strip().lower()

    for item in memory:

        old_url = (
            item.get("source_url", "")
            or item.get("link", "")
        ).strip().lower()

        if old_url and old_url == url:
            return True

    return False


def memory_has_title(memory, title):
    normalized = normalize_title(title)

    if not normalized:
        return False

    for item in memory:

        old_title = normalize_title(
            item.get("title", "")
        )

        if not old_title:
            continue

        if old_title == normalized:
            return True

        similarity = title_similarity(
            title,
            old_title
        )

        if similarity >= 0.85:
            return True

    return False


def memory_has_topic(memory, topic):
    topic = normalize_title(topic)

    if not topic:
        return False

    for item in memory:

        old_topic = normalize_title(
            item.get("topic", "")
        )

        if not old_topic:
            continue

        if old_topic == topic:
            return True

        if title_similarity(
            topic,
            old_topic
        ) >= 0.85:
            return True

    return False


def remember_publication(
    memory,
    article,
    topic,
    post
):
    memory.append({
        "topic": topic,
        "title": article.get("title", ""),
        "source_url": article.get("url", ""),
        "post": post,
        "timestamp": now_timestamp(),
    })

    return memory


# ============================================================
# GOOGLE NEWS RSS
# ============================================================

def make_rss_url(query):
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def get_news():
    news = []

    seen_links = set()

    log("--------------------------------")
    log("STARTING GOOGLE NEWS RSS SEARCH...")

    for query in SEARCH_QUERIES:

        log(f"RSS SEARCH: {query}")

        try:
            rss_url = make_rss_url(query)

            response = requests.get(
                rss_url,
                headers=HEADERS,
                timeout=10,
            )

            response.raise_for_status()

            feed = feedparser.parse(
                response.content
            )

            entries_count = 0

            for entry in feed.entries[
                :MAX_RSS_ITEMS_PER_QUERY
            ]:

                title = clean_text(
                    entry.get("title", "")
                )

                link = (
                    entry.get("link", "")
                    or ""
                ).strip()

                summary = clean_text(
                    entry.get("summary", "")
                    or entry.get("description", "")
                )

                published = (
                    entry.get("published", "")
                    or entry.get("updated", "")
                )

                if not title or not link:
                    continue

                if not is_fc27_title(title):
                    continue

                normalized_link = link.lower()

                if normalized_link in seen_links:
                    continue

                seen_links.add(
                    normalized_link
                )

                news.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": published,
                })

                entries_count += 1

            log(
                f"  -> accepted: {entries_count}"
            )

        except Exception as error:
            log(
                f"RSS error '{query}': "
                f"{repr(error)}"
            )

    log(
        f"TOTAL RSS NEWS FOUND: {len(news)}"
    )

    return news


# ============================================================
# PRIORITY
# ============================================================

def calculate_priority(title):
    low = title.lower()

    points = {
        "sbc": 180,
        "new sbc": 80,

        "meta": 150,

        "tactic": 130,
        "tactics": 130,

        "gameplay": 125,

        "patch": 125,
        "update": 90,

        "player": 80,
        "players": 80,

        "card": 110,
        "cards": 110,

        "rating": 85,
        "ratings": 85,

        "promo": 100,

        "team 2": 90,
        "team 1": 90,

        "leak": 65,
        "leaked": 65,

        "objective": 90,

        "evolution": 90,

        "upgrade": 70,

        "ultimate team": 40,

        "pro player": 120,
        "pro players": 120,
    }

    score = 0

    for key, value in points.items():

        if key in low:
            score += value

    return score


# ============================================================
# DEDUPLICATION
# ============================================================

def deduplicate_news(news):
    result = []

    for item in news:

        duplicate = False

        for existing in result:

            if title_similarity(
                item["title"],
                existing["title"]
            ) >= 0.72:

                duplicate = True

                if len(
                    item.get("summary", "")
                ) > len(
                    existing.get("summary", "")
                ):

                    existing["summary"] = item[
                        "summary"
                    ]

                break

        if not duplicate:
            result.append(item)

    return result


def select_fresh_news(news, memory):
    fresh = []

    for item in news:

        if memory_has_source(
            memory,
            item["link"]
        ):
            continue

        if memory_has_title(
            memory,
            item["title"]
        ):
            continue

        item["priority"] = calculate_priority(
            item["title"]
        )

        fresh.append(item)

    fresh = deduplicate_news(
        fresh
    )

    fresh.sort(
        key=lambda x: (
            x.get("priority", 0),
            x.get("published", "")
        ),
        reverse=True
    )

    return fresh[:MAX_CANDIDATES]


# ============================================================
# GOOGLE NEWS REDIRECT
# ============================================================

def decode_google_news_url(url):
    if not url:
        return url

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=GOOGLE_REDIRECT_TIMEOUT,
            allow_redirects=True,
        )

        final_url = (
            response.url
            or ""
        )

        if (
            final_url
            and "news.google.com/rss/articles/"
            not in final_url
        ):
            return final_url

    except Exception as error:
        log(
            f"Google redirect error: "
            f"{repr(error)}"
        )

    return url


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def extract_article_text(soup):

    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

    bodies = []

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        try:

            raw = (
                script.string
                or script.get_text()
            )

            data = json.loads(raw)

            objects = []

            if isinstance(data, list):
                objects = data

            elif isinstance(data, dict):

                graph = data.get(
                    "@graph"
                )

                if isinstance(
                    graph,
                    list
                ):
                    objects = graph
                else:
                    objects = [data]

            for obj in objects:

                if not isinstance(
                    obj,
                    dict
                ):
                    continue

                body = obj.get(
                    "articleBody"
                )

                if not body:
                    continue

                body = clean_text(body)

                if len(body) >= 300:
                    bodies.append(body)

        except Exception:
            pass

    if bodies:
        return max(
            bodies,
            key=len
        )


    # --------------------------------------------------------
    # ARTICLE CONTAINERS
    # --------------------------------------------------------

    selectors = [
        "article",
        "[itemprop='articleBody']",
        ".article-body",
        ".article-content",
        ".article__body",
        ".article__content",
        ".post-content",
        ".post__content",
        ".entry-content",
        ".story-body",
        ".story-content",
        ".articleBody",
        ".articleText",
        ".article-text",
        ".content-body",
        "main",
    ]

    candidates = []

    for selector in selectors:

        try:
            elements = soup.select(
                selector
            )

        except Exception:
            continue

        for element in elements:

            copy = BeautifulSoup(
                str(element),
                "html.parser"
            )

            for bad in copy.select(
                "script,style,noscript,"
                "nav,footer,header,"
                ".advertisement,.ads,"
                ".social,.comments,.comment"
            ):
                bad.decompose()

            text = clean_text(
                copy.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 300:
                candidates.append(text)

    if candidates:
        return max(
            candidates,
            key=len
        )


    # --------------------------------------------------------
    # PARAGRAPHS
    # --------------------------------------------------------

    paragraphs = []

    for p in soup.find_all("p"):

        text = clean_text(
            p.get_text(
                " ",
                strip=True
            )
        )

        if len(text) >= 40:
            paragraphs.append(text)

    if paragraphs:

        text = "\n".join(
            paragraphs
        )

        if len(text) >= 300:
            return text


    # --------------------------------------------------------
    # META DESCRIPTION
    # --------------------------------------------------------

    for attrs in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ]:

        tag = soup.find(
            "meta",
            attrs=attrs
        )

        if tag and tag.get("content"):
            return clean_text(
                tag["content"]
            )

    return ""


# ============================================================
# FETCH ARTICLE
# ============================================================

def fetch_article(item):

    source_url = item["link"]

    rss_title = item["title"]

    rss_summary = item.get(
        "summary",
        ""
    )

    log("--------------------------------")
    log(
        f"FETCHING: {rss_title}"
    )

    real_url = decode_google_news_url(
        source_url
    )

    if real_url != source_url:

        log(
            f"REAL URL: {real_url}"
        )

    # --------------------------------------------------------
    # FULL ARTICLE
    # --------------------------------------------------------

    try:

        response = requests.get(
            real_url,
            headers=HEADERS,
            timeout=ARTICLE_TIMEOUT,
            allow_redirects=True,
        )

        log(
            f"HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code == 200:

            content_type = (
                response.headers
                .get(
                    "content-type",
                    ""
                )
                .lower()
            )

            if "text/html" in content_type:

                soup = BeautifulSoup(
                    response.text,
                    "html.parser"
                )

                text = extract_article_text(
                    soup
                )

                if len(text) >= 500:

                    log(
                        f"Full article extracted: "
                        f"{len(text)} chars"
                    )

                    return {
                        "title": rss_title,
                        "url": response.url,
                        "text": text[
                            :MAX_ARTICLE_CHARS
                        ],
                        "summary": rss_summary,
                        "source_type":
                            "full_article",
                        "priority":
                            item.get(
                                "priority",
                                0
                            ),
                    }

                log(
                    f"Article too short: "
                    f"{len(text)} chars"
                )

    except Exception as error:

        log(
            f"Article fetch error: "
            f"{repr(error)}"
        )

    # --------------------------------------------------------
    # RSS FALLBACK
    # --------------------------------------------------------

    fallback = clean_text(
        rss_summary
    )

    if len(fallback) < MIN_RSS_SUMMARY_LENGTH:

        fallback = (
            "Источник содержит только "
            "краткий RSS-анонс. "
            "Подробности в исходной статье."
        )

    log(
        f"RSS fallback: "
        f"{len(fallback)} chars"
    )

    return {
        "title": rss_title,
        "url": (
            real_url
            if real_url != source_url
            else source_url
        ),
        "text": fallback[:5000],
        "summary": fallback,
        "source_type":
            "rss_fallback",
        "priority":
            item.get(
                "priority",
                0
            ),
    }


# ============================================================
# PREPARE ARTICLES
# ============================================================

def prepare_articles(news):

    candidates = news[
        :MAX_ARTICLES_TO_FETCH
    ]

    log(
        f"Preparing {len(candidates)} articles..."
    )

    results = []

    workers = min(
        8,
        max(1, len(candidates))
    )

    with ThreadPoolExecutor(
        max_workers=workers
    ) as executor:

        future_map = {
            executor.submit(
                fetch_article,
                item
            ): item

            for item in candidates
        }

        for future in as_completed(
            future_map
        ):

            original = future_map[
                future
            ]

            try:

                article = future.result()

                if article:
                    results.append(
                        article
                    )

            except Exception as error:

                log(
                    "Article worker failed: "
                    f"{repr(error)}"
                )

    results.sort(
        key=lambda x: (
            -x.get("priority", 0)
        )
    )

    log(
        f"Prepared {len(results)} usable articles"
    )

    return results


# ============================================================
# GEMINI
# ============================================================

def gemini_available():
    global gemini_cooldown_until

    if not GEMINI_API_KEY:
        return False

    if time.time() < gemini_cooldown_until:

        remaining = int(
            gemini_cooldown_until
            - time.time()
        )

        log(
            "Gemini cooldown active: "
            f"{remaining}s"
        )

        return False

    return True


def build_gemini_prompt(
    articles,
    memory
):

    articles_text = []

    for index, article in enumerate(
        articles,
        1
    ):

        articles_text.append(
            "\n".join([
                f"===== МАТЕРИАЛ {index} =====",
                f"ЗАГОЛОВОК: "
                f"{article['title']}",
                f"ТИП: "
                f"{article['source_type']}",
                f"URL: "
                f"{article['url']}",
                "ТЕКСТ:",
                article["text"],
            ])
        )

    articles_text = "\n\n".join(
        articles_text
    )

    memory_text = []

    for item in memory[-80:]:

        topic = item.get(
            "topic",
            ""
        )

        title = item.get(
            "title",
            ""
        )

        if topic or title:

            memory_text.append(
                f"- {topic or title}"
            )

    memory_text = "\n".join(
        memory_text
    )

    prompt = f"""
Ты главный редактор Telegram-канала LilsNews
по EA SPORTS FC 27.

Твоя задача — из переданных материалов выбрать
НЕСКОЛЬКО действительно разных и полезных новостей.

Можно создать от 1 до {MAX_POSTS_PER_CYCLE} постов.

ОЧЕНЬ ВАЖНО:

1. Не придумывай факты.
2. Используй только информацию из материалов.
3. Не придумывай игроков, OVR, цены, даты,
   SBC, награды, тактики или игровые механики.
4. Не публикуй уже опубликованную тему.
5. Если несколько материалов рассказывают
   об одном событии — объединяй их в ОДНУ новость.
6. Не делай несколько постов про одно и то же.
7. Если материал является только коротким RSS-анонсом,
   не додумывай отсутствующие детали.
8. Разрешается использовать материал
   с RSS fallback, но только факты,
   которые прямо есть в тексте.
9. Не добавляй ссылки.
10. Не указывай названия сайтов.
11. Не пиши анализ от себя.
12. Не пиши вступления вроде "Вот новости".
13. Каждый пост должен быть самостоятельным.
14. Новости должны быть конкретными.
15. Если новость является слухом или утечкой,
    обязательно сохраняй осторожную формулировку:
    "по данным источника", "сообщается", "по слухам",
    если это соответствует материалу.
16. Не превращай слух в подтверждённый факт.

ПРИОРИТЕТ:

🔥 META
🎮 GAMEPLAY
🛠 ПАТЧ
🃏 SBC
🟣 PROMO
⚠️ СЛУХ
⭐ PLAYERS
📊 RATINGS

Если есть несколько реально важных разных событий,
можно публиковать несколько.

ФОРМАТ:

===POST 1===
📰 LILSNEWS | КАТЕГОРИЯ

Заголовок

2–5 коротких предложений
с конкретной информацией.

TOPIC: уникальное короткое название события

===POST 2===
...

Если есть только одна хорошая новость,
верни только POST 1.

Если вообще нет пригодной новости:
NO_NEWS

УЖЕ ОПУБЛИКОВАНО:
{memory_text}

НОВЫЕ МАТЕРИАЛЫ:

{articles_text}

Ещё раз:
НЕ ПРИДУМЫВАЙ НИЧЕГО.
Ответь только постами указанного формата
или NO_NEWS.
"""

    return prompt


def call_gemini(
    articles,
    memory
):

    global gemini_cooldown_until

    if not gemini_available():

        log(
            "Gemini unavailable or cooldown active."
        )

        return None

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    prompt = build_gemini_prompt(
        articles,
        memory
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ]
    }

    for attempt in range(
        GEMINI_RETRIES + 1
    ):

        try:

            log(
                f"Calling Gemini "
                f"{GEMINI_MODEL} "
                f"(attempt {attempt + 1}/"
                f"{GEMINI_RETRIES + 1})"
            )

            response = requests.post(
                url,
                headers={
                    "x-goog-api-key":
                        GEMINI_API_KEY,
                    "Content-Type":
                        "application/json",
                },
                json=payload,
                timeout=90,
            )

            log(
                f"Gemini HTTP: "
                f"{response.status_code}"
            )

            # ------------------------------------------------
            # QUOTA
            # ------------------------------------------------

            if response.status_code == 429:

                log(
                    "Gemini 429 RESOURCE_EXHAUSTED"
                )

                gemini_cooldown_until = (
                    time.time()
                    + GEMINI_COOLDOWN
                )

                return None

            # ------------------------------------------------
            # SERVER ERRORS
            # ------------------------------------------------

            if response.status_code >= 500:

                if attempt < GEMINI_RETRIES:

                    sleep_time = (
                        3
                        * (attempt + 1)
                    )

                    log(
                        "Gemini server error. "
                        f"Retry in {sleep_time}s"
                    )

                    time.sleep(
                        sleep_time
                    )

                    continue

                return None

            # ------------------------------------------------
            # OTHER API ERRORS
            # ------------------------------------------------

            if response.status_code >= 400:

                try:
                    error_data = response.json()

                    log(
                        "Gemini API error response:"
                    )

                    log(
                        json.dumps(
                            error_data,
                            ensure_ascii=False,
                            indent=2
                        )
                    )

                except Exception:

                    log(
                        "Gemini raw error:"
                    )

                    log(
                        response.text[:3000]
                    )

                return None

            response.raise_for_status()

            data = response.json()

            parts = []

            for candidate in data.get(
                "candidates",
                []
            ):

                content = candidate.get(
                    "content",
                    {}
                )

                for part in content.get(
                    "parts",
                    []
                ):

                    text = part.get(
                        "text"
                    )

                    if text:
                        parts.append(
                            text
                        )

            result = "\n".join(
                parts
            ).strip()

            if not result:

                log(
                    "Gemini returned empty response."
                )

                log(
                    "Gemini response:"
                )

                log(
                    json.dumps(
                        data,
                        ensure_ascii=False,
                        indent=2
                    )[:5000]
                )

                return None

            log(
                "Gemini response received."
            )

            return result

        except requests.Timeout:

            log(
                "Gemini request TIMEOUT."
            )

            if attempt < GEMINI_RETRIES:

                sleep_time = (
                    3
                    * (attempt + 1)
                )

                log(
                    f"Retry in {sleep_time}s..."
                )

                time.sleep(
                    sleep_time
                )

            else:

                return None

        except requests.RequestException as error:

            log(
                f"Gemini request error: "
                f"{repr(error)}"
            )

            if attempt < GEMINI_RETRIES:

                time.sleep(
                    3 * (attempt + 1)
                )

            else:

                return None

        except Exception as error:

            log(
                f"Gemini error: "
                f"{repr(error)}"
            )

            return None

    return None


# ============================================================
# PARSE GEMINI POSTS
# ============================================================

def clean_gemini_output(text):

    if not text:
        return ""

    text = text.strip()

    text = re.sub(
        r"```(?:text|markdown)?",
        "",
        text,
        flags=re.I
    )

    text = text.replace(
        "```",
        ""
    )

    return text.strip()


def parse_gemini_posts(result):

    if not result:
        return []

    result = clean_gemini_output(
        result
    )

    if result.upper() == "NO_NEWS":
        return []

    pattern = re.compile(
        r"===\s*POST\s*\d+\s*===\s*(.*?)(?="
        r"===\s*POST\s*\d+\s*===|$)",
        flags=re.I | re.S
    )

    matches = pattern.findall(
        result
    )

    posts = []

    if matches:

        for block in matches:

            block = block.strip()

            if not block:
                continue

            posts.append(
                block
            )

    else:

        posts = [result]

    cleaned = []

    for post in posts:

        post = clean_gemini_output(
            post
        )

        if not post:
            continue

        topic_match = re.search(
            r"TOPIC\s*:\s*(.+)",
            post,
            flags=re.I
        )

        if topic_match:

            topic = (
                topic_match
                .group(1)
                .strip()
            )

            post_without_topic = re.sub(
                r"\n?\s*TOPIC\s*:\s*.+$",
                "",
                post,
                flags=re.I | re.S
            ).strip()

        else:

            lines = [
                x.strip()
                for x in post.splitlines()
                if x.strip()
            ]

            topic = (
                lines[0][:150]
                if lines
                else "FC 27 News"
            )

            post_without_topic = post

        post_without_topic = (
            post_without_topic.strip()
        )

        if (
            len(post_without_topic)
            < MIN_POST_LENGTH
        ):
            continue

        if (
            len(post_without_topic)
            > MAX_POST_LENGTH
        ):
            post_without_topic = (
                post_without_topic[
                    :MAX_POST_LENGTH
                ].rstrip()
                + "..."
            )

        cleaned.append({
            "post": post_without_topic,
            "topic": topic,
        })

    return cleaned


# ============================================================
# POST DUPLICATE CHECK
# ============================================================

def post_is_duplicate(
    post,
    topic,
    memory,
    current_posts
):

    if memory_has_topic(
        memory,
        topic
    ):
        return True

    for existing in current_posts:

        if title_similarity(
            topic,
            existing["topic"]
        ) >= 0.75:

            return True

    post_words = set(
        re.findall(
            r"\w+",
            post.lower()
        )
    )

    if len(post_words) < 10:
        return False

    for old in memory[-100:]:

        old_post = (
            old.get("post", "")
            .lower()
        )

        old_words = set(
            re.findall(
                r"\w+",
                old_post
            )
        )

        if len(old_words) < 10:
            continue

        intersection = (
            post_words
            & old_words
        )

        similarity = (
            len(intersection)
            / max(
                len(post_words),
                len(old_words)
            )
        )

        if similarity >= 0.80:
            return True

    return False


# ============================================================
# FIND SOURCE
# ============================================================

def find_best_source(
    topic,
    post,
    articles
):

    best = None
    best_score = -1

    topic_words = title_words(
        topic
    )

    post_lower = post.lower()

    for article in articles:

        title = article.get(
            "title",
            ""
        )

        article_title_words = title_words(
            title
        )

        score = len(
            topic_words
            & article_title_words
        ) * 10

        for word in article_title_words:

            if word in post_lower:
                score += 1

        if score > best_score:

            best_score = score
            best = article

    return best


# ============================================================
# RSS FALLBACK
# ============================================================

def category_for_title(title):

    low = title.lower()

    if "sbc" in low:
        return "🃏 SBC"

    if (
        "patch" in low
        or "update" in low
    ):
        return "🛠 ПАТЧ"

    if (
        "meta" in low
        or "tactic" in low
        or "formation" in low
    ):
        return "🔥 META"

    if "gameplay" in low:
        return "🎮 GAMEPLAY"

    if (
        "leak" in low
        or "leaked" in low
    ):
        return "⚠️ СЛУХ"

    if "promo" in low:
        return "🟣 PROMO"

    if (
        "rating" in low
        or "ratings" in low
    ):
        return "📊 RATINGS"

    if (
        "player" in low
        or "players" in low
        or "card" in low
        or "cards" in low
    ):
        return "⭐ PLAYERS"

    return "📰 NEWS"


def make_fallback_post(article):

    title = clean_text(
        article.get(
            "title",
            ""
        )
    )

    summary = clean_text(
        article.get(
            "summary",
            ""
        )
    )

    if not title:
        return None

    if not summary:
        return None

    category = category_for_title(
        title
    )

    summary = re.sub(
        r"\s*-\s*[A-Z][A-Za-z0-9 .'-]+$",
        "",
        summary
    ).strip()

    post = (
        f"📰 LILSNEWS | {category}\n\n"
        f"{title}\n\n"
        f"{summary}"
    )

    if len(post) > MAX_POST_LENGTH:

        post = (
            post[
                :MAX_POST_LENGTH
            ].rstrip()
            + "..."
        )

    if len(post) < MIN_POST_LENGTH:
        return None

    return {
        "post": post,
        "topic": title,
    }


# ============================================================
# CHOOSE ARTICLES
# ============================================================

def choose_articles_for_gemini(
    articles,
    memory
):

    selected = []

    for article in articles:

        if len(
            article.get("text", "")
        ) < 40:

            continue

        selected.append(
            article
        )

        if len(selected) >= MAX_ARTICLES_TO_FETCH:
            break

    return selected


# ============================================================
# PUBLISH GEMINI
# ============================================================

def publish_gemini_posts(
    result,
    articles,
    memory
):

    posts = parse_gemini_posts(
        result
    )

    if not posts:

        log(
            "Gemini produced no usable posts."
        )

        return memory, 0

    published_count = 0

    current_posts = []

    for item in posts:

        if (
            published_count
            >= MAX_POSTS_PER_CYCLE
        ):
            break

        post = item["post"]
        topic = item["topic"]

        if post_is_duplicate(
            post,
            topic,
            memory,
            current_posts
        ):

            log(
                f"Duplicate blocked: {topic}"
            )

            continue

        article = find_best_source(
            topic,
            post,
            articles
        )

        if not article:

            log(
                f"No source found for: {topic}"
            )

            continue

        try:

            send_telegram(
                post
            )

            log(
                f"Telegram publication successful: "
                f"{topic}"
            )

        except Exception as error:

            log(
                "Telegram publication failed: "
                f"{repr(error)}"
            )

            continue

        memory = remember_publication(
            memory,
            article,
            topic,
            post
        )

        current_posts.append({
            "topic": topic,
            "post": post,
        })

        published_count += 1

        time.sleep(1)

    return memory, published_count


# ============================================================
# PUBLISH FALLBACK
# ============================================================

def publish_fallback(
    articles,
    memory
):

    published = 0

    for article in articles:

        if (
            published
            >= MAX_POSTS_PER_CYCLE
        ):
            break

        source_url = article.get(
            "url",
            ""
        )

        title = article.get(
            "title",
            ""
        )

        if memory_has_source(
            memory,
            source_url
        ):
            continue

        if memory_has_title(
            memory,
            title
        ):
            continue

        generated = make_fallback_post(
            article
        )

        if not generated:
            continue

        post = generated[
            "post"
        ]

        topic = generated[
            "topic"
        ]

        if post_is_duplicate(
            post,
            topic,
            memory,
            []
        ):
            continue

        try:

            send_telegram(
                post
            )

            log(
                f"Fallback publication: {topic}"
            )

        except Exception as error:

            log(
                "Fallback Telegram error: "
                f"{repr(error)}"
            )

            continue

        memory = remember_publication(
            memory,
            article,
            topic,
            post
        )

        published += 1

        time.sleep(1)

    return memory, published


# ============================================================
# MAIN CYCLE
# ============================================================

def run_cycle(memory):

    log("")
    log("================================")
    log("STARTING NEWS CHECK")
    log("================================")

    # --------------------------------------------------------
    # 1. RSS
    # --------------------------------------------------------

    raw_news = get_news()

    if not raw_news:

        log(
            "No FC 27 news found."
        )

        return memory

    # --------------------------------------------------------
    # 2. Fresh
    # --------------------------------------------------------

    fresh_news = select_fresh_news(
        raw_news,
        memory
    )

    log(
        f"Fresh candidates: "
        f"{len(fresh_news)}"
    )

    if not fresh_news:

        log(
            "No fresh news."
        )

        return memory

    # --------------------------------------------------------
    # Print candidates
    # --------------------------------------------------------

    log("--------------------------------")
    log("FRESH CANDIDATES:")

    for index, item in enumerate(
        fresh_news,
        1
    ):

        log(
            f"{index}. "
            f"[{item.get('priority', 0)}] "
            f"{item['title']}"
        )

    # --------------------------------------------------------
    # 3. Fetch
    # --------------------------------------------------------

    articles = prepare_articles(
        fresh_news
    )

    if not articles:

        log(
            "No usable article material."
        )

        return memory

    # --------------------------------------------------------
    # 4. Gemini
    # --------------------------------------------------------

    gemini_articles = (
        choose_articles_for_gemini(
            articles,
            memory
        )
    )

    if not gemini_articles:

        log(
            "No articles for Gemini."
        )

        return memory

    result = call_gemini(
        gemini_articles,
        memory
    )

    # --------------------------------------------------------
    # 5. Gemini unavailable
    # --------------------------------------------------------

    if result is None:

        log(
            "Gemini unavailable."
        )

        log(
            "Using RSS fallback."
        )

        memory, count = (
            publish_fallback(
                articles,
                memory
            )
        )

        save_memory(
            memory
        )

        log(
            f"Fallback published: {count}"
        )

        return memory

    # --------------------------------------------------------
    # 6. NO_NEWS
    # --------------------------------------------------------

    if (
        result.strip().upper()
        == "NO_NEWS"
    ):

        log(
            "Gemini: NO_NEWS"
        )

        return memory

    # --------------------------------------------------------
    # 7. Publish
    # --------------------------------------------------------

    memory, published_count = (
        publish_gemini_posts(
            result,
            gemini_articles,
            memory
        )
    )

    # --------------------------------------------------------
    # 8. Save
    # --------------------------------------------------------

    save_memory(
        memory
    )

    log("--------------------------------")

    log(
        f"PUBLISHED THIS CYCLE: "
        f"{published_count}"
    )

    log(
        f"MEMORY ITEMS: "
        f"{len(memory)}"
    )

    log("================================")

    return memory


# ============================================================
# VALIDATION
# ============================================================

def validate_config():

    log("--------------------------------")
    log("CHECKING CONFIGURATION...")

    problems = []

    # --------------------------------------------------------
    # Telegram token
    # --------------------------------------------------------

    if not TELEGRAM_TOKEN:

        problems.append(
            "TELEGRAM_TOKEN"
        )

    # --------------------------------------------------------
    # Chat ID
    # --------------------------------------------------------

    if not TELEGRAM_CHAT_ID:

        problems.append(
            "TELEGRAM_CHAT_ID"
        )

    # --------------------------------------------------------
    # Gemini
    # --------------------------------------------------------

    if not GEMINI_API_KEY:

        log(
            "WARNING: GEMINI_API_KEY is not configured."
        )

        log(
            "Bot will use RSS fallback."
        )

    # --------------------------------------------------------
    # Errors
    # --------------------------------------------------------

    if problems:

        log(
            "CONFIGURATION ERROR:"
        )

        for problem in problems:

            log(
                f" - Missing environment variable: "
                f"{problem}"
            )

        return False

    log(
        "Telegram token: OK"
    )

    log(
        "Telegram chat ID: OK"
    )

    if GEMINI_API_KEY:
        log(
            "Gemini API key: OK"
        )
    else:
        log(
            "Gemini API key: NOT SET"
        )

    log(
        "Configuration check: OK"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    log("")
    log("================================")
    log("LILSNEWS STARTED")
    log("================================")

    log(
        f"Check interval: "
        f"{CHECK_INTERVAL // 60} minutes"
    )

    log(
        f"Max posts per cycle: "
        f"{MAX_POSTS_PER_CYCLE}"
    )

    log(
        f"Gemini model: "
        f"{GEMINI_MODEL}"
    )

    log(
        f"Python PID: "
        f"{os.getpid()}"
    )

    # --------------------------------------------------------
    # Config
    # --------------------------------------------------------

    if not validate_config():

        log(
            "Fix configuration first."
        )

        return

    # --------------------------------------------------------
    # Memory
    # --------------------------------------------------------

    memory = load_memory()

    log(
        f"Memory: "
        f"{len(memory)} events"
    )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    if not test_telegram():

        log(
            "Telegram connection failed."
        )

        log(
            "BOT STOPPED."
        )

        return

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    log("")
    log(
        "INITIALIZATION COMPLETE."
    )

    log(
        "Starting first news check..."
    )

    while True:

        try:

            memory = run_cycle(
                memory
            )

        except KeyboardInterrupt:

            log(
                "LilsNews stopped by user."
            )

            break

        except Exception as error:

            log(
                "Unexpected cycle error:"
            )

            log(
                repr(error)
            )

        log("--------------------------------")

        log(
            f"Sleeping "
            f"{CHECK_INTERVAL // 60} minutes..."
        )

        log("--------------------------------")

        try:

            time.sleep(
                CHECK_INTERVAL
            )

        except KeyboardInterrupt:

            log(
                "LilsNews stopped by user."
            )

            break


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    log(
        "Executing main()..."
    )

    main()
