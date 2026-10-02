import os
import json
import time
import re
import html
from urllib.parse import urlparse

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
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ============================================================
# GOOGLE NEWS SEARCH
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
    """
    Правильно создаём Google News RSS URL.
    Не собираем URL вручную с пробелами.
    """

    from urllib.parse import quote_plus

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
# ARTICLE TEXT EXTRACTION
# ============================================================

def extract_article_text(soup):

    # --------------------------------------------------------
    # 1. JSON-LD ArticleBody
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

                article_body = obj.get(
                    "articleBody"
                )

                if article_body:

                    json_bodies.append(
                        clean_text(article_body)
                    )

        except Exception:
            continue

    if json_bodies:

        best = max(
            json_bodies,
            key=len
        )

        if len(best) >= 500:
            return best


    # --------------------------------------------------------
    # 2. Meta description
    # --------------------------------------------------------

    meta_parts = []

    for attr in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ]:

        tag = soup.find(
            "meta",
            attrs=attr
        )

        if tag and tag.get("content"):

            meta_parts.append(
                clean_text(tag["content"])
            )


    # --------------------------------------------------------
    # 3. Common article containers
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

            # Убираем мусор
            for bad in element.select(
                "script,style,noscript,"
                "nav,footer,header,"
                ".advertisement,.ads,.social,"
                ".comments,.comment"
            ):
                bad.decompose()

            text = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 300:

                candidates.append(text)


    if candidates:

        best = max(
            candidates,
            key=len
        )

        return best


    # --------------------------------------------------------
    # 4. Fallback: paragraphs
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
    # 5. Description only
    # --------------------------------------------------------

    if meta_parts:

        return " ".join(
            meta_parts
        )

    return ""


# ============================================================
# FETCH ARTICLE
# ============================================================

