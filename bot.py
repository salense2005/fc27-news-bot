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
# Required environment variables:
#   TELEGRAM_TOKEN
#   TELEGRAM_CHAT_ID
#   GEMINI_API_KEY
#
# The bot:
#   1) reads Google News RSS searches;
#   2) tries to open the real article;
#   3) if the site blocks extraction, uses RSS title/summary;
#   4) asks Gemini for ONE concrete new FC 27 news post;
#   5) sends it to Telegram;
#   6) remembers published topics/links;
#   7) keeps monitoring automatically.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

MEMORY_FILE = "published_news.json"

# Check Google News every 2 minutes
CHECK_INTERVAL = 2 * 60

# Maximum RSS articles from every search query
MAX_RSS_ITEMS_PER_QUERY = 12

# Maximum fresh articles placed into the queue
MAX_CANDIDATES = 20

# Number of simultaneous workers
WORKER_COUNT = 4

# Maximum article text sent to Gemini
MAX_ARTICLE_CHARS = 16000


# ============================================================
# GEMINI
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY)

# Current stable Gemini model
GEMINI_MODEL = "gemini-3.8-flash"


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
]


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

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
        raise RuntimeError(f"Telegram API error: {data}")


def test_telegram():
    try:
        send_telegram(
            "🤖 LilsNews запущен.\n\n"
            "Мониторинг EA FC 27 активен."
        )

        print("Telegram test message sent successfully.")
        return True

    except Exception as error:
        print(f"Telegram test failed: {error}")
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

            return data if isinstance(data, list) else []

    except Exception as error:
        print(f"Memory error: {error}")
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


def memory_has_item(memory, link="", title=""):

    link = (link or "").strip().lower()

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
            (old.get("title") or "").strip().lower()
        )

        if link and old_link and link == old_link:
            return True

        if title and old_title and title == old_title:
            return True

    return False


# ============================================================
# TEXT / HTML
# ============================================================

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


def is_fc27_title(title):

    low = title.lower()

    # Never mix older FC games into the feed.
    if any(
        x in low
        for x in [
            "fc 26",
            "fc26",
            "fc 25",
            "fc25",
            "fifa 26",
            "fifa 25",
        ]
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
        + "&hl=en-US&gl=US&ceid=US:en"
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

                if not title or not link:
                    continue

                if not is_fc27_title(title):
                    continue

                # Deduplicate stories appearing
                # in multiple searches.
                normalized = re.sub(
                    r"[^a-z0-9]+",
                    "",
                    title.lower()
                )

                if normalized in seen:
                    continue

                seen.add(normalized)

                news.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": entry.get(
                        "published",
                        ""
                    ),
                })

        except Exception as error:

            print(
                f"RSS error for '{query}': {error}"
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
        "vejrgang": 130,
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
# ARTICLE EXTRACTION
# ============================================================

def extract_article_text(soup):

    # --------------------------------------------------------
    # 1. JSON-LD articleBody
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

                graph = data.get("@graph")

                if isinstance(graph, list):
                    objects = graph
                else:
                    objects = [data]

            for obj in objects:

                if isinstance(obj, dict):

                    body = obj.get(
                        "articleBody"
                    )

                    if body:

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
    # 2. Common article containers
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
    # 3. Paragraph fallback
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
    # 4. Meta description
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
# GOOGLE NEWS REDIRECT
# ============================================================

def decode_google_news_url(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True,
        )

        final_url = response.url or ""

        if (
            final_url
            and "news.google.com/rss/articles/"
            not in final_url
        ):
            return final_url

    except Exception as error:

        print(
            f"Google News decode error: {error}"
        )

    return url


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
            f"REAL ARTICLE URL: {real_url}"
        )


    # --------------------------------------------------------
    # Try resolved article
    # --------------------------------------------------------

    try:

        response = requests.get(
            real_url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True,
        )

        print(
            f"Final URL: {response.url}"
        )

        print(
            f"HTTP status: {response.status_code}"
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
            f"Article fetch error: {error}"
        )


    # --------------------------------------------------------
    # RSS fallback
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
# GEMINI ANALYSIS
# ============================================================

def analyze_news(article, memory):

    if not article:
        return None

    memory_text = "\n".join(
        f"TOPIC: {x.get('topic', '')}\n"
        f"TITLE: {x.get('title', '')}"
        for x in memory[-80:]
    )


    prompt = f"""
Ты главный редактор Telegram-канала LilsNews
по EA SPORTS FC 27.

Обработай ЭТУ ОДНУ НОВОСТЬ и подготовь
подробный пост для игрока EA FC 27
Ultimate Team.

ПРАВИЛА:

- Используй ТОЛЬКО факты из материала.
- НИЧЕГО не придумывай.
- Если в статье нет OVR, цены, требований SBC,
  PlayStyles, дат или других данных —
  НЕ ДОБАВЛЯЙ их от себя.
- Если материал является RSS fallback
  и содержит мало информации, не выдумывай
  подробности.
- Не повторяй тему, которая уже опубликована.
- Сохраняй конкретные цифры, имена, даты,
  названия карт, SBC и условия, если они
  присутствуют в материале.
- Пост должен быть содержательным.
- Обычно 120–300 слов.
- Если информации мало — пост должен быть
  короче, а не выдумывать детали.
- Пиши на русском языке.
- Не используй фразы вроде
  "по слухам", если источник прямо
  не описывает это как слух или утечку.
- В конце ОБЯЗАТЕЛЬНО добавь:
  TOPIC: уникальная тема этой новости

ФОРМАТ:

📰 LILSNEWS

[КАТЕГОРИЯ]

[ЗАГОЛОВОК]

[ПОДРОБНОСТИ:
что произошло, какие игроки/карты/SBC,
OVR, цены, требования, даты, апгрейды,
статы и т.д. — только если это есть
в материале]

Источник: {article['url']}

TOPIC: уникальная тема новости


УЖЕ ОПУБЛИКОВАНО:

{memory_text}


МАТЕРИАЛ:

ЗАГОЛОВОК:
{article['title']}

ТИП:
{article['source_type']}

ТЕКСТ:
{article['text']}
"""


    try:

        print(
            f"Calling Gemini: {GEMINI_MODEL}"
        )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        if response and response.text:

            result = response.text.strip()

            print(
                "Gemini response received: "
                f"{article['title']}"
            )

            return result

        print(
            "Gemini returned empty response."
        )

    except Exception as error:

        print(
            f"Gemini error: {error}"
        )

    return None


