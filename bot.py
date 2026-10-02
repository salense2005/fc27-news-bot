import os
import json
import time
import re
import requests
import feedparser
from bs4 import BeautifulSoup
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

    # SBC
    "https://news.google.com/rss/search?q=EA%20FC%2027%20SBC&hl=en-US&gl=US&ceid=US:en",

    # Cards
    "https://news.google.com/rss/search?q=EA%20FC%2027%20Ultimate%20Team%20cards&hl=en-US&gl=US&ceid=US:en",

    # Players / leaks
    "https://news.google.com/rss/search?q=EA%20FC%2027%20players%20leaked&hl=en-US&gl=US&ceid=US:en",

    # Ratings
    "https://news.google.com/rss/search?q=EA%20FC%2027%20ratings&hl=en-US&gl=US&ceid=US:en",

    # Promo
    "https://news.google.com/rss/search?q=EA%20FC%2027%20promo&hl=en-US&gl=US&ceid=US:en",

    # Meta
    "https://news.google.com/rss/search?q=EA%20FC%2027%20meta&hl=en-US&gl=US&ceid=US:en",

    # Tactics
    "https://news.google.com/rss/search?q=EA%20FC%2027%20tactics&hl=en-US&gl=US&ceid=US:en",

    # Gameplay
    "https://news.google.com/rss/search?q=EA%20FC%2027%20gameplay&hl=en-US&gl=US&ceid=US:en",

    # Patch
    "https://news.google.com/rss/search?q=EA%20FC%2027%20patch&hl=en-US&gl=US&ceid=US:en",

    # Objectives
    "https://news.google.com/rss/search?q=EA%20FC%2027%20Objectives&hl=en-US&gl=US&ceid=US:en",

    # Evolutions
    "https://news.google.com/rss/search?q=EA%20FC%2027%20Evolution&hl=en-US&gl=US&ceid=US:en",

    # Pro players
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
# ПОЛУЧЕНИЕ НОВОСТЕЙ
# ============================================================

def get_news():

    news = []
    seen = set()

    for feed_url in RSS_FEEDS:

        try:

            feed = feedparser.parse(
                feed_url
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

                if not title or not link:
                    continue

                title_lower = title.lower()

                # Не берём старые игры
                if "fc 26" in title_lower:
                    continue

                if "fc26" in title_lower:
                    continue

                if "fc 25" in title_lower:
                    continue

                if "fc25" in title_lower:
                    continue

                # Только FC27
                if (
                    "fc 27" not in title_lower
                    and "fc27" not in title_lower
                ):
                    continue

                if link in seen:
                    continue

                seen.add(link)

                news.append({
                    "title": title,
                    "link": link,
                })

        except Exception as error:

            print(
                f"RSS error: {error}"
            )

    return news


# ============================================================
# ПРИОРИТЕТ
# ============================================================

def calculate_priority(title):

    title = title.lower()

    score = 0

    # SBC
    if "sbc" in title:
        score += 100

    if "new sbc" in title:
        score += 30

    # Cards
    if "card" in title:
        score += 70

    if "cards" in title:
        score += 70

    # Players
    if "player" in title:
        score += 60

    if "players" in title:
        score += 60

    # Ratings
    if "rating" in title:
        score += 55

    if "ratings" in title:
        score += 55

    # META
    if "meta" in title:
        score += 100

    if "tactic" in title:
        score += 90

    if "tactics" in title:
        score += 90

    if "formation" in title:
        score += 85

    if "formations" in title:
        score += 85

    if "gameplay" in title:
        score += 75

    if "pro player" in title:
        score += 75

    if "pro players" in title:
        score += 75

    # Promo
    if "promo" in title:
        score += 60

    if "team 2" in title:
        score += 50

    if "team 1" in title:
        score += 50

    # Leaks
    if "leak" in title:
        score += 45

    if "leaked" in title:
        score += 45

    # Patch
    if "patch" in title:
        score += 80

    if "update" in title:
        score += 40

    # Objectives / Evolutions
    if "objective" in title:
        score += 60

    if "evolution" in title:
        score += 60

    if "upgrade" in title:
        score += 50

    return score


# ============================================================
# ВЫБОР ЛУЧШИХ НОВОСТЕЙ
# ============================================================

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
# ЧТЕНИЕ СТАТЬИ
# ============================================================

def read_article(url):

    try:

        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/154.0 Safari/537.36"
            )
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

        html = response.text

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        # Убираем мусор
        for tag in soup(
            [
                "script",
                "style",
                "nav",
                "footer",
                "header",
                "aside",
                "form",
                "noscript"
            ]
        ):

            tag.decompose()

        # Сначала ищем article
        article = soup.find(
            "article"
        )

        if article:

            text = article.get_text(
                "\n",
                strip=True
            )

        else:

            text = soup.get_text(
                "\n",
                strip=True
            )

        # Чистим пустые строки
        lines = []

        for line in text.splitlines():

            line = re.sub(
                r"\s+",
                " ",
                line
            ).strip()

            if line:
                lines.append(line)

        text = "\n".join(lines)

        # Ограничиваем размер
        # чтобы не отправлять Gemini огромную страницу
        if len(text) > 18000:
            text = text[:18000]

        if len(text) < 300:

            print(
                "Article text too short."
            )

            return ""

        return text

    except Exception as error:

        print(
            f"Article read error: {error}"
        )

        return ""


