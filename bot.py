import os
import json
import time
import re
import html
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai


# ============================================================
# LILSNEWS — EA SPORTS FC 27 TELEGRAM NEWS BOT
#
# ENVIRONMENT VARIABLES:
#
# TELEGRAM_TOKEN
# TELEGRAM_CHAT_ID
# GEMINI_API_KEY
#
# BOT:
# 1. Мониторит Google News RSS
# 2. Ищет новости именно EA FC 27
# 3. Пытается открыть оригинальную статью
# 4. Если статья не открывается — использует RSS
# 5. Передаёт информацию Gemini
# 6. Gemini пишет Telegram-пост на русском
# 7. Отправляет пост в Telegram
# 8. Запоминает опубликованные новости
# 9. Не публикует одну и ту же новость повторно
# ============================================================


# ============================================================
# SETTINGS
# ============================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

MEMORY_FILE = "published_news.json"

# Проверка Google News каждые 2 минуты
CHECK_INTERVAL = 2 * 60

MAX_RSS_ITEMS_PER_QUERY = 12
MAX_CANDIDATES = 20
MAX_ARTICLE_CHARS = 16000

# Сколько новостей можно обрабатывать одновременно
WORKER_COUNT = 4


# ============================================================
# GEMINI
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY)


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
}


# ============================================================
# GOOGLE NEWS SEARCHES
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
    "EA FC 27 Ultimate Team",
    "EA Sports FC 27 news",
]


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
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


def test_telegram():

    try:

        send_telegram(
            "🤖 LilsNews запущен.\n\n"
            "Мониторинг EA FC 27 активен."
        )

        print(
            "Telegram test message sent successfully."
        )

        return True

    except Exception as error:

        print(
            f"Telegram test failed: {error}"
        )

        return False


# ============================================================
# MEMORY
# ============================================================

def load_memory():

    if not os.path.exists(MEMORY_FILE):
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

            return []

    except Exception as error:

        print(
            f"Memory error: {error}"
        )

        return []


def save_memory(memory):

    with open(
        MEMORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            memory[-300:],
            file,
            ensure_ascii=False,
            indent=2
        )


def memory_has_item(
    memory,
    link="",
    title=""
):

    link = (
        link or ""
    ).strip().lower()

    title = re.sub(
        r"\s+",
        " ",
        (title or "").strip().lower()
    )

    for old in memory:

        old_link = (
            old.get("link") or ""
        ).strip().lower()

        old_title = re.sub(
            r"\s+",
            " ",
            (old.get("title") or "")
            .strip()
            .lower()
        )

        if (
            link
            and old_link
            and link == old_link
        ):
            return True

        if (
            title
            and old_title
            and title == old_title
        ):
            return True

    return False


# ============================================================
# TEXT CLEANING
# ============================================================

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
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# FC 27 FILTER
# ============================================================

def is_fc27_title(title):

    low = title.lower()

    # Никогда не смешиваем FC 25 / FC 26
    forbidden = [
        "fc 26",
        "fc26",
        "fc 25",
        "fc25",
        "fifa 26",
        "fifa 25",
    ]

    if any(
        x in low
        for x in forbidden
    ):
        return False

    return (
        "fc 27" in low
        or "fc27" in low
    )


# ============================================================
# GOOGLE NEWS RSS
# ============================================================

def make_rss_url(query):

    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US"
        + "&gl=US"
        + "&ceid=US:en"
    )


def get_news():

    news = []
    seen = set()

    for query in SEARCH_QUERIES:

        try:

            feed = feedparser.parse(
                make_rss_url(query)
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

                if not title or not link:
                    continue

                if not is_fc27_title(title):
                    continue

                normalized = re.sub(
                    r"[^a-z0-9]+",
                    "",
                    title.lower()
                )

                if normalized in seen:
                    continue

                seen.add(normalized)

                news.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": summary,
                        "published": entry.get(
                            "published",
                            ""
                        ),
                    }
                )

        except Exception as error:

            print(
                f"RSS error for "
                f"'{query}': {error}"
            )

    return news


# ============================================================
# PRIORITY
# ============================================================

def calculate_priority(title):

    low = title.lower()

    points = {

        "sbc": 180,

        "new sbc": 70,

        "card": 110,

        "cards": 110,

        "player": 80,

        "players": 80,

        "rating": 80,

        "ratings": 80,

        "meta": 140,

        "tactic": 120,

        "tactics": 120,

        "formation": 105,

        "gameplay": 100,

        "pro player": 110,

        "promo": 80,

        "team 2": 75,

        "team 1": 75,

        "leak": 65,

        "leaked": 65,

        "patch": 110,

        "update": 60,

        "objective": 90,

        "evolution": 90,

        "upgrade": 65,

        "ultimate team": 40,
    }

    return sum(
        value
        for key, value in points.items()
        if key in low
    )


