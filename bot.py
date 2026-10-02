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

# ============================================================
# ИСТОЧНИКИ НОВОСТЕЙ
# ============================================================

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=EA%20SPORTS%20FC%2027%20Ultimate%20Team&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20SBC&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20Ultimate%20Team%20cards&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20meta&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20tactics&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20gameplay&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20leaks&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20promo&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20patch&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20ratings&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20pro%20players&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20Objectives&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=FC%2027%20Evolution&hl=en-US&gl=US&ceid=US:en",
]

client = genai.Client(api_key=GEMINI_API_KEY)


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
# ПАМЯТЬ
# ============================================================

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return []

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            return data

    except Exception as error:
        print(f"Memory read error: {error}")

    return []


def save_memory(memory):
    memory = memory[-200:]

    with open(MEMORY_FILE, "w", encoding="utf-8") as file:
        json.dump(
            memory,
            file,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# ПОИСК НОВОСТЕЙ
# ============================================================

def get_news():
    news = []
    seen_links = set()

    for feed_url in RSS_FEEDS:

        try:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:10]:

                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()

                if not title or not link:
                    continue

                # Жёстко отсекаем старые игры
                title_lower = title.lower()

                if "fc 26" in title_lower:
                    continue

                if "fc26" in title_lower:
                    continue

                if "fc 25" in title_lower:
                    continue

                if "fc25" in title_lower:
                    continue

                if link in seen_links:
                    continue

                seen_links.add(link)

                news.append({
                    "title": title,
                    "link": link
                })

        except Exception as error:
            print(f"RSS error: {error}")

    return news[:80]


# ============================================================
# ФИЛЬТР НОВОСТЕЙ
# ============================================================

def filter_relevant_news(news):

    relevant = []

    important_words = [
        "fc 27",
        "fc27",
        "ultimate team",
        "sbc",
        "leak",
        "leaked",
        "meta",
        "tactics",
        "formation",
        "promo",
        "ratings",
        "player",
        "players",
        "patch",
        "gameplay",
        "objective",
        "evolution",
        "playstyle",
        "pro player",
        "team 2",
        "team 1",
        "upgrade",
        "upgrades",
        "card",
        "cards",
    ]

    for item in news:

        title = item["title"].lower()

        # Только реально связанные с FC 27 материалы
        if not (
            "fc 27" in title
            or "fc27" in title
        ):
            continue

        # Проверяем наличие интересующей тематики
        if any(word in title for word in important_words):
            relevant.append(item)

    return relevant[:50]


# ============================================================
# GEMINI
# ============================================================

