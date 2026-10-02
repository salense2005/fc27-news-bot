import os
import json
import time
import re
import html
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse, urlunparse

import requests
import feedparser
from bs4 import BeautifulSoup


# ============================================================
# LILSNEWS — EA SPORTS FC 27 TELEGRAM NEWS BOT
#
# VERSION 3
#
# ОСНОВНЫЕ ИЗМЕНЕНИЯ:
#
#   - до 6 постов за один цикл;
#   - проверка каждые 5 минут;
#   - посты только на русском языке;
#   - Gemini делает русский заголовок и русский текст;
#   - RSS preview НЕ публикуется;
#   - публикуются только полноценные статьи;
#   - усиленная защита от дублей;
#   - одинаковые события из разных источников объединяются;
#   - память хранит только реально опубликованные новости;
#   - Gemini fallback больше не публикует короткие RSS-анонсы;
#   - более точный поиск источника для каждого поста;
#   - защита от повторной публикации одного события;
#   - до 6 разных новостей за цикл.
#
# УСТАНОВИ:
#
#   pip install requests feedparser beautifulsoup4
#
# НУЖНЫ:
#
#   TELEGRAM_TOKEN
#   TELEGRAM_CHAT_ID
#   GEMINI_API_KEY
#
# Рекомендуется использовать переменные окружения.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "PUT_NEW_TELEGRAM_TOKEN_HERE"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "PUT_CHAT_ID_HERE"
)

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    "PUT_NEW_GEMINI_API_KEY_HERE"
)


# ============================================================
# ОСНОВНЫЕ НАСТРОЙКИ
# ============================================================

MEMORY_FILE = "published_news.json"


# ------------------------------------------------------------
# Проверка новостей
# ------------------------------------------------------------

# Каждые 5 минут.
CHECK_INTERVAL = 5 * 60


# ------------------------------------------------------------
# Google News RSS
# ------------------------------------------------------------

MAX_RSS_ITEMS_PER_QUERY = 10


# ------------------------------------------------------------
# Кандидаты
# ------------------------------------------------------------

MAX_CANDIDATES = 30


# ------------------------------------------------------------
# Сколько статей реально скачивать
# ------------------------------------------------------------

MAX_ARTICLES_TO_FETCH = 18


# ------------------------------------------------------------
# Максимум публикаций за цикл
# ------------------------------------------------------------

MAX_POSTS_PER_CYCLE = 6


# ------------------------------------------------------------
# Максимум текста статьи для Gemini
# ------------------------------------------------------------

MAX_ARTICLE_CHARS = 12000


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

ARTICLE_TIMEOUT = 12

GOOGLE_REDIRECT_TIMEOUT = 15


# ------------------------------------------------------------
# Gemini
# ------------------------------------------------------------

GEMINI_MODEL = "gemini-3.8-flash"

GEMINI_RETRIES = 2

GEMINI_COOLDOWN = 60 * 60


# ------------------------------------------------------------
# Память
# ------------------------------------------------------------

MAX_MEMORY_ITEMS = 500


# ------------------------------------------------------------
# Статья считается полноценной,
# если удалось извлечь хотя бы столько текста.
#
# ВАЖНО:
# RSS preview теперь НЕ используется
# как полноценная статья.
# ------------------------------------------------------------

MIN_ARTICLE_LENGTH = 500


# ------------------------------------------------------------
# Посты
# ------------------------------------------------------------

MIN_POST_LENGTH = 120

MAX_POST_LENGTH = 1500


# ============================================================
# HTTP HEADERS
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),

    "Accept-Language": (
        "en-US,en;q=0.9"
    ),

    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
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
# GLOBAL STATE
# ============================================================

gemini_cooldown_until = 0


# ============================================================
# UTILS
# ============================================================

def now_timestamp():
    return int(time.time())


def normalize_whitespace(text):
    return re.sub(
        r"\s+",
        " ",
        str(text or "")
    ).strip()


def clean_text(text):

    if not text:
        return ""

    text = html.unescape(
        str(text)
    )

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

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = normalize_whitespace(
        text
    )

    return text


def normalize_url(url):

    if not url:
        return ""

    url = url.strip()

    try:

        parsed = urlparse(url)

        # Убираем fragment.
        parsed = parsed._replace(
            fragment=""
        )

        return urlunparse(
            parsed
        ).lower()

    except Exception:

        return url.lower()


