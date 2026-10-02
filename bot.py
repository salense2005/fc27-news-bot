import os
import json
import time
import re
import html
import requests
import feedparser
from urllib.parse import quote
from google import genai


# ============================================================
# НАСТРОЙКИ
# ============================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

MEMORY_FILE = "published_news.json"

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# RSS ИСТОЧНИКИ
# ============================================================

RSS_FEEDS = [

    "https://news.google.com/rss/search?q=EA%20FC%2027%20SBC&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20Ultimate%20Team%20cards&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20players%20leaked&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20ratings&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20promo&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20meta&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20tactics&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20gameplay&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20patch&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20Objectives&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20Evolution&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20pro%20players&hl=en-US&gl=US&ceid=US:en",
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

        print(
            f"Memory error: {error}"
        )

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
# ОЧИСТКА HTML
# ============================================================

def clean_html(text):

    if not text:
        return ""

    text = html.unescape(text)

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
# ПОЛУЧЕНИЕ НОВОСТЕЙ
# ============================================================

def get_news():

    news = []
    seen_links = set()
    seen_titles = set()

    for feed_url in RSS_FEEDS:

        try:

            feed = feedparser.parse(
                feed_url
            )

            for entry in feed.entries[:12]:

                title = entry.get(
                    "title",
                    ""
                ).strip()

                link = entry.get(
                    "link",
                    ""
                ).strip()

                description = entry.get(
                    "summary",
                    ""
                )

                description = clean_html(
                    description
                )

                if not title or not link:
                    continue

                title_lower = title.lower()

                # Старые игры не нужны
                old_game_words = [
                    "fc 26",
                    "fc26",
                    "fc 25",
                    "fc25",
                    "fifa 25",
                    "fifa 26"
                ]

                if any(
                    word in title_lower
                    for word in old_game_words
                ):
                    continue

                # Только FC27
                if (
                    "fc 27" not in title_lower
                    and "fc27" not in title_lower
                ):
                    continue

                title_key = re.sub(
                    r"[^a-z0-9а-яё]",
                    "",
                    title_lower
                )

                if link in seen_links:
                    continue

                if title_key in seen_titles:
                    continue

                seen_links.add(link)
                seen_titles.add(title_key)

                news.append({
                    "title": title,
                    "link": link,
                    "description": description,
                })

        except Exception as error:

            print(
                f"RSS error: {error}"
            )

    return news


# ============================================================
# ПРИОРИТЕТ
# ============================================================

def calculate_priority(title, description=""):

    text = (
        title + " " + description
    ).lower()

    score = 0

    # Самые важные категории
    if "sbc" in text:
        score += 150

    if "meta" in text:
        score += 140

    if "tactic" in text:
        score += 130

    if "tactics" in text:
        score += 130

    if "formation" in text:
        score += 120

    if "custom tactics" in text:
        score += 140

    if "pro player" in text:
        score += 120

    if "pro players" in text:
        score += 120

    if "gameplay" in text:
        score += 100

    if "patch" in text:
        score += 110

    if "update" in text:
        score += 60

    if "card" in text:
        score += 90

    if "cards" in text:
        score += 90

    if "player" in text:
        score += 70

    if "players" in text:
        score += 70

    if "leak" in text:
        score += 70

    if "leaked" in text:
        score += 70

    if "promo" in text:
        score += 80

    if "objective" in text:
        score += 90

    if "evolution" in text:
        score += 80

    if "ratings" in text:
        score += 70

    if "rating" in text:
        score += 70

    if "upgrade" in text:
        score += 70

    # Конкретные признаки
    if re.search(
        r"\b\d{2}\s*ovr\b",
        text
    ):
        score += 40

    if re.search(
        r"\b4-\d-\d\b",
        text
    ):
        score += 50

    if re.search(
        r"\b\d-\d-\d-\d\b",
        text
    ):
        score += 50

    # YouTube
    if "youtube.com" in text:
        score += 20

    return score


# ============================================================
# ВЫБОР ЛУЧШИХ НОВОСТЕЙ
# ============================================================

def select_best_news(news):

    for item in news:

        item["priority"] = calculate_priority(
            item["title"],
            item["description"]
        )

    news.sort(
        key=lambda x: x["priority"],
        reverse=True
    )

    # Только 8 лучших.
    # Так Gemini не получает 15 огромных статей.
    return news[:8]


# ============================================================
# ПОПЫТКА ПРОЧИТАТЬ СТРАНИЦУ
# ============================================================

def fetch_page(url):

    user_agents = [

        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0 Safari/537.36",

        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0 Safari/537.36"
    ]

    for user_agent in user_agents:

        try:

            response = requests.get(
                url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": (
                        "text/html,"
                        "application/xhtml+xml,"
                        "application/xml;q=0.9,"
                        "*/*;q=0.8"
                    ),
                    "Accept-Language":
                        "en-US,en;q=0.9",
                },
                timeout=15,
                allow_redirects=True
            )

            if response.status_code != 200:
                continue

            content = response.text

            if len(content) < 1000:
                continue

            return content

        except Exception as error:

            print(
                f"Page request error: {error}"
            )

    return ""


