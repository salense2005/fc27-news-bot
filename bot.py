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

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=EA%20FC%2027%20Ultimate%20Team&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20SBC&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20players&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20meta&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20tactics&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20gameplay&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20leaks&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20promo&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20patch&hl=en-US&gl=US&ceid=US:en",
]

client = genai.Client(api_key=GEMINI_API_KEY)


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
    # Храним только последние 200 событий
    memory = memory[-200:]

    with open(MEMORY_FILE, "w", encoding="utf-8") as file:
        json.dump(
            memory,
            file,
            ensure_ascii=False,
            indent=2
        )


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

                if link in seen_links:
                    continue

                seen_links.add(link)

                news.append({
                    "title": title,
                    "link": link
                })

        except Exception as error:
            print(f"RSS error: {error}")

    return news[:60]


def analyze_news(news, memory):

    news_text = ""

    for index, item in enumerate(news, start=1):
        news_text += (
            f"{index}. {item['title']}\n"
            f"LINK: {item['link']}\n\n"
        )

    memory_text = ""

    for item in memory[-100:]:
        memory_text += (
            f"- {item.get('topic', '')}\n"
            f"  POST: {item.get('post', '')}\n\n"
        )

    prompt = f"""
Ты — редактор Telegram-канала LilsNews про EA SPORTS FC 27.

Твоя главная задача — находить НОВЫЕ и действительно важные события
в FC 27.

Найденные сейчас материалы:

{news_text}

==================================================
УЖЕ ОПУБЛИКОВАННЫЕ НОВОСТИ
==================================================

{memory_text}

==================================================
КРИТИЧЕСКИ ВАЖНО: НЕ ДЕЛАЙ ДУБЛИКИ
==================================================

Перед созданием поста сравни найденные материалы с уже опубликованными
новостями.

Если найденная новость рассказывает О ТОМ ЖЕ СОБЫТИИ и НЕ содержит
существенно новой информации — НЕ публикуй её.

Ответ:

NO_NEWS

Пример:

Ранее:
"⚠️ СЛУХ — Destined for Glory Team 2"

Сейчас:
"В сети снова появился слух о Destined for Glory Team 2"

Это ДУБЛИКАТ.

Ответ:

NO_NEWS

==================================================
НОВАЯ ИНФОРМАЦИЯ ПО СТАРОЙ ТЕМЕ
==================================================

Если тема уже публиковалась, но появилась СУЩЕСТВЕННО НОВАЯ информация,
новость можно публиковать снова.

Например:

Старый пост:
"⚠️ СЛУХ — Destined for Glory Team 2"

Новая информация:
"Стали известны конкретные игроки и рейтинги."

Это НЕ дубликат.

В таком случае публикуй новую информацию.

Но НЕ пересказывай старую информацию заново.

Пиши именно:

"Что стало известно нового?"

==================================================
ПРИОРИТЕТ
==================================================

🔥 META
- новые мета-тактики;
- формации;
- новые механики;
- OP-удары;
- OP-пасы;
- сильные игроки;
- PlayStyles;
- то, что используют сильные игроки.

🃏 ULTIMATE TEAM
- новые SBC;
- новые карты;
- Objectives;
- Evolutions;
- награды;
- промо.

🎮 GAMEPLAY
- важные изменения;
- патчи;
- изменения механик.

⚡ ТАКТИКИ
- новые сильные схемы;
- инструкции;
- тактики про-игроков.

💰 MARKET
- сильные изменения цен;
- выгодные карты;
- важные события рынка.

👀 LEAKS
- новые промо;
- составы;
- карты;
- рейтинги;
- даты.

==================================================
ОСОБЕННО ВАЖНО ДЛЯ SBC
==================================================

Если найден новый SBC, обязательно постарайся показать:

- название SBC;
- игрока/награду;
- рейтинг;
- позицию;
- стоимость, если она известна;
- срок действия;
- важные требования, если они интересны;
- что делает карту интересной.

НЕ пиши просто:

"Вышел новый SBC."

Нужно дать КОНКРЕТИКУ.

==================================================
ОСОБЕННО ВАЖНО ДЛЯ КАРТ И СЛИВОВ
==================================================

Если известны конкретные игроки, обязательно укажи их.

Например:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR

Если известны рейтинги — показывай рейтинги.

Если известны позиции — показывай позиции.

Если известны PlayStyles — показывай важные PlayStyles.

Если данных нет — НЕ ПРИДУМЫВАЙ.

==================================================
СТИЛЬ
==================================================

Пост должен быть коротким, но информативным.

Примерно 30–80 слов.

Коротко НЕ означает "убрать все факты".

Убирай воду, но сохраняй конкретику.

НЕ пиши:

"Готовьте монеты!"
"Не пропустите!"
"Это изменит игру!"
"Топовая карта!"
"Звёзды на подходе!"

если это не подтверждено конкретными фактами.

==================================================
ИСТОЧНИКИ
==================================================

Не показывай источник.

Не добавляй ссылки.

Не пиши:

"Источник:"
"Подробнее:"
"Читать далее:"

==================================================
СЛУХИ
==================================================

Если информация не подтверждена официально,
начни пост:

⚠️ СЛУХ

==================================================
ФОРМАТ
==================================================

Возвращай ТОЛЬКО готовый Telegram-пост.

Пример:

⚠️ СЛУХ

🔥 Destined for Glory Team 2

В сеть утекли первые карты второй команды:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR

Некоторые карты могут получить дополнительные апгрейды.

==================================================
ЕСЛИ НЕТ НОВОЙ ВАЖНОЙ ИНФОРМАЦИИ
==================================================

Ответь строго:

NO_NEWS

==================================================
ФОРМАТ ДЛЯ СИСТЕМЫ
==================================================

После готового поста ОБЯЗАТЕЛЬНО добавь отдельную строку:

TOPIC: короткое название события

Например:

TOPIC: Destined for Glory Team 2

Это нужно для того, чтобы бот мог распознавать повторные новости.

Не добавляй ничего после строки TOPIC.
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
                    contents=prompt,
                )

                if response and response.text:
                    return response.text.strip()

            except Exception as error:

                print(
                    f"Gemini error with {model}: {error}"
                )

                if attempt < 2:
                    time.sleep(10)

    return None


def extract_topic(result):

    lines = result.splitlines()

    topic = ""

    for line in lines:

        if line.strip().startswith("TOPIC:"):
            topic = line.split(
                "TOPIC:",
                1
            )[1].strip()

    # Убираем TOPIC из сообщения Telegram
    post_lines = [
        line
        for line in lines
        if not line.strip().startswith("TOPIC:")
    ]

    post = "\n".join(post_lines).strip()

    return post, topic


def main():

    print("LilsNews started")

    memory = load_memory()

    print(
        f"Memory contains {len(memory)} published events"
    )

    news = get_news()

    print(
        f"Found {len(news)} current news items"
    )

    if not news:

        print("No news found")

        return

    result = analyze_news(
        news,
        memory
    )

    if result is None:

        send_telegram(
            "⚠️ LilsNews\n\n"
            "Новости найдены, но Gemini временно недоступен."
        )

        return

    if result.strip() == "NO_NEWS":

        print(
            "No new important news."
        )

        return

    post, topic = extract_topic(result)

    if not post:

        print("Empty post")

        return

    if not topic:

        print(
            "WARNING: Gemini did not provide TOPIC"
        )

        topic = post[:100]

    # Дополнительная защита от точного повтора
    for item in memory:

        old_topic = item.get(
            "topic",
            ""
        ).lower().strip()

        new_topic = topic.lower().strip()

        if old_topic and old_topic == new_topic:

            print(
                f"Duplicate topic detected: {topic}"
            )

            return

    send_telegram(
        "📰 LILSNEWS\n\n"
        + post
    )

    memory.append({
        "topic": topic,
        "post": post,
        "timestamp": int(time.time())
    })

    save_memory(memory)

    print(
        f"Post sent and saved: {topic}"
    )


if __name__ == "__main__":
    main()
