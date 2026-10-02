import os
import json
import time
import re
import html
from urllib.parse import quote_plus, urlparse, parse_qs

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

MEMORY_FILE = "published_news.json"

client = genai.Client(api_key=GEMINI_API_KEY)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================
# SEARCH
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


def make_rss_url(query):
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=en-US&gl=US&ceid=US:en"
    )


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

    print(
        f"Telegram HTTP status: {response.status_code}"
    )

    if response.status_code != 200:
        print(
            f"Telegram response: {response.text}"
        )

    response.raise_for_status()

    print("Telegram message sent successfully.")


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
        ) as f:

            data = json.load(f)

            if isinstance(data, list):
                return data

    except Exception as e:
        print(f"Memory error: {e}")

    return []


def save_memory(memory):

    memory = memory[-200:]

    with open(
        MEMORY_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            memory,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# TEXT
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = html.unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# GOOGLE NEWS REDIRECT
# ============================================================

def decode_google_news_url(url):

    if "news.google.com/rss/articles/" not in url:
        return url

    try:

        print(
            f"Google News URL: {url}"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True
        )

        final_url = response.url

        # Иногда Google оставляет redirect page.
        # Ищем реальные ссылки на странице.
        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        candidates = []

        for a in soup.find_all("a", href=True):

            href = a.get("href")

            if not href:
                continue

            if href.startswith("http"):

                if (
                    "news.google.com" not in href
                    and "google.com" not in href
                ):
                    candidates.append(href)

        # Выбираем наиболее похожую на статью ссылку.
        if candidates:

            for candidate in candidates:

                host = urlparse(
                    candidate
                ).netloc.lower()

                if host and "google" not in host:

                    print(
                        f"REAL ARTICLE URL: "
                        f"{candidate}"
                    )

                    return candidate

        print(
            f"Final URL: {final_url}"
        )

        return final_url

    except Exception as e:

        print(
            f"Google URL decode error: {e}"
        )

        return url


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def extract_article_text(soup):

    # --------------------------------------------------------
    # JSON-LD
    # --------------------------------------------------------

    json_bodies = []

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        raw = script.string or script.get_text()

        if not raw:
            continue

        try:

            data = json.loads(raw)

            objects = []

            if isinstance(data, list):
                objects = data

            elif isinstance(data, dict):

                if "@graph" in data:
                    objects = data["@graph"]
                else:
                    objects = [data]

            for obj in objects:

                if not isinstance(obj, dict):
                    continue

                body = obj.get(
                    "articleBody"
                )

                if body:

                    body = clean_text(body)

                    if len(body) > 500:
                        json_bodies.append(body)

        except Exception:
            continue

    if json_bodies:

        return max(
            json_bodies,
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
            elements = soup.select(selector)
        except Exception:
            continue

        for element in elements:

            for bad in element.select(
                "script,style,noscript,"
                "nav,footer,header,"
                "svg,iframe,"
                ".advertisement,.ads,"
                ".social,.comments,"
                ".comment"
            ):
                bad.decompose()

            text = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 500:
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

        result = "\n".join(
            paragraphs
        )

        if len(result) >= 500:
            return result

    return ""


# ============================================================
# FETCH ARTICLE
# ============================================================

def fetch_article(url):

    try:

        real_url = decode_google_news_url(url)

        print(
            f"Fetching real article: {real_url}"
        )

        response = requests.get(
            real_url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        print(
            f"HTTP status: {response.status_code}"
        )

        if response.status_code != 200:

            print(
                "Article returned bad HTTP status."
            )

            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        title = ""

        if soup.title:

            title = clean_text(
                soup.title.get_text()
            )

        # Удаляем мусор только после
        # получения title.
        for tag in soup.select(
            "script,style,noscript,"
            "svg,iframe,nav,footer"
        ):
            tag.decompose()

        text = extract_article_text(
            soup
        )

        print(
            f"Extracted REAL article text: "
            f"{len(text)} characters"
        )

        if len(text) < 500:

            print(
                "Article content too short."
            )

            return None

        # Не перегружаем Gemini.
        if len(text) > 18000:
            text = text[:18000]

        return {
            "title": title,
            "url": response.url,
            "text": text,
        }

    except Exception as e:

        print(
            f"Article fetch error: {e}"
        )

        return None


# ============================================================
# NEWS
# ============================================================

def get_news():

    news = []
    seen = set()

    for query in SEARCH_QUERIES:

        try:

            feed = feedparser.parse(
                make_rss_url(query)
            )

            for entry in feed.entries[:10]:

                title = entry.get(
                    "title",
                    ""
                ).strip()

                link = entry.get(
                    "link",
                    ""
                ).strip()

                summary = entry.get(
                    "summary",
                    ""
                )

                if not title or not link:
                    continue

                low = title.lower()

                # Только FC27.
                if (
                    "fc 27" not in low
                    and "fc27" not in low
                ):
                    continue

                # Не брать FC25/FC26.
                if (
                    "fc 26" in low
                    or "fc26" in low
                    or "fc 25" in low
                    or "fc25" in low
                ):
                    continue

                normalized = re.sub(
                    r"[^a-z0-9]+",
                    "",
                    low
                )

                if normalized in seen:
                    continue

                seen.add(normalized)

                news.append({
                    "title": title,
                    "link": link,
                    "summary": clean_text(
                        summary
                    ),
                })

        except Exception as e:

            print(
                f"RSS error: {e}"
            )

    return news


# ============================================================
# PRIORITY
# ============================================================

def calculate_priority(title):

    t = title.lower()

    score = 0

    keywords = {
        "sbc": 150,
        "new sbc": 200,
        "card": 100,
        "cards": 100,
        "player": 80,
        "players": 80,
        "rating": 80,
        "ratings": 80,
        "meta": 120,
        "tactic": 110,
        "tactics": 110,
        "formation": 100,
        "gameplay": 90,
        "pro player": 100,
        "vejrgang": 120,
        "promo": 70,
        "team 2": 70,
        "team 1": 70,
        "leak": 60,
        "leaked": 60,
        "patch": 100,
        "update": 50,
        "objective": 80,
        "evolution": 80,
        "upgrade": 60,
    }

    for word, points in keywords.items():

        if word in t:
            score += points

    return score


def select_best_news(news):

    for item in news:

        item["priority"] = calculate_priority(
            item["title"]
        )

    news.sort(
        key=lambda x: x["priority"],
        reverse=True
    )

    return news[:15]


# ============================================================
# PREPARE ARTICLES
# ============================================================

def prepare_articles(news):

    prepared = []

    for i, item in enumerate(
        news,
        1
    ):

        print(
            "--------------------------------"
        )

        print(
            f"Reading article "
            f"{i}/{len(news)}: "
            f"{item['title']}"
        )

        article = fetch_article(
            item["link"]
        )

        if not article:

            print(
                "Could not read article."
            )

            continue

        prepared.append({
            "title": item["title"],
            "url": article["url"],
            "text": article["text"],
            "priority": item["priority"],
        })

        print(
            "Article successfully prepared."
        )

    print(
        "--------------------------------"
    )

    print(
        f"Successfully read "
        f"{len(prepared)} articles"
    )

    return prepared


# ============================================================
# GEMINI
# ============================================================

def analyze_news(articles, memory):

    if not articles:
        return None

    articles_text = ""

    for i, article in enumerate(
        articles,
        1
    ):

        articles_text += f"""

================ ARTICLE {i} ================

TITLE:
{article["title"]}

FULL ARTICLE TEXT:
{article["text"]}

ARTICLE END
==============================================
"""


    memory_text = ""

    for item in memory[-50:]:

        memory_text += f"""
OLD TOPIC:
{item.get("topic", "")}

OLD POST:
{item.get("post", "")}
"""


    prompt = f"""
You are the editor of LilsNews, a Telegram channel
about EA SPORTS FC 27 Ultimate Team.

Your job is to select ONE genuinely useful NEW piece
of information from the FULL ARTICLE TEXT provided below.

IMPORTANT:
Do NOT write a post based only on a headline.

You have the actual article text.
Use concrete information from it.

==================================================
WHAT COUNTS AS GOOD NEWS
==================================================

SBC:
- player
- rating
- position
- requirements
- price
- rewards
- expiry

CARDS:
- player names
- ratings
- positions
- card type
- important stats

META / TACTICS:
- formation
- player roles
- instructions
- width
- depth
- build-up
- defensive approach
- specific settings
- specific pro-player setup

GAMEPLAY:
- specific changes to passing
- shooting
- dribbling
- defending
- runs
- goalkeepers
- playstyles
- mechanics

PATCH:
- exact gameplay changes
- exact Ultimate Team changes
- exact fixes

LEAK:
- actual leaked player names
- ratings
- positions
- SBC/objective details
- upgrade path
- release dates

==================================================
ABSOLUTE RULE
==================================================

NEVER invent information.

If the article only says:

"Team 2 has leaked"

but does not provide actual useful details,
DO NOT publish it.

Return:

NO_NEWS

The same applies to tactics.

If an article says:
"4-4-2 is meta"

but does not provide actual useful tactical
details, do not invent player instructions,
width, depth, etc.

==================================================
DUPLICATES
==================================================

Previously published:

{memory_text}

Do not repeat the exact same information.

If an old topic appears again but the new article
contains genuinely NEW concrete information,
it can be published.

==================================================
STYLE
==================================================

Write in Russian.

Short Telegram-style post.

Approximately 40-100 words.

No source link.

No "инсайдеры сообщили" filler.

No "в сети появилась информация" filler.

No generic introduction.

Start with:

📰 LILSNEWS

Then category:

🔥 META
🃏 SBC
⚠️ СЛУХ
🎮 GAMEPLAY
🛠 ПАТЧ
🟣 PROMO
⭐ CARDS

Then a short headline.

Then concrete information.

==================================================
OUTPUT
==================================================

Return ONLY the Telegram post.

At the VERY END add:

TOPIC: short unique topic

Example:

📰 LILSNEWS

🔥 META

4-4-2 от Anders Vejrgang

В материале указано, что Vejrgang использует 4-4-2
с конкретными настройками...

TOPIC: Vejrgang 4-4-2 tactics

If there is no genuinely useful new information,
return ONLY:

NO_NEWS

==================================================

ARTICLES:

{articles_text}
"""


    models = [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]

    for model in models:

        for attempt in range(3):

            try:

                print(
                    f"Trying model: {model}, "
                    f"attempt: {attempt + 1}"
                )

                response = client.models.generate_content(
                    model=model,
                    contents=prompt
                )

                if response and response.text:

                    result = response.text.strip()

                    print(
                        "Gemini returned "
                        f"{len(result)} characters."
                    )

                    print(
                        "Gemini raw result:"
                    )

                    print(
                        result[:3000]
                    )

                    return result

            except Exception as e:

                print(
                    f"Gemini error: {e}"
                )

                if attempt < 2:
                    time.sleep(8)

    return None


# ============================================================
# EXTRACT GEMINI RESULT
# ============================================================

def extract_post_and_topic(result):

    if not result:
        return None, None

    result = result.strip()

    # --------------------------------------------------------
    # NO NEWS
    # --------------------------------------------------------

    if result.upper() == "NO_NEWS":
        return None, None

    # Убираем markdown code fences,
    # если Gemini зачем-то их добавил.
    result = re.sub(
        r"^```(?:text|markdown)?",
        "",
        result,
        flags=re.IGNORECASE
    )

    result = re.sub(
        r"```$",
        "",
        result
    ).strip()

    # --------------------------------------------------------
    # ИЩЕМ TOPIC
    # --------------------------------------------------------

    topic_match = re.search(
        r"(?im)^\s*TOPIC\s*:\s*(.+?)\s*$",
        result
    )

    if topic_match:

        topic = topic_match.group(1).strip()

        post = (
            result[:topic_match.start()]
            .strip()
        )

    else:

        # ====================================================
        # ВАЖНЫЙ FIX
        # Gemini иногда НЕ возвращает TOPIC.
        # В таком случае не блокируем публикацию.
        # ====================================================

        print(
            "WARNING: Gemini did not return TOPIC."
        )

        topic = generate_fallback_topic(
            result
        )

        post = result.strip()

    # --------------------------------------------------------
    # CLEAN
    # --------------------------------------------------------

    post = re.sub(
        r"\n{3,}",
        "\n\n",
        post
    ).strip()

    topic = re.sub(
        r"\s+",
        " ",
        topic
    ).strip()

    # --------------------------------------------------------
    # НЕ ПУБЛИКУЕМ МУСОР
    # --------------------------------------------------------

    if len(post) < 30:

        print(
            "Post is too short."
        )

        return None, None

    if len(topic) < 5:

        print(
            "Topic is too short."
        )

        return None, None

    return post, topic


# ============================================================
# FALLBACK TOPIC
# ============================================================

def generate_fallback_topic(post):

    """
    Gemini иногда забывает TOPIC.
    Не делаем второй API-запрос.
    Создаём техническую тему из текста поста.
    """

    text = post

    # Убираем служебные заголовки.
    text = re.sub(
        r"(?i)📰\s*LILSNEWS",
        "",
        text
    )

    text = re.sub(
        r"(?i)🔥\s*META|🃏\s*SBC|⚠️\s*СЛУХ|"
        r"🎮\s*GAMEPLAY|🛠\s*ПАТЧ|🟣\s*PROMO|"
        r"⭐\s*CARDS",
        "",
        text
    )

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    # Берём наиболее содержательные первые строки.
    useful = []

    for line in lines:

        if len(line) < 8:
            continue

        if line.startswith("TOPIC:"):
            continue

        useful.append(line)

        if len(useful) >= 2:
            break

    if useful:

        topic = " ".join(useful)

    else:

        topic = text[:100]

    # Нормализуем.
    topic = re.sub(
        r"[^\w\sА-Яа-яЁё0-9\-]",
        " ",
        topic
    )

    topic = re.sub(
        r"\s+",
        " ",
        topic
    ).strip()

    return topic[:160]


# ============================================================
# DUPLICATE CHECK
# ============================================================

def normalize_words(text):

    return set(
        re.findall(
            r"[a-zA-Zа-яА-ЯёЁ0-9]+",
            text.lower()
        )
    )


def is_duplicate(topic, post, memory):

    new_topic = topic.lower().strip()
    new_words = normalize_words(post)

    for old in memory:

        old_topic = (
            old.get(
                "topic",
                ""
            )
            .lower()
            .strip()
        )

        old_post = old.get(
            "post",
            ""
        )

        # Точное совпадение topic.
        if (
            old_topic
            and old_topic == new_topic
        ):

            print(
                f"Duplicate topic: {topic}"
            )

            return True

        # Проверка похожести текста.
        if len(new_words) < 10:
            continue

        old_words = normalize_words(
            old_post
        )

        if len(old_words) < 10:
            continue

        intersection = (
            len(new_words & old_words)
        )

        similarity = (
            intersection /
            max(len(new_words), 1)
        )

        if similarity >= 0.88:

            print(
                f"Duplicate post similarity: "
                f"{similarity:.2f}"
            )

            return True

    return False


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "================================"
    )

    print(
        "LilsNews started"
    )

    print(
        "================================"
    )

    memory = load_memory()

    print(
        f"Memory: {len(memory)} events"
    )


    # --------------------------------------------------------
    # NEWS
    # --------------------------------------------------------

    news = get_news()

    print(
        f"Found {len(news)} raw news items"
    )

    if not news:

        print(
            "No FC27 news found."
        )

        return


    # --------------------------------------------------------
    # PRIORITY
    # --------------------------------------------------------

    news = select_best_news(
        news
    )

    print(
        f"Selected {len(news)} "
        f"high-priority items"
    )

    print(
        "--------------------------------"
    )

    for i, item in enumerate(
        news,
        1
    ):

        print(
            f"{i}. "
            f"[{item['priority']}] "
            f"{item['title']}"
        )

    print(
        "--------------------------------"
    )


    # --------------------------------------------------------
    # ARTICLES
    # --------------------------------------------------------

    articles = prepare_articles(
        news
    )

    if not articles:

        print(
            "Could not read any articles."
        )

        return

    print(
        f"Prepared {len(articles)} "
        f"articles for Gemini"
    )


    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    result = analyze_news(
        articles,
        memory
    )

    if not result:

        print(
            "Gemini returned nothing."
        )

        return


    if result.strip().upper() == "NO_NEWS":

        print(
            "No new useful news."
        )

        return


    # --------------------------------------------------------
    # EXTRACT
    # --------------------------------------------------------

    post, topic = extract_post_and_topic(
        result
    )

    if not post:

        print(
            "Could not create post."
        )

        return

    if not topic:

        print(
            "Could not create topic."
        )

        return


    print(
        "--------------------------------"
    )

    print(
        "FINAL POST:"
    )

    print(
        post
    )

    print(
        "--------------------------------"
    )

    print(
        f"FINAL TOPIC: {topic}"
    )


    # --------------------------------------------------------
    # DUPLICATE
    # --------------------------------------------------------

    if is_duplicate(
        topic,
        post,
        memory
    ):

        print(
            "Publication blocked: duplicate."
        )

        return


    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    try:

        send_telegram(
            post
        )

    except Exception as e:

        print(
            f"TELEGRAM SEND FAILED: {e}"
        )

        return


    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    memory.append({
        "topic": topic,
        "post": post,
        "timestamp": int(
            time.time()
        )
    })

    try:

        save_memory(
            memory
        )

        print(
            "Memory saved successfully."
        )

    except Exception as e:

        print(
            f"Memory save error: {e}"
        )


    print(
        "================================"
    )

    print(
        f"Published: {topic}"
    )

    print(
        "================================"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
