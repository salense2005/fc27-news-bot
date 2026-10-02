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
#   3) if the site blocks extraction, uses the RSS title/summary
#      instead of silently dropping the news;
#   4) asks Gemini for ONE concrete new FC 27 news post;
#   5) sends it to Telegram;
#   6) remembers published topics/links;
#   7) keeps monitoring automatically.
# ============================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

MEMORY_FILE = "published_news.json"
CHECK_INTERVAL = 2 * 60           # check every 2 minutes
MAX_RSS_ITEMS_PER_QUERY = 12
MAX_CANDIDATES = 20
MAX_ARTICLES_TO_FETCH = 12
MAX_ARTICLE_CHARS = 16000
WORKER_COUNT = 4

client = genai.Client(api_key=GEMINI_API_KEY)

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
        send_telegram("🤖 LilsNews запущен.\n\nМониторинг EA FC 27 активен.")
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
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, list) else []
    except Exception as error:
        print(f"Memory error: {error}")
        return []


def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as file:
        json.dump(memory[-300:], file, ensure_ascii=False, indent=2)


def memory_has_item(memory, link="", title=""):
    link = (link or "").strip().lower()
    title = re.sub(r"\s+", " ", (title or "").strip().lower())

    for old in memory:
        old_link = (old.get("link") or "").strip().lower()
        old_title = re.sub(
            r"\s+", " ", (old.get("title") or "").strip().lower()
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
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_fc27_title(title):
    low = title.lower()

    # Never mix older FC games into the feed.
    if any(x in low for x in [
        "fc 26", "fc26", "fc 25", "fc25", "fifa 26", "fifa 25"
    ]):
        return False

    return "fc 27" in low or "fc27" in low


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
            feed = feedparser.parse(make_rss_url(query))

            for entry in feed.entries[:MAX_RSS_ITEMS_PER_QUERY]:
                title = clean_text(entry.get("title", ""))
                link = (entry.get("link", "") or "").strip()
                summary = clean_text(
                    entry.get("summary", "")
                    or entry.get("description", "")
                )

                if not title or not link:
                    continue

                if not is_fc27_title(title):
                    continue

                # Google News sometimes returns the same story from
                # multiple searches. Deduplicate by normalized title.
                normalized = re.sub(r"[^a-z0-9]+", "", title.lower())
                if normalized in seen:
                    continue

                seen.add(normalized)

                news.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": entry.get("published", ""),
                })

        except Exception as error:
            print(f"RSS error for '{query}': {error}")

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

    return sum(value for key, value in points.items() if key in low)


def select_best_news(news, memory):
    fresh = []

    for item in news:
        if memory_has_item(memory, item["link"], item["title"]):
            continue

        item["priority"] = calculate_priority(item["title"])
        fresh.append(item)

    fresh.sort(key=lambda x: x["priority"], reverse=True)
    return fresh[:MAX_CANDIDATES]


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def extract_article_text(soup):
    # 1) JSON-LD articleBody
    bodies = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or script.get_text()
            data = json.loads(raw)

            objects = []
            if isinstance(data, list):
                objects = data
            elif isinstance(data, dict):
                graph = data.get("@graph")
                objects = graph if isinstance(graph, list) else [data]

            for obj in objects:
                if isinstance(obj, dict):
                    body = obj.get("articleBody")
                    if body:
                        body = clean_text(body)
                        if len(body) >= 300:
                            bodies.append(body)

        except Exception:
            pass

    if bodies:
        return max(bodies, key=len)

    # 2) Common article containers
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
            elements = soup.select(selector)
        except Exception:
            continue

        for element in elements:
            copy = BeautifulSoup(str(element), "html.parser")

            for bad in copy.select(
                "script,style,noscript,nav,footer,header,"
                ".advertisement,.ads,.social,.comments,.comment"
            ):
                bad.decompose()

            text = clean_text(copy.get_text(" ", strip=True))

            if len(text) >= 300:
                candidates.append(text)

    if candidates:
        return max(candidates, key=len)

    # 3) Paragraph fallback
    paragraphs = []

    for p in soup.find_all("p"):
        text = clean_text(p.get_text(" ", strip=True))
        if len(text) >= 40:
            paragraphs.append(text)

    if paragraphs:
        text = "\n".join(paragraphs)
        if len(text) >= 300:
            return text

    # 4) Meta description fallback
    for attrs in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ]:
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return clean_text(tag["content"])

    return ""


def decode_google_news_url(url):
    """
    Google News redirect decoding.

    Important: we do NOT consider failure here fatal.
    If Google keeps the RSS URL, the caller can still use
    the RSS title/summary as a fallback.
    """
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True,
        )

        final_url = response.url or ""

        if final_url and "news.google.com/rss/articles/" not in final_url:
            return final_url

    except Exception as error:
        print(f"Google News decode error: {error}")

    return url