# ============================================================
# GOOGLE NEWS URL
# ============================================================

def decode_google_news_url(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True
        )

        final_url = response.url or ""

        if (
            final_url
            and
            "news.google.com/rss/articles/"
            not in final_url
        ):
            return final_url

    except Exception as error:

        print(
            f"Google News decode error: "
            f"{error}"
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

                if body:

                    body = clean_text(
                        body
                    )

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
    # COMMON ARTICLE CONTAINERS
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
                ".advertisement,"
                ".ads,.social,"
                ".comments,.comment"
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
            and tag.get("content")
        ):

            return clean_text(
                tag.get("content")
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

    print(
        f"Fetching: {source_url}"
    )

    real_url = decode_google_news_url(
        source_url
    )

    if real_url != source_url:

        print(
            f"REAL ARTICLE URL: "
            f"{real_url}"
        )

    # --------------------------------------------------------
    # TRY ORIGINAL ARTICLE
    # --------------------------------------------------------

    try:

        response = requests.get(
            real_url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True
        )

        print(
            f"Final URL: "
            f"{response.url}"
        )

        print(
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

                    print(
                        "Full article extracted: "
                        f"{len(text)} characters"
                    )

                    return {

                        "title": rss_title,

                        "url": response.url,

                        "text": text[
                            :MAX_ARTICLE_CHARS
                        ],

                        "source_type":
                            "full_article",

                        "priority":
                            item.get(
                                "priority",
                                0
                            ),
                    }

                print(
                    f"Article text too short "
                    f"({len(text)} chars). "
                    "Using RSS fallback."
                )

    except Exception as error:

        print(
            f"Article fetch error: "
            f"{error}"
        )

    # --------------------------------------------------------
    # RSS FALLBACK
    # --------------------------------------------------------

    fallback = clean_text(
        rss_summary
    )

    if len(fallback) < 20:

        fallback = (
            "No usable RSS summary "
            "was provided."
        )

    print(
        "Using RSS fallback: "
        f"title + {len(fallback)} "
        "summary characters"
    )

    return {

        "title": rss_title,

        "url": (
            real_url
            if real_url != source_url
            else source_url
        ),

        "text": fallback[:5000],

        "source_type":
            "rss_fallback",

        "priority":
            item.get(
                "priority",
                0
            ),
    }


# ============================================================
# GEMINI
# ============================================================

def analyze_news(
    article,
    memory
):

    if not article:
        return None

    memory_text = "\n".join(

        f"TOPIC: "
        f"{x.get('topic', '')}\n"
        f"TITLE: "
        f"{x.get('title', '')}"

        for x in memory[-80:]
    )

    prompt = f"""

Ты главный редактор Telegram-канала LilsNews
по EA SPORTS FC 27.

Твоя задача — обработать ЭТУ ОДНУ НОВОСТЬ
и подготовить подробный пост для игроков
EA FC 27 Ultimate Team.

========================================
ГЛАВНОЕ ПРАВИЛО
========================================

ИСПОЛЬЗУЙ ТОЛЬКО ИНФОРМАЦИЮ ИЗ МАТЕРИАЛА.

НИЧЕГО НЕ ПРИДУМЫВАЙ.

Если в материале нет:

- OVR
- цены
- требований SBC
- PlayStyles
- статистики
- даты
- наград
- рейтингов
- позиции
- характеристик
- названия промо

НЕ ДОБАВЛЯЙ ЭТИ ДАННЫЕ ОТ СЕБЯ.

========================================
ЕСЛИ МАТЕРИАЛ RSS FALLBACK
========================================

Если тип материала:

rss_fallback

то информации может быть мало.

В таком случае:

- не додумывай содержание статьи;
- не придумывай цифры;
- не придумывай характеристики;
- не придумывай мнение;
- используй только заголовок и доступный текст.

========================================
СТИЛЬ
========================================

Пиши естественно на русском языке.

Пост должен выглядеть как нормальная
новость Telegram-канала по FC 27.

Не пиши как робот.

Не начинай каждый пост одинаково.

Обычно 120–300 слов.

Но если информации мало —
пост должен быть короче.

========================================
КАТЕГОРИЯ
========================================

Самостоятельно выбери категорию:

SBC

ULTIMATE TEAM

CARDS

RATINGS

LEAK

PROMO

GAMEPLAY

META

TACTICS

PATCH

OBJECTIVE

EVOLUTION

OTHER

========================================
ФОРМАТ
========================================

📰 LILSNEWS

[КАТЕГОРИЯ]

[ЗАГОЛОВОК]

[ПОДРОБНОСТИ]

Источник: {article["url"]}

TOPIC: уникальная тема новости

========================================
ВАЖНО
========================================

TOPIC должен быть коротким,
но конкретным.

Например:

TOPIC: Новый SBC Килиана Мбаппе

или

TOPIC: Новые рейтинги игроков FC 27

Не используй просто:

TOPIC: FC 27

========================================
УЖЕ ОПУБЛИКОВАНО
========================================

{memory_text}

========================================
МАТЕРИАЛ
========================================

ЗАГОЛОВОК:

{article["title"]}

ТИП:

{article["source_type"]}

ТЕКСТ:

{article["text"]}

"""


    # --------------------------------------------------------
    # GEMINI MODELS
    # --------------------------------------------------------

    models = [

        "gemini-2.5-flash",

        "gemini-2.5-flash-lite",

        "gemini-2.0-flash",

    ]

    for model in models:

        for attempt in range(2):

            try:

                print(
                    f"Calling Gemini: "
                    f"{model}, "
                    f"attempt {attempt + 1}"
                )

                response = (
                    client.models.generate_content(
                        model=model,
                        contents=prompt
                    )
                )

                if (
                    response
                    and response.text
                ):

                    result = (
                        response.text
                        .strip()
                    )

                    print(
                        "Gemini response "
                        "received:"
                    )

                    print(
                        article["title"]
                    )

                    return result

            except Exception as error:

                print(
                    f"Gemini error "
                    f"({model}): "
                    f"{error}"
                )

                if attempt == 0:

                    time.sleep(3)

    return None


# ============================================================
# OUTPUT PARSING
# ============================================================

def extract_topic(result):

    if not result:

        return "", ""

    result = result.strip()

    if (
        result.upper()
        == "NO_NEWS"
    ):

        return "", ""

    result = re.sub(
        r"```(?:text|markdown)?",
        "",
        result,
        flags=re.I
    )

    result = result.replace(
        "```",
        ""
    ).strip()

    result = re.sub(
        r"^\s*POST\s*:\s*",
        "",
        result,
        flags=re.I
    ).strip()

    match = re.search(
        r"TOPIC\s*:\s*(.+)",
        result,
        flags=re.I
    )

    if match:

        topic = (
            match.group(1)
            .strip()
        )

        post = re.sub(
            r"\n?\s*TOPIC\s*:\s*.+$",
            "",
            result,
            flags=re.I | re.S
        ).strip()

    else:

        post = result

        lines = [

            line.strip()

            for line
            in post.splitlines()

            if line.strip()

        ]

        topic = (

            lines[0][:150]

            if lines

            else "FC 27 News"

        )

        print(
            "WARNING: Gemini did not "
            "return TOPIC."
        )

    return post, topic


# ============================================================
# DUPLICATE CHECK
# ============================================================

def is_duplicate(
    topic,
    post,
    memory
):

    topic_normalized = (
        topic
        .lower()
        .strip()
    )

    post_words = set(
        re.findall(
            r"\w+",
            post.lower()
        )
    )

    for old in memory:

        old_topic = (
            old.get(
                "topic",
                ""
            )
            .lower()
            .strip()
        )

        if (
            old_topic
            and
            old_topic
            == topic_normalized
        ):

            return True

        old_post = (
            old.get(
                "post",
                ""
            )
            .lower()
        )

        old_words = set(
            re.findall(
                r"\w+",
                old_post
            )
        )

        if (
            len(post_words) >= 12
            and
            len(old_words) >= 12
        ):

            intersection = (
                post_words
                &
                old_words
            )

            similarity = (
                len(intersection)
                /
                max(
                    len(post_words),
                    len(old_words)
                )
            )

            if similarity >= 0.75:

                return True

    return False


# ============================================================
# THREADING
# ============================================================

memory_lock = threading.Lock()

inflight_links = set()

inflight_lock = threading.Lock()


# ============================================================
# PROCESS ONE NEWS
# ============================================================

def process_one_news(item):

    link = item.get(
        "link",
        ""
    )

    try:

        print("--------------------------------")

        print(
            f"WORKER: "
            f"{item['title']}"
        )

        article = fetch_article(
            item
        )

        if not article:

            print(
                "WORKER: "
                "article could not "
                "be prepared"
            )

            return

        # ----------------------------------------------------
        # MEMORY SNAPSHOT
        # ----------------------------------------------------

        with memory_lock:

            memory_snapshot = (
                load_memory()
            )

        # ----------------------------------------------------
        # GEMINI
        # ----------------------------------------------------

        result = analyze_news(
            article,
            memory_snapshot
        )

        if result is None:

            print(
                "WORKER: Gemini "
                "unavailable for:"
            )

            print(
                item["title"]
            )

            return

        if (
            result.strip().upper()
            == "NO_NEWS"
        ):

            print(
                "WORKER: NO_NEWS:"
            )

            print(
                item["title"]
            )

            return

        # ----------------------------------------------------
        # PARSE
        # ----------------------------------------------------

        post, topic = (
            extract_topic(result)
        )

        if not post:

            print(
                "WORKER: empty post"
            )

            return

        # ----------------------------------------------------
        # CHECK DUPLICATE
        # ----------------------------------------------------

        with memory_lock:

            current_memory = (
                load_memory()
            )

            if is_duplicate(
                topic,
                post,
                current_memory
            ):

                print(
                    "WORKER: duplicate "
                    f"blocked: {topic}"
                )

                return

            # ------------------------------------------------
            # SEND TELEGRAM
            # ------------------------------------------------

            send_telegram(
                post
            )

            # ------------------------------------------------
            # SAVE MEMORY
            # ------------------------------------------------

            now = int(
                time.time()
            )

            current_memory.append(

                {
                    "topic":
                        topic,

                    "post":
                        post,

                    "title":
                        item["title"],

                    "link":
                        article["url"],

                    "timestamp":
                        now,
                }

            )

            save_memory(
                current_memory
            )

        print(
            "WORKER: Telegram "
            "publication successful."
        )

        print(
            f"WORKER: Published: "
            f"{topic}"
        )

    except Exception as error:

        print(
            f"WORKER ERROR for "
            f"'{item.get('title', '')}': "
            f"{error}"
        )

    finally:

        with inflight_lock:

            inflight_links.discard(
                link
            )


# ============================================================
# FIND NEW ITEMS
# ============================================================

def submit_new_items(
    executor
):

    news = get_news()

    print(
        f"Found {len(news)} "
        "raw news items"
    )

    if not news:

        return

    with memory_lock:

        memory = load_memory()

    fresh = []

    for item in news:

        link = item.get(
            "link",
            ""
        )

        if not link:

            continue

        if memory_has_item(
            memory,
            link,
            item.get(
                "title",
                ""
            )
        ):

            continue

        with inflight_lock:

            if link in inflight_links:

                continue

        item["priority"] = (
            calculate_priority(
                item["title"]
            )
        )

        fresh.append(
            item
        )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    fresh.sort(
        key=lambda x:
            x["priority"],
        reverse=True
    )

    fresh = fresh[
        :MAX_CANDIDATES
    ]

    # --------------------------------------------------------
    # QUEUE
    # --------------------------------------------------------

    queued = []

    for item in fresh:

        link = item["link"]

        with inflight_lock:

            if link in inflight_links:

                continue

            inflight_links.add(
                link
            )

            queued.append(
                item
            )

    print(
        f"Queued {len(queued)} "
        "new items for processing"
    )

    for i, item in enumerate(
        queued,
        1
    ):

        print(
            f"{i}. "
            f"[{item['priority']}] "
            f"{item['title']}"
        )

        executor.submit(
            process_one_news,
            item
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "================================"
    )

    print(
        "LilsNews REAL-TIME "
        "monitor started"
    )

    print(
        "================================"
    )

    print(
        f"Check interval: "
        f"{CHECK_INTERVAL // 60} minutes"
    )

    print(
        f"Background workers: "
        f"{WORKER_COUNT}"
    )

    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    memory = load_memory()

    print(
        f"Memory: "
        f"{len(memory)} events"
    )

    # --------------------------------------------------------
    # TELEGRAM TEST
    # --------------------------------------------------------

    if not test_telegram():

        print(
            "Telegram connection "
            "failed. STOP."
        )

        return

    # --------------------------------------------------------
    # THREAD POOL
    # --------------------------------------------------------

    executor = (
        ThreadPoolExecutor(
            max_workers=WORKER_COUNT
        )
    )

    try:

        while True:

            try:

                submit_new_items(
                    executor
                )

            except Exception as error:

                print(
                    f"Monitor cycle "
                    f"error: {error}"
                )

            print(
                f"Next scan in "
                f"{CHECK_INTERVAL // 60} "
                "minutes...",
                flush=True
            )

            time.sleep(
                CHECK_INTERVAL
            )

    except KeyboardInterrupt:

        print(
            "LilsNews stopped "
            "by user."
        )

    finally:

        executor.shutdown(
            wait=False,
            cancel_futures=False
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