# ============================================================
# ДОБАВЛЯЕМ ТЕКСТ СТАТЕЙ
# ============================================================

def enrich_news(news):

    enriched = []

    for i, item in enumerate(
        news,
        start=1
    ):

        print(
            f"Reading article "
            f"{i}/{len(news)}: "
            f"{item['title']}"
        )

        article_text = read_article(
            item["link"]
        )

        if not article_text:

            print(
                "Could not read article."
            )

            continue

        item["content"] = article_text

        enriched.append(
            item
        )

        # Небольшая пауза
        time.sleep(1)

    return enriched


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

СОДЕРЖАНИЕ СТАТЬИ:
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

==============================
"""


    prompt = f"""
Ты главный редактор Telegram-канала LilsNews
по EA SPORTS FC 27.

Твоя задача — находить НЕ просто новости,
а конкретную информацию, которая реально
интересна игроку Ultimate Team.

Теперь у тебя есть НЕ ТОЛЬКО заголовки,
но и содержимое статей.

ОБЯЗАТЕЛЬНО анализируй содержание статьи.

==================================================
ПРИОРИТЕТ
==================================================

🔥 SBC

🔥 НОВЫЕ КАРТЫ

🔥 КОНКРЕТНЫЕ ИГРОКИ

🔥 OVR / РЕЙТИНГИ

🔥 META

🔥 ТАКТИКИ

🔥 ФОРМАЦИИ

🔥 ТАКТИКИ ПРО-ИГРОКОВ

🔥 GAMEPLAY

🔥 PATCH

🔥 PROMO

🔥 LEAKS

🔥 OBJECTIVES

🔥 EVOLUTIONS

==================================================
ГЛАВНОЕ ПРАВИЛО
==================================================

НЕ ПЕРЕСКАЗЫВАЙ ЗАГОЛОВОК.

ТЫ ДОЛЖЕН ДОСТАТЬ ИЗ СТАТЬИ
КОНКРЕТНЫЕ ДАННЫЕ.

Например, если статья называется:

"EA FC 27 Best Meta Tactics"

НЕ ПИШИ:

"Появились новые мета-тактики."

Это бесполезно.

Вместо этого найди в статье:

• формацию
• настройки
• роли
• инструкции
• стиль игры
• игроков
• причины эффективности
• информацию о про-игроках

И напиши конкретно то,
что реально указано в статье.

==================================================
META / ТАКТИКИ
==================================================

Если статья содержит META,
ищи:

• Formation
• Custom Tactics
• Player Roles
• Player Instructions
• Build Up Style
• Defensive Approach
• Width
• Depth
• Attacking Style
• конкретных игроков
• конкретных про-игроков
• PlayStyles
• Gameplay mechanics

Если есть конкретная схема:

например:

4-4-2

обязательно покажи её.

Если есть конкретные настройки —
покажи их.

Если есть про-игрок —
укажи его имя.

Если статья говорит,
что определённый игрок использует эту тактику —
укажи это.

НЕ ПРИДУМЫВАЙ отсутствующие настройки.

==================================================
SBC
==================================================

Если найден новый SBC,
ищи в статье:

🃏 Игрок
⭐ OVR
📍 Позиция
💰 Цена
📋 Требования
⏳ Срок
🎁 Награды

Показывай только те данные,
которые реально есть.

==================================================
КАРТЫ
==================================================

Если найдена новая карта:

🃏 Игрок
⭐ OVR
📍 Позиция
⚡ Pace
🎯 Shooting
🎮 PlayStyles
🔥 ключевые характеристики

Но только если данные присутствуют.

==================================================
LEAK
==================================================

Если это слух:

⚠️ СЛУХ

Но обязательно показывай конкретику.