def fetch_article(url):

    try:

        print(
            f"Fetching: {url}"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True
        )

        final_url = response.url

        print(
            f"Final URL: {final_url}"
        )

        if response.status_code != 200:

            print(
                f"HTTP status: "
                f"{response.status_code}"
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

        # Удаляем очевидный мусор
        for tag in soup.select(
            "script,style,noscript,"
            "svg,iframe,nav,footer"
        ):

            tag.decompose()

        title = ""

        if soup.title:
            title = clean_text(
                soup.title.get_text()
            )

        text = extract_article_text(
            soup
        )

        if not text:

            print(
                "No article text found."
            )

            return None

        print(
            f"Extracted text: "
            f"{len(text)} characters"
        )

        # ----------------------------------------------------
        # КРИТИЧЕСКАЯ ПРОВЕРКА
        # ----------------------------------------------------

        if len(text) < 500:

            print(
                "Article content too short. "
                "Skipping."
            )

            return None

        # Ограничиваем размер,
        # чтобы не отправлять Gemini гигантские статьи
        if len(text) > 18000:

            text = text[:18000]

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
# NEWS SEARCH
# ============================================================

def get_news():

    news = []
    seen = set()

    for query in SEARCH_QUERIES:

        rss_url = make_rss_url(
            query
        )

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

                # Старые игры сразу убираем
                if (
                    "fc 26" in title_lower
                    or "fc26" in title_lower
                    or "fc 25" in title_lower
                    or "fc25" in title_lower
                ):
                    continue

                # Только FC27
                if (
                    "fc 27" not in title_lower
                    and "fc27" not in title_lower
                ):
                    continue

                # Дубликаты
                normalized = (
                    title_lower
                    .replace(" ", "")
                    .replace("-", "")
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

    # SBC
    if "sbc" in title:
        score += 150

    if "new sbc" in title:
        score += 50

    # Cards
    if "card" in title:
        score += 100

    if "cards" in title:
        score += 100

    # Players
    if "player" in title:
        score += 80

    if "players" in title:
        score += 80

    # Ratings
    if "rating" in title:
        score += 80

    if "ratings" in title:
        score += 80

    # META
    if "meta" in title:
        score += 120

    if "tactic" in title:
        score += 110

    if "tactics" in title:
        score += 110

    if "formation" in title:
        score += 100

    if "gameplay" in title:
        score += 90

    if "pro player" in title:
        score += 100

    if "vejrgang" in title:
        score += 120

    # Promo
    if "promo" in title:
        score += 70

    if "team 2" in title:
        score += 70

    if "team 1" in title:
        score += 70

    # Leak
    if "leak" in title:
        score += 60

    if "leaked" in title:
        score += 60

    # Patch
    if "patch" in title:
        score += 90

    if "update" in title:
        score += 50

    # Objectives
    if "objective" in title:
        score += 80

    if "evolution" in title:
        score += 80

    if "upgrade" in title:
        score += 60

    return score


def select_best_news(news):

    for item in news:

        item["priority"] = (
            calculate_priority(
                item["title"]
            )
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

ТЕКСТ СТАТЬИ:
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

Твоя задача — находить ИМЕННО КОНКРЕТНУЮ
новую информацию, которая интересна игроку
EA FC 27 Ultimate Team.

У тебя есть ПОЛНЫЕ ТЕКСТЫ статей ниже.

==================================================
ГЛАВНОЕ ПРАВИЛО
==================================================

НИКОГДА НЕ ПИШИ ПОСТ ТОЛЬКО ПО ЗАГОЛОВКУ.

Используй только факты, которые реально есть
в ТЕКСТЕ СТАТЬИ.

Если в статье написано:

"утекли карты Team 2"

но дальше нет названий игроков,
рейтингов или других конкретных данных —

НЕ ПИШИ:

"утекли карты Team 2".

Это бесполезно.

В таком случае ответь:

NO_NEWS

==================================================
ЧТО МНЕ НУЖНО
==================================================

🔥 SBC

Если статья сообщает о новом SBC,
покажи конкретно:

• игрок
• OVR
• позиция
• требования
• цена
• награды
• срок

Но только то, что реально есть в тексте.

---

🃏 НОВЫЕ КАРТЫ

Покажи:

• имя игрока
• OVR
• позицию
• тип карты
• важные характеристики

Если информация есть.

---

🔥 META / ТАКТИКИ

Это ОЧЕНЬ ВАЖНО.

Если статья про новую META,
найди конкретные данные:

• формация
• роли игроков
• инструкции
• ширина
• глубина
• стиль игры
• конкретные приёмы
• конкретные настройки
• какой pro использует тактику

Например:

🔥 META 4-4-2

• ST — Get In Behind
• CM — Stay Back
• Width — 45
• Depth — 65

Только если эти значения действительно
присутствуют в статье.

НЕ ПРИДУМЫВАЙ настройки.

---

🎮 GAMEPLAY

Покажи конкретные изменения:

• удары
• пасы
• дриблинг
• защита
• забегания
• goalkeeper
• playstyles
• механики

---

🛠 ПАТЧ

Покажи именно изменения патча.

Не:

"EA выпустила новый патч."

А:

"EA изменила X,
исправила Y,
усилила Z."

Только конкретные изменения из текста.

---

⚠️ LEAK

Если это утечка,
покажи конкретику:

Игрок — OVR
Игрок — OVR
Игрок — OVR

или:

SBC
Objective
Upgrade path

если это действительно указано.

==================================================
DUPLICATES
==================================================

Ниже находится память уже опубликованных постов.

{memory_text}

Не публикуй одну и ту же информацию повторно.

ВАЖНО:

Одна тема может появиться снова,
если появились НОВЫЕ КОНКРЕТНЫЕ ДАННЫЕ.

Например:

Первый пост:
"Утек Team 2."

Второй материал:
"Haaland 91 OVR,
Diani 88 OVR,
Upamecano 88 OVR."

Второй материал МОЖНО публиковать,
потому что появилась новая информация.

Но если второй материал снова говорит:
"Team 2 leaked"

без новых данных —

NO_NEWS.

==================================================
ВЫБОР
==================================================

Выбери максимум ОДНУ новость.

Выбирай ту, которая принесёт игроку
реальную пользу прямо сейчас.

Приоритет:

1. Новый SBC
2. Новые конкретные карты
3. Новая META / тактика
4. Конкретные данные от pro player
5. Gameplay
6. Патч
7. Новая промо
8. Leak с конкретными игроками
9. Objectives
10. Evolutions

НЕ выбирай статью только потому,
что у неё громкий заголовок.

==================================================
ЗАПРЕЩЕНО
==================================================

❌ "инсайдеры сообщили" без конкретики

❌ "в сети появилась информация"

❌ "ожидается релиз"

❌ длинные вступления

❌ вода

❌ пересказ всей статьи

❌ придумывать игроков

❌ придумывать рейтинги

❌ придумывать цены

❌ придумывать SBC

❌ придумывать тактики

❌ придумывать meta

❌ выдавать старую информацию за новую

==================================================
ДЛИНА
==================================================

Обычно 30–90 слов.

Лучше 50 слов с конкретными данными,
чем 120 слов воды.

==================================================
ИСТОЧНИК
==================================================

НЕ показывай источник.

НЕ добавляй ссылку.

==================================================
ФОРМАТ
==================================================

Начало:

📰 LILSNEWS

Затем категория:

🔥 META

или

🃏 SBC

или

⚠️ СЛУХ

или

🎮 GAMEPLAY

или

🛠 ПАТЧ

или

🟣 PROMO

Затем короткий заголовок.

Затем конкретные данные.

В самом конце ОБЯЗАТЕЛЬНО:

TOPIC: уникальная тема новости

==================================================
ЕСЛИ НЕТ КОНКРЕТНОЙ НОВОЙ ИНФОРМАЦИИ
==================================================

Ответь строго:

NO_NEWS
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

                response = (
                    client.models.generate_content(
                        model=model,
                        contents=prompt
                    )
                )

                if response and response.text:

                    result = (
                        response.text
                        .strip()
                    )

                    return result

            except Exception as error:

                print(
                    f"Gemini error: {error}"
                )

                if attempt < 2:
                    time.sleep(8)

    return None


# ============================================================
# TOPIC
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
        topic
        .lower()
        .strip()
    )

    post_normalized = (
        post
        .lower()
        .strip()
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


        # Одинаковая тема
        if (
            old_topic
            and old_topic == topic_normalized
        ):

            return True


        # Проверка похожести постов
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
                len(
                    old_words & new_words
                )
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
    # GET NEWS
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
    # READ REAL ARTICLES
    # --------------------------------------------------------

    articles = prepare_articles(
        news
    )


    if not articles:

        print(
            "Could not read any articles."
        )

        # НИЧЕГО НЕ ОТПРАВЛЯЕМ В TELEGRAM.
        # Лучше пропустить цикл, чем отправить воду.

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
    # EXTRACT POST
    # --------------------------------------------------------

    post, topic = extract_topic(
        result
    )


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
            f"Duplicate blocked: "
            f"{topic}"
        )

        return


    # --------------------------------------------------------
    # PUBLISH
    # --------------------------------------------------------

    send_telegram(
        post
    )


    # --------------------------------------------------------
    # SAVE MEMORY
    # --------------------------------------------------------

    memory.append({
        "topic": topic,
        "post": post,
        "timestamp": int(
            time.time()
        )
    })


    save_memory(
        memory
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


if __name__ == "__main__":
    main()