def fetch_article(item):
    """
    Returns an article object even when full article extraction fails.

    This is the key fix: previously a Google News redirect/extraction
    failure caused the item to be discarded completely. Now the bot
    falls back to RSS title + summary.
    """
    source_url = item["link"]
    rss_title = item["title"]
    rss_summary = item.get("summary", "")

    print(f"Fetching: {source_url}")

    real_url = decode_google_news_url(source_url)

    if real_url != source_url:
        print(f"REAL ARTICLE URL: {real_url}")

    # Try the resolved article first.
    try:
        response = requests.get(
            real_url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True,
        )

        print(f"Final URL: {response.url}")
        print(f"HTTP status: {response.status_code}")

        if response.status_code == 200:
            content_type = response.headers.get("content-type", "").lower()

            if "text/html" in content_type:
                soup = BeautifulSoup(response.text, "html.parser")

                # Keep JSON-LD until extract_article_text() has read it.
                title = (
                    clean_text(soup.title.get_text())
                    if soup.title
                    else rss_title
                )

                text = extract_article_text(soup)

                if len(text) >= 500:
                    print(
                        f"Full article extracted: {len(text)} characters"
                    )
                    return {
                        "title": rss_title,
                        "url": response.url,
                        "text": text[:MAX_ARTICLE_CHARS],
                        "source_type": "full_article",
                        "priority": item.get("priority", 0),
                    }

                print(
                    f"Article text too short ({len(text)} chars). "
                    "Using RSS fallback."
                )

    except Exception as error:
        print(f"Article fetch error: {error}")

    # RSS fallback.
    fallback = clean_text(rss_summary)

    # Google News descriptions can sometimes contain only a tiny snippet.
    # That is still useful together with the title, but tell Gemini
    # explicitly that it must not invent missing details.
    if len(fallback) < 20:
        fallback = "No usable RSS summary was provided."

    print(
        f"Using RSS fallback: title + {len(fallback)} summary characters"
    )

    return {
        "title": rss_title,
        "url": real_url if real_url != source_url else source_url,
        "text": fallback[:5000],
        "source_type": "rss_fallback",
        "priority": item.get("priority", 0),
    }


def prepare_articles(news):
    prepared = []

    for i, item in enumerate(news, 1):
        print("--------------------------------")
        print(
            f"Reading article {i}/{len(news)}: "
            f"{item['title']}"
        )

        article = fetch_article(item)

        if article:
            prepared.append({
                "title": article["title"],
                "url": article["url"],
                "text": article["text"],
                "priority": article["priority"],
                "source_type": article["source_type"],
            })
            print(
                "Article prepared "
                f"({article['source_type']})."
            )
        else:
            print("Could not prepare article.")

    print("--------------------------------")
    print(f"Prepared {len(prepared)} usable news items")
    return prepared


# ============================================================
# GEMINI
# ============================================================

def analyze_news(articles, memory):
    """Turn ONE article into ONE detailed Telegram post.

    A single article is processed independently so that a slow/broken
    article or Gemini request does not hold up the rest of the monitor.
    """
    if not articles:
        return None

    article = articles[0]
    memory_text = "\n".join(
        f"TOPIC: {x.get('topic','')}\nTITLE: {x.get('title','')}"
        for x in memory[-80:]
    )

    prompt = f"""
Ты главный редактор Telegram-канала LilsNews по EA SPORTS FC 27.

Обработай ЭТУ ОДНУ НОВОСТЬ и подготовь подробный пост для игрока EA FC 27 Ultimate Team.

ПРАВИЛА:
- Используй ТОЛЬКО факты из материала.
- НИЧЕГО не придумывай. Если в статье нет OVR, цены, требований SBC,
  PlayStyles, дат или других данных — НЕ ДОБАВЛЯЙ их от себя.
- Если материал неполный/RSS fallback, честно укажи, что подробности
  в источнике недоступны из полученного текста.
- Не повторяй тему, которая уже опубликована.
- Не делай рейтинг источников и не выдумывай мнение автора.
- Сохраняй конкретные цифры, имена, даты, названия карт/SBC и условия,
  если они есть в материале.
- Пост должен быть содержательным, обычно 120–300 слов, но не растягивай
  его, если в источнике мало информации.
- Пиши на русском.
- В конце обязательно добавь строку TOPIC: уникальная тема этой новости.

ФОРМАТ:
📰 LILSNEWS

[КАТЕГОРИЯ]

[ЗАГОЛОВОК]

[ПОДРОБНОСТИ: что произошло, какие игроки/карты/SBC, OVR, цены,
требования, даты, апгрейды, статы и т.д. — только если это есть в тексте]

Источник: {article['url']}

TOPIC: уникальная тема новости

УЖЕ ОПУБЛИКОВАНО:
{memory_text}

МАТЕРИАЛ:
ЗАГОЛОВОК: {article['title']}
ТИП: {article['source_type']}
ТЕКСТ:
{article['text']}
"""

    models = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
    ]

    for model in models:
        for attempt in range(2):
            try:
                print(f"Calling Gemini: {model}, attempt {attempt + 1}")
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                if response and response.text:
                    result = response.text.strip()
                    print(f"Gemini response received: {article['title']}")
                    return result
            except Exception as error:
                print(f"Gemini error ({model}): {error}")
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

    if result.upper() == "NO_NEWS":
        return "", ""

    result = re.sub(
        r"```(?:text|markdown)?",
        "",
        result,
        flags=re.I,
    )
    result = result.replace("```", "").strip()

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