def normalize_title(title):

    title = clean_text(
        title
    ).lower()

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

    title = re.sub(
        r"\s+",
        " ",
        title
    ).strip()

    return title


def title_words(title):

    return set(
        word
        for word in normalize_title(
            title
        ).split()
        if len(word) >= 3
    )


def title_similarity(a, b):

    words_a = title_words(a)
    words_b = title_words(b)

    if not words_a or not words_b:
        return 0.0

    intersection = (
        words_a & words_b
    )

    return len(intersection) / max(
        len(words_a),
        len(words_b)
    )


def safe_int(value, default=0):

    try:
        return int(value)

    except Exception:

        return default


def text_words(text):

    return set(
        re.findall(
            r"[a-zа-яё0-9]+",
            str(text or "").lower()
        )
    )


def text_similarity(a, b):

    words_a = text_words(a)
    words_b = text_words(b)

    if not words_a or not words_b:
        return 0.0

    common = words_a & words_b

    return len(common) / max(
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
        "fc 24",
        "fc24",
        "fifa 24",
    ]

    if any(
        word in low
        for word in forbidden
    ):
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
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/{method}"
    )


def test_telegram():

    try:

        response = requests.get(
            telegram_url("getMe"),
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):

            raise RuntimeError(
                f"Telegram API error: {data}"
            )

        bot_name = (
            data.get(
                "result",
                {}
            ).get(
                "username",
                "unknown"
            )
        )

        print(
            f"Telegram connection OK: "
            f"@{bot_name}"
        )

        return True

    except Exception as error:

        print(
            f"Telegram connection failed: "
            f"{error}"
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

    if not os.path.exists(
        MEMORY_FILE
    ):
        return []

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

            if isinstance(
                data,
                list
            ):
                return data

    except Exception as error:

        print(
            f"Memory read error: "
            f"{error}"
        )

    return []


def save_memory(memory):

    try:

        memory = memory[
            -MAX_MEMORY_ITEMS:
        ]

        temp_file = (
            MEMORY_FILE
            + ".tmp"
        )

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

        print(
            f"Memory save error: "
            f"{error}"
        )


def memory_has_source(
    memory,
    url
):

    if not url:
        return False

    url = normalize_url(
        url
    )

    for item in memory:

        old_url = normalize_url(
            item.get(
                "source_url",
                ""
            )
            or item.get(
                "link",
                ""
            )
        )

        if (
            old_url
            and old_url == url
        ):
            return True

    return False


def memory_has_title(
    memory,
    title
):

    normalized = normalize_title(
        title
    )

    if not normalized:
        return False

    for item in memory:

        old_title = normalize_title(
            item.get(
                "title",
                ""
            )
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


def memory_has_topic(
    memory,
    topic
):

    normalized = normalize_title(
        topic
    )

    if not normalized:
        return False

    for item in memory:

        old_topic = normalize_title(
            item.get(
                "topic",
                ""
            )
        )

        if not old_topic:
            continue

        if old_topic == normalized:
            return True

        similarity = title_similarity(
            topic,
            old_topic
        )

        if similarity >= 0.82:
            return True

    return False


def memory_has_similar_post(
    memory,
    post
):

    if not post:
        return False

    recent_memory = memory[
        -100:
    ]

    for item in recent_memory:

        old_post = item.get(
            "post",
            ""
        )

        if not old_post:
            continue

        similarity = text_similarity(
            post,
            old_post
        )

        if similarity >= 0.82:
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

        "title": article.get(
            "title",
            ""
        ),

        "source_url": article.get(
            "url",
            ""
        ),

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

    for query in SEARCH_QUERIES:

        print(
            f"RSS SEARCH: {query}"
        )

        try:

            rss_url = make_rss_url(
                query
            )

            response = requests.get(
                rss_url,
                headers=HEADERS,
                timeout=8,
            )

            response.raise_for_status()

            feed = feedparser.parse(
                response.content
            )

            for entry in feed.entries[
                :MAX_RSS_ITEMS_PER_QUERY
            ]:

                title = clean_text(
                    entry.get(
                        "title",
                        ""
                    )
                )

                link = (
                    entry.get(
                        "link",
                        ""
                    )
                    or ""
                ).strip()

                summary = clean_text(
                    entry.get(
                        "summary",
                        ""
                    )
                    or entry.get(
                        "description",
                        ""
                    )
                )

                published = (
                    entry.get(
                        "published",
                        ""
                    )
                    or entry.get(
                        "updated",
                        ""
                    )
                )

                if not title or not link:
                    continue

                if not is_fc27_title(
                    title
                ):
                    continue

                normalized_link = (
                    normalize_url(link)
                )

                if (
                    normalized_link
                    in seen_links
                ):
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

        except Exception as error:

            print(
                f"RSS error '{query}': "
                f"{error}"
            )

    print(
        f"TOTAL RSS NEWS FOUND: "
        f"{len(news)}"
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

            similarity = title_similarity(
                item["title"],
                existing["title"]
            )

            if similarity >= 0.72:

                duplicate = True

                # Если новая версия имеет
                # более подробный RSS-анонс,
                # оставляем его.
                if len(
                    item.get(
                        "summary",
                        ""
                    )
                ) > len(
                    existing.get(
                        "summary",
                        ""
                    )
                ):

                    existing[
                        "summary"
                    ] = item[
                        "summary"
                    ]

                break

        if not duplicate:

            result.append(
                item
            )

    return result


def select_fresh_news(
    news,
    memory
):

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

        item["priority"] = (
            calculate_priority(
                item["title"]
            )
        )

        fresh.append(
            item
        )

    fresh = deduplicate_news(
        fresh
    )

    fresh.sort(
        key=lambda x: (
            x.get(
                "priority",
                0
            ),
            x.get(
                "published",
                ""
            )
        ),
        reverse=True
    )

    return fresh[
        :MAX_CANDIDATES
    ]


# ============================================================
# GOOGLE NEWS REDIRECT
# ============================================================

def decode_google_news_url(
    url
):

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
            and
            "news.google.com/rss/articles/"
            not in final_url
        ):

            return final_url

    except Exception as error:

        print(
            f"Google redirect error: "
            f"{error}"
        )

    return url


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def extract_article_text(
    soup
):

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

            data = json.loads(
                raw
            )

            objects = []

            if isinstance(
                data,
                list
            ):

                objects = data

            elif isinstance(
                data,
                dict
            ):

                graph = data.get(
                    "@graph"
                )

                if isinstance(
                    graph,
                    list
                ):

                    objects = graph

                else:

                    objects = [
                        data
                    ]

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

                body = clean_text(
                    body
                )

                if len(body) >= 300:

                    bodies.append(
                        body
                    )

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
                ".social,.comments,.comment,"
                ".related,.recommended"
            ):

                bad.decompose()

            text = clean_text(
                copy.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 300:

                candidates.append(
                    text
                )

    if candidates:

        return max(
            candidates,
            key=len
        )


    # --------------------------------------------------------
    # PARAGRAPHS
    # --------------------------------------------------------

    paragraphs = []

    for p in soup.find_all(
        "p"
    ):

        text = clean_text(
            p.get_text(
                " ",
                strip=True
            )
        )

        if len(text) >= 40:

            paragraphs.append(
                text
            )

    if paragraphs:

        text = "\n".join(
            paragraphs
        )

        if len(text) >= 300:

            return text


    # --------------------------------------------------------
    # META DESCRIPTION
    #
    # ВАЖНО:
    # META DESCRIPTION НЕ считаем полноценной статьёй.
    # Она здесь возвращается только для диагностики.
    # --------------------------------------------------------

    for attrs in [

        {"name": "description"},

        {
            "property":
            "og:description"
        },

        {
            "name":
            "twitter:description"
        },

    ]:

        tag = soup.find(
            "meta",
            attrs=attrs
        )

        if (
            tag
            and tag.get(
                "content"
            )
        ):

            return clean_text(
                tag["content"]
            )

    return ""


# ============================================================
# FETCH ARTICLE
# ============================================================

def fetch_article(item):

    source_url = item[
        "link"
    ]

    rss_title = item[
        "title"
    ]

    print(
        "--------------------------------"
    )

    print(
        f"Fetching: {rss_title}"
    )

    real_url = decode_google_news_url(
        source_url
    )

    if real_url != source_url:

        print(
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

        print(
            f"HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "Article unavailable."
            )

            return None

        content_type = (
            response.headers
            .get(
                "content-type",
                ""
            )
            .lower()
        )

        if "text/html" not in content_type:

            print(
                "Not an HTML article."
            )

            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        text = extract_article_text(
            soup
        )

        # ----------------------------------------------------
        # КРИТИЧЕСКАЯ ПРОВЕРКА
        #
        # Если полноценный текст статьи не найден,
        # НИЧЕГО НЕ ПУБЛИКУЕМ.
        # ----------------------------------------------------

        if len(text) < MIN_ARTICLE_LENGTH:

            print(
                f"Article rejected: "
                f"only {len(text)} chars"
            )

            return None

        print(
            f"Full article accepted: "
            f"{len(text)} chars"
        )

        return {

            "title": rss_title,

            "url": response.url,

            "text": text[
                :MAX_ARTICLE_CHARS
            ],

            "summary": clean_text(
                item.get(
                    "summary",
                    ""
                )
            ),

            "source_type":
                "full_article",

            "priority":
                item.get(
                    "priority",
                    0
                ),

        }

    except Exception as error:

        print(
            f"Article fetch error: "
            f"{error}"
        )

        return None


# ============================================================
# PREPARE ARTICLES
# ============================================================

def prepare_articles(
    news
):

    candidates = news[
        :MAX_ARTICLES_TO_FETCH
    ]

    print(
        f"Preparing "
        f"{len(candidates)} articles..."
    )

    results = []

    workers = min(
        8,
        max(
            1,
            len(candidates)
        )
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

            try:

                article = (
                    future.result()
                )

                if article:

                    results.append(
                        article
                    )

            except Exception as error:

                print(
                    "Article worker failed: "
                    f"{error}"
                )

    results.sort(
        key=lambda x: (
            -x.get(
                "priority",
                0
            )
        )
    )

    print(
        f"Prepared "
        f"{len(results)} usable "
        f"full articles"
    )

    return results


# ============================================================
# GEMINI
# ============================================================

def gemini_available():

    global gemini_cooldown_until

    if not GEMINI_API_KEY:

        return False

    if (
        GEMINI_API_KEY
        == "PUT_NEW_GEMINI_API_KEY_HERE"
    ):

        return False

    if (
        time.time()
        < gemini_cooldown_until
    ):

        remaining = int(
            gemini_cooldown_until
            - time.time()
        )

        print(
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

                "ПОЛНЫЙ ТЕКСТ СТАТЬИ:",

                article["text"],

            ])
        )

    articles_text = "\n\n".join(
        articles_text
    )


    # --------------------------------------------------------
    # Последние опубликованные темы
    # --------------------------------------------------------

    memory_text = []

    for item in memory[
        -100:
    ]:

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
Ты — главный редактор Telegram-канала LilsNews
по EA SPORTS FC 27.

Тебе переданы ПОЛНЫЕ ТЕКСТЫ свежих статей.

Твоя задача — выбрать из них действительно важные
и РАЗНЫЕ новости для Telegram.

Можно создать от 1 до {MAX_POSTS_PER_CYCLE} постов.

==================================================
ГЛАВНОЕ ПРАВИЛО
==================================================

ПИШИ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ.

Не оставляй английский заголовок статьи
как заголовок Telegram-поста.

Сделай НОВЫЙ естественный русский заголовок,
который кратко передаёт суть новости.

Текст новости также должен быть на русском.

==================================================
ФАКТЫ
==================================================

1. Не придумывай факты.

2. Используй ТОЛЬКО информацию,
   которая есть в переданных статьях.

3. Не придумывай:
   - игроков;
   - OVR;
   - характеристики;
   - цены;
   - даты;
   - SBC;
   - награды;
   - промо;
   - тактики;
   - игровые механики;
   - результаты матчей;
   - заявления разработчиков.

4. Если конкретной информации в статье нет —
   НЕ ДОБАВЛЯЙ её от себя.

5. Не делай выводы, которых нет в источнике.

==================================================
ДУБЛИ
==================================================

6. Если несколько материалов рассказывают
   об одном и том же событии,
   НЕ создавай несколько постов.

   Объедини информацию в ОДНУ новость.

7. Не делай два поста с одной и той же темой,
   даже если источники разные.

8. Каждая опубликованная новость должна
   заметно отличаться от остальных.

9. Не используй уже опубликованные темы.

==================================================
КАЧЕСТВО
==================================================

10. Не пересказывай статью одним предложением.

11. Пост должен содержать:
    - понятный русский заголовок;
    - 2–5 содержательных предложений;
    - конкретную информацию.

12. Если новость действительно важная,
    можно написать немного подробнее,
    но не превращай пост в огромную статью.

13. Не копируй английский текст дословно.

14. Не пиши:
    "Вот главные новости".
    "Стало известно".
    "По информации источника"
    без необходимости.

15. Не добавляй ссылки.

16. Не указывай название сайта.

17. Не добавляй хэштеги,
    если их нет в исходном материале.

18. Не добавляй мнение от себя.

==================================================
ПРИОРИТЕТ
==================================================

Предпочтение отдавай:

🔥 META
🎮 GAMEPLAY
🛠 ПАТЧ
🃏 SBC
🟣 PROMO
⚠️ СЛУХ
⭐ PLAYERS
📊 RATINGS

Но публикуй только реальные новости.

==================================================
ФОРМАТ
==================================================

Каждый пост ОБЯЗАТЕЛЬНО должен иметь такой формат:

===POST 1===

📰 LILSNEWS | КАТЕГОРИЯ

НОВЫЙ РУССКИЙ ЗАГОЛОВОК

2–5 содержательных предложений
на русском языке.

TOPIC: уникальное короткое название события

===POST 2===

...

TOPIC должен быть коротким названием
САМОГО СОБЫТИЯ, а не копией заголовка статьи.

Например:

TOPIC: Новый SBC с игроком X

или:

TOPIC: Изменения игрового процесса

==================================================
ЕСЛИ НЕТ НОРМАЛЬНЫХ НОВОСТЕЙ
==================================================

Если нет ни одной новости,
которую можно уверенно подтвердить
переданными материалами:

NO_NEWS

==================================================
УЖЕ ОПУБЛИКОВАНО
==================================================

{memory_text}

==================================================
НОВЫЕ МАТЕРИАЛЫ
==================================================

{articles_text}

==================================================
ФИНАЛЬНЫЕ ПРАВИЛА
==================================================

НЕ ПРИДУМЫВАЙ НИЧЕГО.

ПИШИ ТОЛЬКО НА РУССКОМ.

НЕ КОПИРУЙ АНГЛИЙСКИЕ ЗАГОЛОВКИ.

НЕ ПУБЛИКУЙ RSS-ПРЕВЬЮ.

НЕ ДЕЛАЙ ДВА ПОСТА ПРО ОДНО СОБЫТИЕ.

НЕ ПОВТОРЯЙ УЖЕ ОПУБЛИКОВАННЫЕ ТЕМЫ.

Ответь ТОЛЬКО постами указанного формата
или NO_NEWS.
"""

    return prompt


def call_gemini(
    articles,
    memory
):

    global gemini_cooldown_until

    if not gemini_available():

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

        ],

        "generationConfig": {

            "temperature": 0.15,

            "maxOutputTokens": 3000,

        }

    }


    for attempt in range(
        GEMINI_RETRIES + 1
    ):

        try:

            print(
                f"Calling Gemini "
                f"{GEMINI_MODEL} "
                f"(attempt {attempt + 1})"
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

                timeout=60,

            )

            print(
                f"Gemini HTTP: "
                f"{response.status_code}"
            )


            # ------------------------------------------------
            # QUOTA
            # ------------------------------------------------

            if response.status_code == 429:

                print(
                    "Gemini 429 "
                    "RESOURCE_EXHAUSTED"
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

                if (
                    attempt
                    < GEMINI_RETRIES
                ):

                    sleep_time = (
                        3
                        * (attempt + 1)
                    )

                    print(
                        "Gemini server error. "
                        f"Retry in {sleep_time}s"
                    )

                    time.sleep(
                        sleep_time
                    )

                    continue

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

                print(
                    "Gemini returned "
                    "empty response."
                )

                return None

            print(
                "Gemini response received."
            )

            return result

        except requests.RequestException as error:

            print(
                f"Gemini request error: "
                f"{error}"
            )

            if (
                attempt
                < GEMINI_RETRIES
            ):

                time.sleep(
                    3
                    * (attempt + 1)
                )

            else:

                return None

        except Exception as error:

            print(
                f"Gemini error: "
                f"{error}"
            )

            return None

    return None


# ============================================================
# PARSE GEMINI POSTS
# ============================================================

def clean_gemini_output(
    text
):

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


def extract_topic(
    post
):

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

        return (
            topic,
            post_without_topic
        )


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

    return (
        topic,
        post
    )


def looks_like_english(
    text
):

    if not text:
        return False

    words = re.findall(
        r"\b[a-zA-Z]{3,}\b",
        text
    )

    if len(words) < 5:
        return False

    russian_words = re.findall(
        r"\b[а-яА-ЯёЁ]{3,}\b",
        text
    )

    # Если русский отсутствует,
    # а английских слов много —
    # скорее всего Gemini вернул английский.
    if (
        len(russian_words) == 0
        and len(words) >= 5
    ):
        return True

    return False


def parse_gemini_posts(
    result
):

    if not result:
        return []

    result = clean_gemini_output(
        result
    )

    if (
        result.upper()
        == "NO_NEWS"
    ):
        return []

    pattern = re.compile(

        r"===\s*POST\s*\d+\s*===\s*"
        r"(.*?)"
        r"(?===\s*POST\s*\d+\s*===|$)",

        flags=re.I | re.S

    )

    matches = pattern.findall(
        result
    )

    posts = []

    if matches:

        for block in matches:

            block = block.strip()

            if block:

                posts.append(
                    block
                )

    else:

        posts = [
            result
        ]


    cleaned = []

    for post in posts:

        post = clean_gemini_output(
            post
        )

        if not post:
            continue


        topic, post_without_topic = (
            extract_topic(
                post
            )
        )


        post_without_topic = (
            post_without_topic.strip()
        )


        # ----------------------------------------------------
        # Проверка длины
        # ----------------------------------------------------

        if (
            len(post_without_topic)
            < MIN_POST_LENGTH
        ):

            print(
                "Gemini post rejected: "
                "too short"
            )

            continue


        # ----------------------------------------------------
        # Проверка языка
        # ----------------------------------------------------

        if looks_like_english(
            post_without_topic
        ):

            print(
                "Gemini post rejected: "
                "looks English"
            )

            continue


        # ----------------------------------------------------
        # Ограничение длины
        # ----------------------------------------------------

        if (
            len(post_without_topic)
            > MAX_POST_LENGTH
        ):

            post_without_topic = (
                post_without_topic[
                    :MAX_POST_LENGTH
                ]
                .rstrip()
                + "..."
            )


        cleaned.append({

            "post":
                post_without_topic,

            "topic":
                topic,

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

    # --------------------------------------------------------
    # Topic
    # --------------------------------------------------------

    if memory_has_topic(
        memory,
        topic
    ):

        return True


    # --------------------------------------------------------
    # Текущий цикл
    # --------------------------------------------------------

    for existing in current_posts:

        if (
            title_similarity(
                topic,
                existing[
                    "topic"
                ]
            )
            >= 0.75
        ):

            return True


        if (
            text_similarity(
                post,
                existing[
                    "post"
                ]
            )
            >= 0.75
        ):

            return True


    # --------------------------------------------------------
    # Полный пост против памяти
    # --------------------------------------------------------

    if memory_has_similar_post(
        memory,
        post
    ):

        return True


    return False


# ============================================================
# FIND SOURCE FOR GENERATED POST
# ============================================================

def find_best_source(
    topic,
    post,
    articles
):

    best = None

    best_score = -1

    topic_words_set = title_words(
        topic
    )

    post_words = text_words(
        post
    )

    for article in articles:

        title = article.get(
            "title",
            ""
        )

        article_text = article.get(
            "text",
            ""
        )

        title_words_set = title_words(
            title
        )

        score = 0


        # ----------------------------------------------------
        # Topic ↔ source title
        # ----------------------------------------------------

        score += (
            len(
                topic_words_set
                &
                title_words_set
            )
            * 20
        )


        # ----------------------------------------------------
        # Source title ↔ generated post
        # ----------------------------------------------------

        score += (
            len(
                title_words_set
                &
                post_words
            )
            * 3
        )


        # ----------------------------------------------------
        # Topic ↔ article text
        # ----------------------------------------------------

        article_words = text_words(
            article_text
        )

        score += (
            len(
                topic_words_set
                &
                article_words
            )
            * 2
        )


        if score > best_score:

            best_score = score

            best = article


    # Если вообще ничего общего нет,
    # источник лучше НЕ назначать.
    if best_score <= 0:

        return None

    return best


# ============================================================
# PUBLISH GEMINI POSTS
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

        print(
            "Gemini produced no usable posts."
        )

        return (
            memory,
            0
        )


    published_count = 0

    current_posts = []


    for item in posts:

        if (
            published_count
            >= MAX_POSTS_PER_CYCLE
        ):

            break


        post = item[
            "post"
        ]

        topic = item[
            "topic"
        ]


        # ----------------------------------------------------
        # Duplicate protection
        # ----------------------------------------------------

        if post_is_duplicate(
            post,
            topic,
            memory,
            current_posts
        ):

            print(
                f"Duplicate blocked: "
                f"{topic}"
            )

            continue


        # ----------------------------------------------------
        # Source
        # ----------------------------------------------------

        article = find_best_source(
            topic,
            post,
            articles
        )

        if not article:

            print(
                f"No reliable source "
                f"found for: {topic}"
            )

            continue


        # ----------------------------------------------------
        # Publish
        # ----------------------------------------------------

        try:

            send_telegram(
                post
            )

            print(
                "Telegram publication "
                f"successful: {topic}"
            )

        except Exception as error:

            print(
                "Telegram publication "
                f"failed: {error}"
            )

            continue


        # ----------------------------------------------------
        # Memory ONLY after successful send
        # ----------------------------------------------------

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


        time.sleep(
            1
        )


    return (
        memory,
        published_count
    )


# ============================================================
# GEMINI ARTICLE SELECTION
# ============================================================

def choose_articles_for_gemini(
    articles,
    memory
):

    selected = []

    for article in articles:

        # Только полноценные статьи.
        if (
            article.get(
                "source_type"
            )
            != "full_article"
        ):

            continue


        if len(
            article.get(
                "text",
                ""
            )
        ) < MIN_ARTICLE_LENGTH:

            continue


        selected.append(
            article
        )


        if (
            len(selected)
            >= MAX_ARTICLES_TO_FETCH
        ):

            break


    return selected


# ============================================================
# MAIN CYCLE
# ============================================================

def run_cycle(
    memory
):

    print("\n")

    print(
        "================================"
    )

    print(
        "STARTING NEWS CHECK"
    )

    print(
        "================================"
    )


    # --------------------------------------------------------
    # 1. RSS
    # --------------------------------------------------------

    raw_news = get_news()

    if not raw_news:

        print(
            "No FC 27 news found."
        )

        return memory


    # --------------------------------------------------------
    # 2. Fresh news
    # --------------------------------------------------------

    fresh_news = select_fresh_news(
        raw_news,
        memory
    )

    print(
        f"Fresh candidates: "
        f"{len(fresh_news)}"
    )

    if not fresh_news:

        print(
            "No fresh news."
        )

        return memory


    # --------------------------------------------------------
    # Candidates
    # --------------------------------------------------------

    print(
        "--------------------------------"
    )

    for index, item in enumerate(
        fresh_news,
        1
    ):

        print(

            f"{index}. "
            f"[{item.get('priority', 0)}] "
            f"{item['title']}"

        )


    # --------------------------------------------------------
    # 3. Fetch FULL articles
    # --------------------------------------------------------

    articles = prepare_articles(
        fresh_news
    )


    if not articles:

        print(
            "No full articles available."
        )

        print(
            "Nothing will be published."
        )

        return memory


    # --------------------------------------------------------
    # 4. Gemini articles
    # --------------------------------------------------------

    gemini_articles = (
        choose_articles_for_gemini(
            articles,
            memory
        )
    )


    if not gemini_articles:

        print(
            "No articles for Gemini."
        )

        return memory


    print(
        f"Articles sent to Gemini: "
        f"{len(gemini_articles)}"
    )


    # --------------------------------------------------------
    # 5. Gemini
    # --------------------------------------------------------

    result = call_gemini(
        gemini_articles,
        memory
    )


    # --------------------------------------------------------
    # 6. Gemini unavailable
    #
    # ВАЖНО:
    #
    # Старый RSS fallback удалён.
    #
    # Если Gemini недоступен, бот НЕ публикует
    # сырые RSS-превью.
    # --------------------------------------------------------

    if result is None:

        print(
            "Gemini unavailable."
        )

        print(
            "No fallback publication."
        )

        print(
            "Full articles remain "
            "unpublished and can be "
            "found again on next cycle."
        )

        return memory


    # --------------------------------------------------------
    # 7. Gemini says NO_NEWS
    # --------------------------------------------------------

    if (
        result.strip().upper()
        == "NO_NEWS"
    ):

        print(
            "Gemini: NO_NEWS"
        )

        return memory


    # --------------------------------------------------------
    # 8. Publish
    # --------------------------------------------------------

    memory, published_count = (
        publish_gemini_posts(
            result,
            gemini_articles,
            memory
        )
    )


    # --------------------------------------------------------
    # 9. Save memory
    # --------------------------------------------------------

    save_memory(
        memory
    )


    print(
        "--------------------------------"
    )

    print(
        f"PUBLISHED THIS CYCLE: "
        f"{published_count}"
    )

    print(
        f"MEMORY ITEMS: "
        f"{len(memory)}"
    )

    print(
        "================================"
    )


    return memory


# ============================================================
# VALIDATION
# ============================================================

def validate_config():

    problems = []


    if (
        not TELEGRAM_TOKEN
        or
        TELEGRAM_TOKEN
        == "PUT_NEW_TELEGRAM_TOKEN_HERE"
    ):

        problems.append(
            "TELEGRAM_TOKEN"
        )


    if (
        not TELEGRAM_CHAT_ID
        or
        TELEGRAM_CHAT_ID
        == "PUT_CHAT_ID_HERE"
    ):

        problems.append(
            "TELEGRAM_CHAT_ID"
        )


    if (
        not GEMINI_API_KEY
        or
        GEMINI_API_KEY
        == "PUT_NEW_GEMINI_API_KEY_HERE"
    ):

        print(
            "WARNING: Gemini API key "
            "is not configured."
        )

        print(
            "Bot will NOT publish RSS "
            "fallback previews."
        )

        print(
            "Gemini is required for "
            "publication."
        )


    if problems:

        print(
            "CONFIGURATION ERROR:"
        )

        for problem in problems:

            print(
                f" - {problem}"
            )

        return False


    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "================================"
    )

    print(
        "LILSNEWS STARTED"
    )

    print(
        "================================"
    )

    print(
        f"Check interval: "
        f"{CHECK_INTERVAL // 60} minutes"
    )

    print(
        f"Max posts per cycle: "
        f"{MAX_POSTS_PER_CYCLE}"
    )

    print(
        f"Gemini model: "
        f"{GEMINI_MODEL}"
    )

    print(
        f"Minimum article length: "
        f"{MIN_ARTICLE_LENGTH} chars"
    )


    # --------------------------------------------------------
    # Config
    # --------------------------------------------------------

    if not validate_config():

        print(
            "Fix configuration first."
        )

        return


    # --------------------------------------------------------
    # Memory
    # --------------------------------------------------------

    memory = load_memory()

    print(
        f"Memory: "
        f"{len(memory)} events"
    )


    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    if not test_telegram():

        print(
            "Telegram connection failed."
        )

        print(
            "BOT STOPPED."
        )

        return


    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    while True:

        try:

            memory = run_cycle(
                memory
            )

        except KeyboardInterrupt:

            print(
                "LilsNews stopped by user."
            )

            break

        except Exception as error:

            print(
                "Unexpected cycle error:"
            )

            print(
                repr(error)
            )


        print(
            "--------------------------------"
        )

        print(
            f"Sleeping "
            f"{CHECK_INTERVAL // 60} minutes..."
        )

        print(
            "--------------------------------"
        )


        try:

            time.sleep(
                CHECK_INTERVAL
            )

        except KeyboardInterrupt:

            print(
                "LilsNews stopped by user."
            )

            break


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