Например:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR

Если статья называет только промо,
но не называет игроков —
НЕ ПРИДУМЫВАЙ игроков.

==================================================
ПАТЧ
==================================================

Если это патч:

🛠 ПАТЧ

Покажи конкретные изменения.

Например:

• Passing
• Shooting
• Defending
• Dribbling
• Goalkeepers
• Ultimate Team

Но только если это действительно
есть в тексте статьи.

==================================================
ДЕДУПЛИКАЦИЯ
==================================================

Очень важно.

Уже была:

"Destined for Glory Team 2 leaked."

Новая статья:

"More information about Destined for Glory Team 2."

НЕ публиковать.

Но если новая статья добавляет:

• новых игроков
• OVR
• цены
• SBC
• Objectives
• новые характеристики
• новые даты
• новые апгрейды

то это уже новая информация.

В таком случае можно публиковать.

==================================================
КАЧЕСТВО
==================================================

Публикуй ТОЛЬКО если в статье
есть конкретная новая информация.

Если статья просто:

• обзор
• мнение
• список старых игроков
• старый гайд
• общие советы
• повторение уже известной информации

ответь:

NO_NEWS

==================================================
НЕ ВЫДУМЫВАЙ
==================================================

НИКОГДА не придумывай:

❌ игроков
❌ OVR
❌ цены
❌ SBC
❌ тактики
❌ формации
❌ настройки
❌ даты
❌ характеристики
❌ META

Если этого нет в статье —
не пиши это.

==================================================
СТИЛЬ
==================================================

Пост должен быть коротким.

Обычно:

40–90 слов.

Но если есть реально много важной
конкретики — можно немного больше.

Никакой воды.

Никаких длинных вступлений.

Никаких:

"В мире EA FC 27 произошло..."

"Игроки получили возможность..."

"Фанаты игры с нетерпением..."

Сразу к сути.

==================================================
ФОРМАТ
==================================================

Используй:

📰 LILSNEWS

Затем категория:

🔥 META

🃏 SBC

⚠️ СЛУХ

🎮 GAMEPLAY

🛠 ПАТЧ

🟣 PROMO

Затем короткий заголовок.

Затем конкретика.

Не добавляй источник.

Не добавляй ссылку.

==================================================
ВАЖНО
==================================================

Если информация полезная,
но статья содержит недостаточно конкретики,
лучше НЕ публиковать.

Лучше один действительно хороший пост,
чем пять пустых.

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

Если есть действительно новая
и конкретная информация:

напиши готовый пост.

В конце обязательно добавь:

TOPIC: уникальное название новости

Если ничего действительно нового
и полезного нет:

ответь строго:

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
# ИЗВЛЕЧЕНИЕ TOPIC
# ============================================================

def extract_topic(result):

    lines = result.splitlines()

    post_lines = []
    topic = ""

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
# ПРОВЕРКА ДУБЛЯ
# ============================================================

def is_duplicate(
    topic,
    post,
    memory
):

    topic = topic.lower().strip()

    post = post.lower().strip()

    for old in memory:

        old_topic = old.get(
            "topic",
            ""
        ).lower().strip()

        old_post = old.get(
            "post",
            ""
        ).lower().strip()

        # Точная тема
        if (
            old_topic
            and old_topic == topic
        ):

            return True

        # Сравнение текста
        if (
            old_post
            and len(post) > 40
        ):

            old_words = set(
                old_post.split()
            )

            new_words = set(
                post.split()
            )

            if new_words:

                similarity = len(
                    old_words & new_words
                ) / len(new_words)

                if similarity > 0.88:

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
    # 1. Получаем RSS
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
    # 2. Приоритет
    # --------------------------------------------------------

    news = select_best_news(
        news
    )

    print(
        f"Selected {len(news)} high-priority items"
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
    # 3. Читаем статьи
    # --------------------------------------------------------

    news = enrich_news(
        news
    )

    print(
        f"Successfully read "
        f"{len(news)} articles"
    )

    if not news:

        print(
            "Could not read any articles."
        )

        return

    # --------------------------------------------------------
    # 4. Gemini
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
    # 5. Ничего нового
    # --------------------------------------------------------

    if result.strip() == "NO_NEWS":

        print(
            "No new important news."
        )

        return

    # --------------------------------------------------------
    # 6. Получаем пост
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
    # 7. Проверяем дубль
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
    # 8. Отправляем Telegram
    # --------------------------------------------------------

    send_telegram(
        post
    )

    # --------------------------------------------------------
    # 9. Сохраняем память
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