# ============================================================
# OUTPUT PARSING
# ============================================================

def extract_topic(result):

    if not result:
        return "", ""

    result = result.strip()

    if result.upper() == "NO_NEWS":
        return "", ""

    result = re.sub(
        r"```(?:text|markdown)?",
        "",
        result,
        flags=re.I,
    )

    result = result.replace(
        "```",
        ""
    ).strip()

    result = re.sub(
        r"^\s*POST\s*:\s*",
        "",
        result,
        flags=re.I,
    ).strip()


    match = re.search(
        r"TOPIC\s*:\s*(.+)",
        result,
        flags=re.I,
    )

    if match:

        topic = match.group(1).strip()

        post = re.sub(
            r"\n?\s*TOPIC\s*:\s*.+$",
            "",
            result,
            flags=re.I | re.S,
        ).strip()

    else:

        post = result

        lines = [
            line.strip()
            for line in post.splitlines()
            if line.strip()
        ]

        topic = (
            lines[0][:150]
            if lines
            else "FC 27 News"
        )

        print(
            "WARNING: Gemini did not return TOPIC; "
            "generated one automatically."
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
        topic.lower().strip()
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
            and old_topic == topic_normalized
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
            and len(old_words) >= 12
        ):

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

            if similarity >= 0.75:
                return True

    return False


# ============================================================
# REAL-TIME MONITORING
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
            f"WORKER: {item['title']}"
        )


        # ----------------------------------------------------
        # Fetch
        # ----------------------------------------------------

        article = fetch_article(
            item
        )

        if not article:

            print(
                "WORKER: article could "
                "not be prepared"
            )

            return


        # ----------------------------------------------------
        # Memory snapshot
        # ----------------------------------------------------

        with memory_lock:

            memory_snapshot = (
                load_memory()
            )


        # ----------------------------------------------------
        # Gemini
        # ----------------------------------------------------

        result = analyze_news(
            article,
            memory_snapshot
        )

        if result is None:

            print(
                "WORKER: Gemini unavailable "
                f"for: {item['title']}"
            )

            return


        if (
            result.strip().upper()
            == "NO_NEWS"
        ):

            print(
                f"WORKER: NO_NEWS: "
                f"{item['title']}"
            )

            return


        # ----------------------------------------------------
        # Parse post
        # ----------------------------------------------------

        post, topic = extract_topic(
            result
        )

        if not post:

            print(
                "WORKER: empty post"
            )

            return


        # ----------------------------------------------------
        # Duplicate protection
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
                    "WORKER: duplicate blocked: "
                    f"{topic}"
                )

                return


            # ------------------------------------------------
            # Telegram publication
            # ------------------------------------------------

            send_telegram(
                post
            )


            # ------------------------------------------------
            # Save memory
            # ------------------------------------------------

            now = int(
                time.time()
            )

            current_memory.append({

                "topic": topic,

                "post": post,

                "title":
                    item["title"],

                "link":
                    article["url"],

                "timestamp":
                    now,
            })

            save_memory(
                current_memory
            )


        print(
            "WORKER: Telegram "
            "publication successful."
        )

        print(
            f"WORKER: Published: {topic}"
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
# SUBMIT NEW ITEMS
# ============================================================

def submit_new_items(
    executor
):

    news = get_news()

    print(
        f"Found {len(news)} raw news items"
    )

    if not news:
        return


    # --------------------------------------------------------
    # Current memory
    # --------------------------------------------------------

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
    # Highest priority first
    # --------------------------------------------------------

    fresh.sort(
        key=lambda x: x["priority"],
        reverse=True
    )

    fresh = fresh[
        :MAX_CANDIDATES
    ]


    # --------------------------------------------------------
    # Queue
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
        "new items for immediate processing"
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

    print("================================")
    print("LilsNews REAL-TIME monitor started")
    print("================================")

    print(
        f"Gemini model: {GEMINI_MODEL}"
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
    # Memory
    # --------------------------------------------------------

    memory = load_memory()

    print(
        f"Memory: {len(memory)} events"
    )


    # --------------------------------------------------------
    # Telegram test
    # --------------------------------------------------------

    if not test_telegram():

        print(
            "Telegram connection failed. STOP."
        )

        return


    # --------------------------------------------------------
    # Workers
    # --------------------------------------------------------

    executor = ThreadPoolExecutor(
        max_workers=WORKER_COUNT
    )


    try:

        while True:

            try:

                submit_new_items(
                    executor
                )

            except Exception as error:

                print(
                    f"Monitor cycle error: "
                    f"{error}"
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
            "LilsNews stopped by user."
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