def is_duplicate(topic, post, memory):
    topic_normalized = topic.lower().strip()
    post_words = set(
        re.findall(r"\w+", post.lower())
    )

    for old in memory:
        old_topic = (
            old.get("topic", "")
            .lower()
            .strip()
        )

        if old_topic and old_topic == topic_normalized:
            return True

        old_post = old.get("post", "").lower()
        old_words = set(
            re.findall(r"\w+", old_post)
        )

        if (
            len(post_words) >= 12
            and len(old_words) >= 12
        ):
            intersection = post_words & old_words
            similarity = (
                len(intersection)
                / max(len(post_words), len(old_words))
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


def process_one_news(item):
    """Fetch, analyze and publish one news item independently."""
    link = item.get("link", "")

    try:
        print("--------------------------------")
        print(f"WORKER: {item['title']}")

        article = fetch_article(item)
        if not article:
            print("WORKER: article could not be prepared")
            return

        with memory_lock:
            memory_snapshot = load_memory()

        result = analyze_news([article], memory_snapshot)

        if result is None:
            print(f"WORKER: Gemini unavailable for: {item['title']}")
            return

        if result.strip().upper() == "NO_NEWS":
            print(f"WORKER: NO_NEWS: {item['title']}")
            return

        post, topic = extract_topic(result)
        if not post:
            print("WORKER: empty post")
            return

        with memory_lock:
            current_memory = load_memory()
            if is_duplicate(topic, post, current_memory):
                print(f"WORKER: duplicate blocked: {topic}")
                return

            send_telegram(post)

            now = int(time.time())
            current_memory.append({
                "topic": topic,
                "post": post,
                "title": item["title"],
                "link": article["url"],
                "timestamp": now,
            })
            save_memory(current_memory)

        print("WORKER: Telegram publication successful.")
        print(f"WORKER: Published: {topic}")

    except Exception as error:
        print(f"WORKER ERROR for '{item.get('title','')}': {error}")
    finally:
        with inflight_lock:
            inflight_links.discard(link)


def submit_new_items(executor):
    """Find fresh items and immediately hand them to background workers."""
    news = get_news()
    print(f"Found {len(news)} raw news items")

    if not news:
        return

    with memory_lock:
        memory = load_memory()

    fresh = []
    for item in news:
        link = item.get("link", "")
        if not link:
            continue
        if memory_has_item(memory, link, item.get("title", "")):
            continue
        with inflight_lock:
            if link in inflight_links:
                continue
        item["priority"] = calculate_priority(item["title"])
        fresh.append(item)

    fresh.sort(key=lambda x: x["priority"], reverse=True)
    fresh = fresh[:MAX_CANDIDATES]

    # Mark only the items that we actually submit, so lower-priority
    # items are still eligible on the next scan.
    queued = []
    for item in fresh:
        link = item["link"]
        with inflight_lock:
            if link in inflight_links:
                continue
            inflight_links.add(link)
            queued.append(item)

    print(f"Queued {len(queued)} new items for immediate processing")

    for i, item in enumerate(queued, 1):
        print(f"{i}. [{item['priority']}] {item['title']}")
        executor.submit(process_one_news, item)


def main():
    print("================================")
    print("LilsNews REAL-TIME monitor started")
    print("================================")
    print(f"Check interval: {CHECK_INTERVAL // 60} minutes")
    print(f"Background workers: {WORKER_COUNT}")

    memory = load_memory()
    print(f"Memory: {len(memory)} events")

    if not test_telegram():
        print("Telegram connection failed. STOP.")
        return

    executor = ThreadPoolExecutor(max_workers=WORKER_COUNT)

    try:
        while True:
            try:
                submit_new_items(executor)
            except Exception as error:
                print(f"Monitor cycle error: {error}")

            print(f"Next scan in {CHECK_INTERVAL // 60} minutes...", flush=True)
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("LilsNews stopped by user.")
    finally:
        executor.shutdown(wait=False, cancel_futures=False)


if __name__ == "__main__":
    main()