def analyze_news(news, memory):

    news_text = ""

    for index, item in enumerate(news, start=1):

        news_text += f"""
МАТЕРИАЛ {index}

ЗАГОЛОВОК:
{item["title"]}

ССЫЛКА:
{item["link"]}

"""


    memory_text = ""

    for item in memory[-100:]:

        memory_text += f"""
УЖЕ ПУБЛИКОВАЛОСЬ:

ТЕМА:
{item.get("topic", "")}

ПОСТ:
{item.get("post", "")}

"""


    prompt = f"""
Ты — редактор Telegram-канала LilsNews про EA SPORTS FC 27.

Канал нужен игрокам Ultimate Team и людям,
которые хотят быстро узнавать:

🔥 META
🃏 SBC
🟣 новые карты
⚡ тактики
🎮 изменения gameplay
👀 сливы
📈 рейтинги
💰 важные изменения рынка
🛠 патчи

Тебе переданы свежие материалы:

{news_text}

==================================================
ИСТОРИЯ КАНАЛА
==================================================

{memory_text}

==================================================
ПЕРВОЕ ПРАВИЛО — НИКАКИХ ДУБЛЕЙ
==================================================

Если новость рассказывает то же самое,
что уже было опубликовано, НЕ публикуй её.

Например:

Было:
"Destined for Glory Team 2 leaked."

Новое:
"Another source reports Destined for Glory Team 2 leaked."

Это одна и та же новость.

Ответ:

NO_NEWS

==================================================
НОВАЯ ИНФОРМАЦИЯ
==================================================

Если старая тема получила НОВУЮ конкретную информацию,
её можно публиковать.

Например:

Было:
"Destined for Glory Team 2 leaked."

Теперь:
"Haaland 91 OVR, Diani 88 OVR and Upamecano 88 OVR
have been leaked."

Это новая информация.

Публикуй только новые данные.

==================================================
САМОЕ ВАЖНОЕ — КОНКРЕТИКА
==================================================

Пользователю неинтересно читать:

"В сети появились новые карты."

Это слишком мало информации.

Если доступны конкретные данные,
обязательно показывай их.

Например:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR

Если известна позиция:

📍 ST

Если известна стоимость:

💰 85K

Если известен срок:

⏳ 3 дня

Если известны PlayStyles:

⚡ Finesse Shot+

Используй только данные,
которые есть в предоставленных материалах.

НИЧЕГО НЕ ВЫДУМЫВАЙ.

==================================================
SBC
==================================================

SBC имеют ОЧЕНЬ ВЫСОКИЙ ПРИОРИТЕТ.

Если найден новый SBC,
постарайся указать:

🃏 Игрок
⭐ OVR
📍 Позиция
💰 Примерная стоимость
⏳ Срок
📌 Главное преимущество

Если известны требования —
укажи только действительно важные.

Не пиши просто:

"Вышел новый SBC."

==================================================
META
==================================================

META имеет ОЧЕНЬ ВЫСОКИЙ ПРИОРИТЕТ.

Ищи:

🔥 новые формации
🔥 новые тактики
🔥 новые инструкции
🔥 OP-удары
🔥 OP-пасы
🔥 OP-механики
🔥 сильных игроков
🔥 PlayStyles
🔥 тактики про-игроков
🔥 изменения после патча

Если информация может помочь человеку
выигрывать матчи — это очень важно.

==================================================
СЛИВЫ
==================================================

Если это неподтверждённая информация,
начни:

⚠️ СЛУХ

Но не пиши только:

"Инсайдеры слили новые карты."

Нужно показать конкретные карты,
если они известны.

Например:

⚠️ СЛУХ

🔥 Destined for Glory Team 2

В сеть утекли:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR

==================================================
ПАТЧИ
==================================================

Если вышел патч,
ищи именно то, что изменилось.

Не:

"EA выпустила новый патч."

А:

🎮 EA изменила удары:

• Low Driven Shot стал ...
• Power Shot получил ...
• ...

Используй только подтверждённые данные.

==================================================
ФИЛЬТРАЦИЯ
==================================================

Не публикуй:

❌ обычные обзоры игры
❌ старые новости
❌ новости FC 26
❌ новости FC 25
❌ общие советы
❌ статьи "10 лучших игроков", если это не новая информация
❌ статьи без новых событий
❌ новости без конкретной ценности для игрока

==================================================
СТИЛЬ
==================================================

Пост должен быть:

коротким;
конкретным;
понятным;
интересным.

Обычно 30–100 слов.

Не пиши воду.

Не используй:

"Готовьте монеты!"
"Не пропустите!"
"Это изменит игру!"
"Топовая карта!"
"Звёзды на подходе!"

если это просто рекламная фраза.

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
ЕСЛИ НЕТ ВАЖНОЙ НОВОСТИ
==================================================

Ответь строго:

NO_NEWS

==================================================
ФОРМАТ
==================================================

Верни готовый Telegram-пост.

После него отдельной строкой:

TOPIC: название конкретного события

Например:

⚠️ СЛУХ

🔥 Destined for Glory Team 2

В сеть утекли новые карты второй команды:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR

TOPIC: Destined for Glory Team 2 player ratings leak
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
                    time.sleep(10)

    return None


# ============================================================
# TOPIC
# ============================================================

def extract_topic(result):

    lines = result.splitlines()

    topic = ""
    post_lines = []

    for line in lines:

        if line.strip().startswith("TOPIC:"):

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
# ДУБЛИ
# ============================================================

def is_duplicate(topic, post, memory):

    new_topic = topic.lower().strip()

    new_post = post.lower().strip()

    for item in memory:

        old_topic = item.get(
            "topic",
            ""
        ).lower().strip()

        old_post = item.get(
            "post",
            ""
        ).lower().strip()

        # Полное совпадение темы
        if old_topic and old_topic == new_topic:
            return True

        # Защита от почти одинаковых постов
        if old_post and new_post:

            old_words = set(
                old_post.split()
            )

            new_words = set(
                new_post.split()
            )

            if len(new_words) > 20:

                intersection = (
                    len(old_words & new_words)
                    / len(new_words)
                )

                if intersection > 0.85:
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

    news = get_news()

    print(
        f"Found {len(news)} raw news items"
    )

    news = filter_relevant_news(news)

    print(
        f"After FC27 filter: "
        f"{len(news)} relevant items"
    )

    if not news:

        print(
            "No relevant FC27 news."
        )

        return

    # Показываем найденные новости
    for index, item in enumerate(
        news[:20],
        start=1
    ):

        print(
            f"{index}. {item['title']}"
        )

    result = analyze_news(
        news,
        memory
    )

    if result is None:

        send_telegram(
            "⚠️ LilsNews\n\n"
            "Новости найдены, но Gemini "
            "временно недоступен."
        )

        return

    if result.strip() == "NO_NEWS":

        print(
            "No new important news."
        )

        return

    post, topic = extract_topic(
        result
    )

    if not post:

        print(
            "Gemini returned empty post."
        )

        return

    if not topic:

        print(
            "Gemini did not return TOPIC."
        )

        return

    if is_duplicate(
        topic,
        post,
        memory
    ):

        print(
            f"Duplicate blocked: {topic}"
        )

        return

    # Отправляем
    send_telegram(
        "📰 LILSNEWS\n\n"
        + post
    )

    # Сохраняем
    memory.append({
        "topic": topic,
        "post": post,
        "timestamp": int(time.time())
    })

    save_memory(memory)

    print(
        f"Published: {topic}"
    )


if __name__ == "__main__":
    main()