# ============================================================
# ИЗВЛЕЧЕНИЕ ТЕКСТА ИЗ HTML
# ============================================================

def extract_text_from_html(content):

    if not content:
        return ""

    # Удаляем скрипты / стили / SVG
    content = re.sub(
        r"<script.*?</script>",
        " ",
        content,
        flags=re.I | re.S
    )

    content = re.sub(
        r"<style.*?</style>",
        " ",
        content,
        flags=re.I | re.S
    )

    content = re.sub(
        r"<svg.*?</svg>",
        " ",
        content,
        flags=re.I | re.S
    )

    # Сохраняем разделители
    content = re.sub(
        r"</(p|div|article|section|li|h1|h2|h3|h4|br)>",
        "\n",
        content,
        flags=re.I
    )

    # Удаляем остальные HTML-теги
    content = re.sub(
        r"<[^>]+>",
        " ",
        content
    )

    content = html.unescape(
        content
    )

    lines = []

    for line in content.splitlines():

        line = re.sub(
            r"\s+",
            " ",
            line
        ).strip()

        if len(line) >= 3:
            lines.append(line)

    text = "\n".join(
        lines
    )

    # Убираем типичный мусор
    junk_patterns = [
        r"cookie",
        r"privacy policy",
        r"sign up",
        r"subscribe",
        r"advertisement",
        r"all rights reserved",
    ]

    cleaned = []

    for line in text.splitlines():

        low = line.lower()

        if len(line) < 3:
            continue

        # Не удаляем строку только из-за одного слова,
        # иначе можно случайно потерять важную информацию.
        if len(line) < 80 and any(
            pattern in low
            for pattern in junk_patterns
        ):
            continue

        cleaned.append(
            line
        )

    text = "\n".join(
        cleaned
    )

    # Ограничение
    if len(text) > 14000:
        text = text[:14000]

    return text.strip()


# ============================================================
# ПОЛУЧЕНИЕ КОНТЕНТА
# ============================================================

def get_article_content(item):

    # 1. Сначала RSS description
    rss_text = clean_html(
        item.get(
            "description",
            ""
        )
    )

    # Если RSS уже содержит полезный текст,
    # сохраняем его.
    if len(rss_text) >= 150:

        base_text = rss_text

    else:

        base_text = ""

    # 2. Пробуем открыть страницу
    page = fetch_page(
        item["link"]
    )

    if page:

        page_text = extract_text_from_html(
            page
        )

        if len(page_text) > len(base_text):

            base_text = page_text

    # 3. Если статья не открылась,
    # используем хотя бы RSS.
    if len(base_text) < 80:

        base_text = (
            "Текст статьи недоступен. "
            "Используй только заголовок "
            "и RSS-описание, не выдумывай "
            "детали."
        )

    return base_text


