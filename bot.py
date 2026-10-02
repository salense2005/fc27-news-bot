import os
import json
import time
import requests
import feedparser
from google import genai

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

MEMORY_FILE = "published_news.json"

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# GOOGLE NEWS — ПОИСК
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

    "https://news.google.com/rss/search?q=EA%20FC%2027%20Objective&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20Evolution&hl=en-US&gl=US&ceid=US:en",

    "https://news.google.com/rss/search?q=EA%20FC%2027%20pro%20players&hl=en-US&gl=US&ceid=US:en",
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

                # Жёстко исключаем старые игры
                if "fc 26" in title_lower:
                    continue

                if "fc26" in title_lower:
                    continue

                if "fc 25" in title_lower:
                    continue

                if "fc25" in title_lower:
                    continue

                # Только FC 27
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
# ПРИОРИТЕТ НОВОСТЕЙ
# ============================================================

def calculate_priority(title):

    title = title.lower()

    score = 0

    # САМОЕ ВАЖНОЕ
    if "sbc" in title:
        score += 100

    if "new sbc" in title:
        score += 30

    if "card" in title:
        score += 70

    if "cards" in title:
        score += 70

    if "player" in title:
        score += 60

    if "players" in title:
        score += 60

    if "rating" in title:
        score += 55

    if "ratings" in title:
        score += 55

    # META
    if "meta" in title:
        score += 80

    if "tactic" in title:
        score += 75

    if "tactics" in title:
        score += 75

    if "formation" in title:
        score += 70

    if "gameplay" in title:
        score += 65

    if "pro player" in title:
        score += 65

    # ПРОМО
    if "promo" in title:
        score += 55

    if "team 2" in title:
        score += 50

    if "team 1" in title:
        score += 50

    if "leak" in title:
        score += 45

    if "leaked" in title:
        score += 45

    # ПАТЧ
    if "patch" in title:
        score += 75

    if "update" in title:
        score += 40

    # OBJECTIVES / EVOLUTION
    if "objective" in title:
        score += 55

    if "evolution" in title:
        score += 55

    if "upgrade" in title:
        score += 45

    return score


# ============================================================
# ВЫБИРАЕМ ЛУЧШИЕ НОВОСТИ
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

    # Берём только самые интересные материалы
    return news[:15]


# ============================================================
# GEMINI
# ============================================================

