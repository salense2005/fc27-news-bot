import os
import json
import time
import re
import html
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai
from googlenewsdecoder import gnewsdecoder


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
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
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
]


# ============================================================
# GOOGLE NEWS RSS
# ============================================================

def make_rss_url(query):

    encoded = quote_plus(query)

    return (
        "https://news.google.com/rss/search?"
        f"q={encoded}&hl=en-US&gl=US&ceid=US:en"
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

    response.raise_for_status()


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

    except Exception as error:

        print(f"Memory error: {error}")

    return []


def save_memory(memory):

    memory = memory[-200:]

    with open(
        MEMORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            memory,
            file,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# CLEAN TEXT
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
# EXTRACT ARTICLE TEXT
# ============================================================

def extract_article_text(soup):

    # --------------------------------------------------------
    # JSON-LD articleBody
    # --------------------------------------------------------

    scripts = soup.find_all(
        "script",
        type="application/ld+json"
    )

    json_bodies = []

    for script in scripts:

        raw = script.string or script.get_text()

        if not raw:
            continue

        try:

            data = json.loads(raw)

            if isinstance(data, list):
                objects = data

            elif isinstance(data, dict):

                if "@graph" in data:
                    objects = data["@graph"]
                else:
                    objects = [data]

            else:
                objects = []

            for obj in objects:

                if not isinstance(obj, dict):
                    continue

                body = obj.get("articleBody")

                if body:

                    body = clean_text(body)

                    if len(body) >= 500:
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
                "iframe,svg,"
                ".advertisement,.ads,"
                ".social,.comments,.comment"
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
    # PARAGRAPH FALLBACK
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

        text = "\n".join(paragraphs)

        if len(text) >= 500:
            return text

    return ""


# ============================================================
# GOOGLE NEWS DECODER
# ============================================================

def decode_google_news_url(url):

    if "news.google.com" not in url:
        return url

    print("Decoding Google News URL...")

    try:

        result = gnewsdecoder(
            url,
            interval=1,
            timeout=20
        )

        if not result.get("success"):

            print(
                "Google News decoder failed:"
            )

            print(
                result.get(
                    "message",
                    "Unknown error"
                )
            )

            return None

        decoded_url = result.get(
            "decoded_url"
        )

        if not decoded_url:

            print(
                "Decoder returned empty URL."
            )

            return None

        print(
            f"REAL ARTICLE URL: {decoded_url}"
        )

        return decoded_url

    except Exception as error:

        print(
            f"Google News decoder error: "
            f"{error}"
        )

        return None


# ============================================================
# FETCH REAL ARTICLE
# ============================================================

def fetch_article(url):

    try:

        print(
            f"Google News URL: {url}"
        )

        # ----------------------------------------------------
        # GOOGLE NEWS -> REAL WEBSITE
        # ----------------------------------------------------

        real_url = decode_google_news_url(url)

        if not real_url:

            print(
                "Could not decode Google News URL."
            )

            return None

        # ----------------------------------------------------
        # FETCH REAL WEBSITE
        # ----------------------------------------------------

        response = requests.get(
            real_url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        final_url = response.url

        print(
            f"Final URL: {final_url}"
        )

        print(
            f"HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "Article returned bad HTTP status."
            )

            return None

        content_type = response.headers.get(
            "content-type",
            ""
        ).lower()

        if (
            "text/html" not in content_type
            and "application/xhtml" not in content_type
        ):

            print(
                f"Not HTML: {content_type}"
            )

            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # ----------------------------------------------------
        # TITLE
        # ----------------------------------------------------

        title = ""

        if soup.title:

            title = clean_text(
                soup.title.get_text()
            )

        # ----------------------------------------------------
        # REMOVE JUNK
        # ----------------------------------------------------

        for tag in soup.select(
            "script,style,noscript,"
            "svg,iframe,nav,footer,header,form"
        ):

            tag.decompose()

        # ----------------------------------------------------
        # EXTRACT ARTICLE
        # ----------------------------------------------------

        text = extract_article_text(
            soup
        )

        if not text:

            print(
                "No article text found."
            )

            return None

        print(
            f"Extracted REAL article text: "
            f"{len(text)} characters"
        )

        # ----------------------------------------------------
        # HARD CHECK
        # ----------------------------------------------------

        if len(text) < 700:

            print(
                "Real article content too short. "
                "Skipping."
            )

            return None

        # ----------------------------------------------------
        # LIMIT SIZE
        # ----------------------------------------------------

        if len(text) > 20000:

            text = text[:20000]

        return {
            "title": title,
            "url": final_url,
            "text": text,
        }

    except Exception as error:

        print(
            f"Article fetch error: {error}"
        )

        return None


# ============================================================
# GET NEWS
# ============================================================

def get_news():

    news = []
    seen = set()

    for query in SEARCH_QUERIES:

        rss_url = make_rss_url(query)

        try:

            feed = feedparser.parse(
                rss_url
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

                title_lower = title.lower()

                # ------------------------------------------------
                # ONLY FC27
                # ------------------------------------------------

                if (
                    "fc 27" not in title_lower
                    and "fc27" not in title_lower
                ):
                    continue

                # ------------------------------------------------
                # REMOVE OLD GAMES
                # ------------------------------------------------

                if (
                    "fc 26" in title_lower
                    or "fc26" in title_lower
                    or "fc 25" in title_lower
                    or "fc25" in title_lower
                ):
                    continue

                # ------------------------------------------------
                # DUPLICATE TITLES
                # ------------------------------------------------

                normalized = (
                    title_lower
                    .replace(" ", "")
                    .replace("-", "")
                    .replace(":", "")
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

        except Exception as error:

            print(
                f"RSS error: {error}"
            )

    return news


# ============================================================
# PRIORITY
# ============================================================

def calculate_priority(title):

    title = title.lower()

    score = 0

    keywords = {
        "sbc": 150,
        "new sbc": 50,
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
        "patch": 90,
        "update": 50,
        "objective": 80,
        "evolution": 80,
        "upgrade": 60,
    }

    for keyword, points in keywords.items():

        if keyword in title:
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
        start=1
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

def analyze_news(
    articles,
    memory
):

    if not articles:
        return None

    articles_text = ""

    for i, article in enumerate(
        articles,
        start=1
    ):

        articles_text += f"""
==================================================
МАТЕРИАЛ {i}
==================================================

ЗАГОЛОВОК:
{article["title"]}

URL:
{article["url"]}

ПОЛНЫЙ ТЕКСТ:
{article["text"]}

==================================================
"""


    memory_text = ""

    for item in memory[-50:]:

        memory_text += f"""
TOPIC:
{item.get("topic", "")}

POST:
{item.get("post", "")}

==================================================
"""


    prompt = f"""
Ты главный редактор Telegram-канала LilsNews
по EA SPORTS FC 27.

Твоя задача — делать короткие, КОНКРЕТНЫЕ
новостные посты для игроков EA FC 27 Ultimate Team.

Ниже тебе переданы реальные тексты статей.

==================================================
КРИТИЧЕСКОЕ ПРАВИЛО
==================================================

НИКОГДА НЕ ПИШИ ПОСТ ТОЛЬКО ПО ЗАГОЛОВКУ.

Используй ТОЛЬКО информацию,
которая реально присутствует
в ПОЛНОМ ТЕКСТЕ статьи.

Если статья говорит:

"утекли карты Team 2"

но текст не содержит игроков,
рейтингов или другой конкретики,

НЕ ПЫТАЙСЯ ДОГАДЫВАТЬСЯ.

Ответ:

NO_NEWS

==================================================
META / ТАКТИКИ
==================================================

Если статья посвящена META,
формации или тактикам,
обязательно ищи конкретные значения:

• формация
• роли
• инструкции
• ширина
• глубина
• build-up
• defensive approach
• player roles
• конкретный pro player
• конкретные настройки

Если статья содержит:

4-4-2
Width 45
Depth 65
ST Get In Behind

можно написать эти данные.

Если статья содержит только:

"4-4-2 is one of the best formations"

это НЕ достаточно.

В таком случае:

NO_NEWS

НИКОГДА НЕ ПРИДУМЫВАЙ
настройки самостоятельно.

==================================================
SBC
==================================================

Если статья о SBC,
показывай конкретику:

• имя игрока
• OVR
• позиция
• цена
• требования
• награды
• срок

Только если информация есть.

==================================================
КАРТЫ
==================================================

Показывай:

• игрок
• OVR
• позиция
• тип карты
• характеристики

Только если есть в тексте.

==================================================
GAMEPLAY
==================================================

Ищи конкретные изменения:

• shots
• passing
• dribbling
• defending
• goalkeeper
• runs
• playstyles
• mechanics

==================================================
PATCH
==================================================

Не пиши:

"EA выпустила патч."

Показывай конкретные изменения:

• что исправили
• что изменили
• что усилили
• что ослабили

==================================================
LEAK
==================================================

Если это leak,
показывай конкретные данные:

Игрок — OVR
Игрок — OVR
Игрок — OVR

или конкретные SBC,
Objectives,
Upgrade Path и т.д.

==================================================
DUPLICATES
==================================================

Уже опубликованные новости:

{memory_text}

Не публикуй ту же информацию повторно.

Но если старая тема получила новые конкретные данные,
её можно публиковать снова.

==================================================
ПРИОРИТЕТ
==================================================

1. Новый SBC
2. Новые конкретные карты
3. META / тактика с конкретными настройками
4. Информация от pro player
5. Gameplay
6. Patch
7. Promo
8. Leak с конкретными данными
9. Objectives
10. Evolutions

==================================================
ЗАПРЕЩЕНО
==================================================

❌ "инсайдеры сообщили"
❌ "в сети появилась информация"
❌ "ожидается релиз"
❌ "стало известно"
❌ вода
❌ пересказ заголовка
❌ придумывание игроков
❌ придумывание рейтингов
❌ придумывание цен
❌ придумывание тактик
❌ придумывание META
❌ выдавать старую информацию за новую

==================================================
ДЛИНА
==================================================

30–100 слов.

Лучше коротко и конкретно.

==================================================
ИСТОЧНИК
==================================================

Источник НЕ показывай.

Ссылку НЕ добавляй.

==================================================
ФОРМАТ
==================================================

📰 LILSNEWS

[КАТЕГОРИЯ]

[КОРОТКИЙ ЗАГОЛОВОК]

[КОНКРЕТНАЯ ИНФОРМАЦИЯ]

В конце:

TOPIC: уникальная тема новости

==================================================
ЕСЛИ НЕТ КОНКРЕТНОЙ НОВОЙ ИНФОРМАЦИИ
==================================================

Ответь строго:

NO_NEWS

==================================================

МАТЕРИАЛЫ:

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

                    return response.text.strip()

            except Exception as error:

                print(
                    f"Gemini error: {error}"
                )

                if attempt < 2:
                    time.sleep(8)

    return None


# ============================================================
# EXTRACT TOPIC
# ============================================================

def extract_topic(result):

    lines = result.splitlines()

    post_lines = []
    topic = ""

    for line in lines:

        if line.strip().upper().startswith(
            "TOPIC:"
        ):

            topic = line.split(
                ":",
                1
            )[1].strip()

        else:

            post_lines.append(line)

    post = "\n".join(
        post_lines
    ).strip()

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

    post_normalized = (
        post.lower().strip()
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

        old_post = (
            old.get(
                "post",
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

        if (
            old_post
            and len(post_normalized) > 50
        ):

            old_words = set(
                re.findall(
                    r"\b\w+\b",
                    old_post
                )
            )

            new_words = set(
                re.findall(
                    r"\b\w+\b",
                    post_normalized
                )
            )

            if not new_words:
                continue

            similarity = (
                len(old_words & new_words)
                /
                len(new_words)
            )

            if similarity > 0.85:
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
    # SEARCH
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

    news = select_best_news(news)

    print(
        f"Selected {len(news)} "
        f"high-priority items"
    )

    print(
        "--------------------------------"
    )

    for i, item in enumerate(
        news,
        start=1
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
    # REAL ARTICLES
    # --------------------------------------------------------

    articles = prepare_articles(news)

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

    if result is None:

        print(
            "Gemini unavailable."
        )

        return

    # --------------------------------------------------------
    # NO NEWS
    # --------------------------------------------------------

    if result.strip() == "NO_NEWS":

        print(
            "No new important news."
        )

        return

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    post, topic = extract_topic(result)

    if not post:

        print(
            "Empty post."
        )

        return

    if not topic:

        print(
            "No topic returned."
        )

        return

    # --------------------------------------------------------
    # DUPLICATE
    # --------------------------------------------------------

    if is_duplicate(
        topic,
        post,
        memory
    ):

        print(
            f"Duplicate blocked: {topic}"
        )

        return

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    send_telegram(post)

    # --------------------------------------------------------
    # MEMORY
    # --------------------------------------------------------

    memory.append({
        "topic": topic,
        "post": post,
        "timestamp": int(time.time())
    })

    save_memory(memory)

    print(
        "================================"
    )

    print(
        f"Published: {topic}"
    )

    print(
        "================================"
    )


if __name__ == "__main__":
    main()