# ============================================================
# ОБОГАЩЕНИЕ НОВОСТЕЙ
# ============================================================

def enrich_news(news):

    result = []

    for i, item in enumerate(
        news,
        start=1
    ):

        print(
            f"Reading article "
            f"{i}/{len(news)}: "
            f"{item['title']}"
        )

        content = get_article_content(
            item
        )

        item["content"] = content

        print(
            f"Content length: "
            f"{len(content)} characters"
        )

        result.append(
            item
        )

        time.sleep(1)

    return result


# ============================================================
# GEMINI
# ============================================================

def analyze_news(
    news,
    memory
):

    news_text = ""

    for i, item in enumerate(
        news,
        start=1
    ):

        news_text += f"""

==============================
МАТЕРИАЛ {i}
==============================

ЗАГОЛОВОК:
{item["title"]}

ССЫЛКА:
{item["link"]}

ПРИОРИТЕТ:
{item["priority"]}

ТЕКСТ / ОПИСАНИЕ:
{item["content"]}

==============================
"""


    memory_text = ""

    for item in memory[-50:]:

        memory_text += f"""

ТЕМА:
{item.get("topic", "")}

ПОСТ:
{item.get("post", "")}

"""


    prompt = f"""
Ты редактор Telegram-канала LilsNews
по EA SPORTS FC 27 Ultimate Team.

Твоя задача — находить самые полезные,
конкретные и свежие новости для игроков.

НЕ ПИШИ ПУСТЫЕ НОВОСТИ.

==================================================
ЧТО НАМ НУЖНО
==================================================

🔥 META

🎮 TAKTICS

🧠 CUSTOM TACTICS

🃏 SBC

⭐ НОВЫЕ КАРТЫ

👤 META PLAYERS

⚡ GAMEPLAY

🛠 PATCH

🟣 PROMO

⚠️ LEAKS

🎯 OBJECTIVES

🔄 EVOLUTIONS

==================================================
ГЛАВНОЕ ПРАВИЛО
==================================================

НЕ ПЕРЕСКАЗЫВАЙ ЗАГОЛОВОК.

Если статья:

"EA FC 27 Best Meta Tactics"

нельзя писать:

"Появились лучшие мета-тактики."

Это бесполезно.

Нужно найти КОНКРЕТИКУ.

Например:

🔥 4-4-2

• какая роль у нападающих
• какая роль у полузащитников
• defensive approach
• build-up style
• width
• depth
• конкретные player instructions
• конкретный про-игрок

Но только если это действительно
есть в предоставленном материале.

==================================================
META / TAKTICS
==================================================

Если материал про тактики:

ОБЯЗАТЕЛЬНО ищи:

• Formation
• Custom Tactics
• Roles
• Instructions
• Build Up
• Defensive Approach
• Width
• Depth
• конкретных игроков
• имена про-игроков

Если в статье есть:

"4-4-2"

покажи:

"4-4-2"

Если есть конкретные настройки —
покажи их.

Если настроек нет —
НЕ ВЫДУМЫВАЙ.

Если статья только говорит,
что "4-4-2 сильная",
но не даёт полезной информации,
лучше NO_NEWS.

==================================================
PRO PLAYERS
==================================================

Если упоминается:

Anders Vejrgang
или другой про-игрок,

покажи:

👤 Имя

🎮 Что именно он использует.

Например:

4-4-2

или конкретные Custom Tactics.

НЕ ПРИДУМЫВАЙ связь,
если её нет в материале.

==================================================
SBC
==================================================

Для SBC ищи:

🃏 Игрок
⭐ OVR
📍 Позиция
💰 Цена
📋 Требования
🎁 Награды
⏳ Срок

Показывай только известные данные.

==================================================
КАРТЫ
==================================================

Если есть новая карта:

🃏 Игрок
⭐ OVR
📍 Позиция
⚡ Pace
🎯 Shooting
🎮 PlayStyles
🔥 главные характеристики

Только реальные данные
из материала.

==================================================
LEAKS
==================================================

Если это слив:

⚠️ СЛУХ

Но обязательно нужна конкретика.

Например:

🟣 Mbappé — 92 OVR
🟣 Haaland — 91 OVR

Если игроков в материале нет,
НЕ ПРИДУМЫВАЙ их.

==================================================
PATCH
==================================================

Если это патч:

🛠 ПАТЧ

Покажи именно изменения:

• Passing
• Shooting
• Defending
• Dribbling
• Goalkeepers
• Ultimate Team
• Career Mode

Но только если это написано
в материале.

==================================================
ДЕДУПЛИКАЦИЯ
==================================================

Очень важно.

Если уже публиковалось:

"Destined for Glory Team 2 leaked"

а новый материал рассказывает
то же самое,

НЕ ПУБЛИКУЙ.

Но если новый материал содержит
НОВУЮ конкретную информацию:

• новый игрок
• OVR
• SBC
• Objective
• цена
• новая карта
• новая дата
• новый upgrade path

тогда это может быть отдельная новость.

==================================================
КЛЮЧЕВОЕ ПРАВИЛО КАЧЕСТВА
==================================================

Лучше НЕ опубликовать новость,
чем опубликовать воду.

Если нет конкретной полезной информации:

NO_NEWS

==================================================
СТИЛЬ ПОСТА
==================================================

Пост:

40–90 слов.

Можно до 120,
если информации действительно много.

Без воды.

Без длинных вступлений.

Сразу конкретика.

Не используй:

"Фанаты с нетерпением ждут..."

"Игроки получили возможность..."

"В мире EA FC 27..."

==================================================
ФОРМАТ
==================================================

📰 LILSNEWS

🔥 META

или

🃏 SBC

или

🎮 GAMEPLAY

или

🛠 ПАТЧ

или

⚠️ СЛУХ

или

🟣 PROMO

Затем:

Короткий конкретный заголовок

Затем:

самая важная информация.

НЕ добавляй источник.

НЕ добавляй ссылку.

==================================================
УЖЕ ОПУБЛИКОВАНО
==================================================

{memory_text}

==================================================
НОВЫЕ МАТЕРИАЛЫ
==================================================

{news_text}

==================================================
ОТВЕТ
==================================================

Если есть ОДНА действительно важная
и конкретная новость:

напиши только готовый Telegram-пост.

В конце обязательно:

TOPIC: уникальное название новости

Если ничего достойного публикации нет:

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

                response = client.models.generate_content(
                    model=model,
                    contents=prompt
                )

                if (
                    response
                    and response.text
                ):

                    return response.text.strip()

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

    topic = ""

    lines = result.splitlines()

    post_lines = []

    for line in lines:

        if line.strip().startswith(
            "TOPIC:"
        ):

            topic = line.split(
                "TOPIC:",
                1
            )[1].strip()

        else:

            post_lines.append(
                line
            )

    post = "\n".join(
        post_lines
    ).strip()

    return post, topic


# ============================================================
# ДЕДУПЛИКАЦИЯ
# ============================================================

def is_duplicate(
    topic,
    post,
    memory
):

    topic_normalized = (
        topic.lower()
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

        old_post = old.get(
            "post",
            ""
        ).lower()

        # Одинаковая тема
        if (
            old_topic
            and old_topic == topic_normalized
        ):

            return True

        # Очень похожий пост
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
    # RSS
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
    # READ
    # --------------------------------------------------------

    news = enrich_news(
        news
    )

    print(
        f"Prepared {len(news)} "
        f"articles for Gemini"
    )

    if not news:

        print(
            "No readable news."
        )

        return

    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    result = analyze_news(
        news,
        memory
    )

    if result is None:

        print(
            "Gemini unavailable."
        )

        send_telegram(
            "⚠️ LilsNews\n\n"
            "Новости найдены, но Gemini "
            "временно не смог их обработать."
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
            "Gemini did not return TOPIC."
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
    # TELEGRAM
    # --------------------------------------------------------

    send_telegram(
        post
    )

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


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