def analyze_news(news, memory):

    news_text = ""

    for i, item in enumerate(
        news,
        start=1
    ):

        news_text += f"""
МАТЕРИАЛ {i}

ЗАГОЛОВОК:
{item["title"]}

ССЫЛКА:
{item["link"]}

ПРИОРИТЕТ:
{item["priority"]}

--------------------------------
"""


    memory_text = ""

    for item in memory[-50:]:

        memory_text += f"""
ТЕМА:
{item.get("topic", "")}

ПОСТ:
{item.get("post", "")}

--------------------------------
"""


    prompt = f"""
Ты главный редактор Telegram-канала LilsNews,
посвящённого EA SPORTS FC 27.

Твоя задача — НЕ писать обычные новости.

Твоя задача — находить информацию,
ради которой игрок FC 27 реально захочет открыть Telegram.

==================================================
ПРИОРИТЕТ
==================================================

🔥 1. НОВЫЕ SBC

🔥 2. НОВЫЕ КАРТЫ

🔥 3. КОНКРЕТНЫЕ ИГРОКИ И ИХ OVR

🔥 4. META

🔥 5. НОВЫЕ ТАКТИКИ

🔥 6. ИЗМЕНЕНИЯ GAMEPLAY

🔥 7. ПАТЧИ

🔥 8. НОВЫЕ ПРОМО

🔥 9. LEAKS

🔥 10. OBJECTIVES / EVOLUTIONS

==================================================
МАТЕРИАЛЫ
==================================================

{news_text}

==================================================
ЧТО УЖЕ БЫЛО
==================================================

{memory_text}

==================================================
ГЛАВНОЕ ПРАВИЛО
==================================================

НЕ публикуй новость только потому,
что она связана с FC 27.

Она должна содержать НОВУЮ И ПОЛЕЗНУЮ информацию.

Например:

ПЛОХО:

"В сеть утекла информация
о Destined for Glory Team 2."

Это слишком мало.

ХОРОШО:

"В сеть утекли:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR"

Вот это конкретика.

==================================================
DUPLICATES
==================================================

Очень важно.

Если уже была новость:

"Destined for Glory Team 2 leaked."

И появляется:

"More information about Destined for Glory Team 2."

Это НЕ новая новость.

НЕ публикуй.

Но если появляется:

"Конкретные игроки Team 2:
Haaland 91 OVR, Diani 88 OVR..."

Это уже НОВАЯ информация.

Публикуй её.

То есть одна и та же тема может появляться
несколько раз ТОЛЬКО если появились новые
конкретные данные.

==================================================
SBC
==================================================

Если найден новый SBC,
это один из самых важных типов новостей.

Если есть информация,
покажи:

🃏 Игрок
⭐ OVR
📍 Позиция
💰 Цена
⏳ Срок
📌 Главное

Но только если это реально есть
в материале.

НИЧЕГО НЕ ВЫДУМЫВАЙ.

==================================================
META
==================================================

Особое внимание:

🔥 META-формации
🔥 META-тактики
🔥 инструкции
🔥 OP-игроки
🔥 OP-PlayStyles
🔥 новые механики
🔥 удары
🔥 пасы
🔥 забегания
🔥 gameplay
🔥 тактики про-игроков

Если найдено что-то конкретное,
публикуй.

Например:

🔥 Новая META

4-2-3-1

CAM → свободная позиция
ST → впереди
CDM → stay back

Но только если такие данные
реально есть в материале.

==================================================
LEAKS
==================================================

Для слухов используй:

⚠️ СЛУХ

Но обязательно дай КОНКРЕТИКУ.

Не:

"Слили новую промо."

А:

"Слили игроков:

🟣 X — 91 OVR
🟣 Y — 89 OVR
🟣 Z — 88 OVR"

==================================================
ПАТЧ
==================================================

Не пиши:

"EA выпустила новый патч."

Показывай конкретное изменение:

🎮 EA изменила:

• Low Driven Shots
• Passing
• Defending

если такие данные действительно есть.

==================================================
ОЧЕНЬ ВАЖНО
==================================================

НЕ ПРИДУМЫВАЙ игроков.

НЕ ПРИДУМЫВАЙ рейтинги.

НЕ ПРИДУМЫВАЙ цены.

НЕ ПРИДУМЫВАЙ тактики.

НЕ ПРИДУМЫВАЙ META.

Используй только информацию,
которая присутствует в предоставленных материалах.

==================================================
ЧТО НЕ НУЖНО
==================================================

❌ длинные вступления

❌ вода

❌ обзоры игры

❌ обычные статьи

❌ "10 лучших игроков",
если это не новая информация

❌ рекламный текст

❌ советы, которые были известны давно

❌ информация про FC 26

❌ информация про FC 25

❌ одна и та же новость второй раз

==================================================
ДЛИНА
==================================================

Идеальный пост:

30–80 слов.

Иногда можно до 120 слов,
если информации действительно много.

Лучше коротко и конкретно,
чем длинно и пусто.

==================================================
ИСТОЧНИК
==================================================

НЕ показывай источник.

НЕ добавляй ссылку.

НЕ пиши:

"Источник:"
"Подробнее:"
"Читать далее:"

==================================================
ФОРМАТ
==================================================

Начало:

📰 LILSNEWS

Затем один из вариантов:

🔥 META

🃏 SBC

⚠️ СЛУХ

🎮 GAMEPLAY

🛠 ПАТЧ

🟣 PROMO

Затем:

короткий заголовок

конкретная информация

В конце:

TOPIC: уникальное название события

==================================================
ЕСЛИ НЕТ ДЕЙСТВИТЕЛЬНО НОВОЙ ИНФОРМАЦИИ
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
# TOPIC
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

            post_lines.append(line)

    post = "\n".join(
        post_lines
    ).strip()

    return post, topic


# ============================================================
# ДУБЛЬ
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

        # Слишком похожий пост
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

    print("================================")
    print("LilsNews started")
    print("================================")

    memory = load_memory()

    print(
        f"Memory: {len(memory)} events"
    )

    # Получаем новости
    news = get_news()

    print(
        f"Found {len(news)} raw news items"
    )

    if not news:

        print(
            "No FC27 news found."
        )

        return

    # Сортируем по важности
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

    # Gemini
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

    # Нет новости
    if result.strip() == "NO_NEWS":

        print(
            "No new important news."
        )

        return

    # Разбираем ответ
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

    # Проверяем дубль
    if is_duplicate(
        topic,
        post,
        memory
    ):

        print(
            f"Duplicate blocked: {topic}"
        )

        return

    # Публикация
    telegram_message = (
        post
    )

    send_telegram(
        telegram_message
    )

    # Записываем в память
    memory.append({
        "topic": topic,
        "post": post,
        "timestamp": int(time.time())
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
