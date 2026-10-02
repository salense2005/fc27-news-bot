import os
import json
import time
import requests
import feedparser
from bs4 import BeautifulSoup
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
    "https://news.google.com/rss/search?q=EA%20FC%2027 tactics&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027 gameplay&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027 leaks&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027 promo&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027 patch&hl=en-US&gl=US&ceid=US:en",
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
        print(f"Memory error: {error}")

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

    return news[:40]


def extract_article_text(url):
    """
    Открывает статью и пытается получить её основной текст.
    """

    try:

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/130 Safari/537.36"
            )
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=20,
            allow_redirects=True
        )

        if response.status_code != 200:
            print(
                f"Article request failed: "
                f"{response.status_code}"
            )
            return ""

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # Убираем ненужные элементы
        for element in soup([
            "script",
            "style",
            "nav",
            "header",
            "footer",
            "aside",
            "form",
            "noscript"
        ]):
            element.decompose()

        paragraphs = []

        for paragraph in soup.find_all("p"):

            text = paragraph.get_text(
                " ",
                strip=True
            )

            if len(text) < 40:
                continue

            paragraphs.append(text)

        article_text = "\n".join(paragraphs)

        # Ограничиваем объём, чтобы не отправлять
        # гигантские статьи в Gemini
        return article_text[:18000]

    except Exception as error:

        print(
            f"Article extraction error: {error}"
        )

        return ""


def prepare_sources(news):
    """
    Для каждой новости пытаемся получить
    полный текст статьи.
    """

    sources = []

    for index, item in enumerate(news):

        print(
            f"Reading article "
            f"{index + 1}/{len(news)}: "
            f"{item['title']}"
        )

        article_text = extract_article_text(
            item["link"]
        )

        sources.append({
            "title": item["title"],
            "link": item["link"],
            "text": article_text
        })

        # Небольшая пауза между запросами
        time.sleep(1)

    return sources


def analyze_news(sources, memory):

    sources_text = ""

    for index, source in enumerate(
        sources,
        start=1
    ):

        sources_text += f"""
========================
МАТЕРИАЛ {index}
========================

ЗАГОЛОВОК:
{source['title']}

ССЫЛКА:
{source['link']}

ТЕКСТ СТАТЬИ:
{source['text'][:12000]}

"""

    memory_text = ""

    for item in memory[-100:]:

        memory_text += f"""
TOPIC:
{item.get('topic', '')}

POST:
{item.get('post', '')}

"""

    prompt = f"""
Ты — главный редактор Telegram-канала LilsNews
про EA SPORTS FC 27.

Твоя задача — находить реально важные события
для игроков FC 27.

У тебя есть НЕ только заголовки, но и тексты статей.

МАТЕРИАЛЫ:

{sources_text}

==================================================
УЖЕ ОПУБЛИКОВАНО
==================================================

{memory_text}

==================================================
ГЛАВНОЕ ПРАВИЛО
==================================================

НЕ ПУБЛИКУЙ одну и ту же новость повторно.

Если новая статья просто повторяет уже опубликованную
информацию — ответь:

NO_NEWS

НО:

Если по уже известной теме появилась новая конкретная
информация — её можно опубликовать.

Например:

Старое:
"Destined for Glory Team 2 leaked."

Новое:
"Стали известны конкретные игроки и рейтинги."

Это НОВАЯ информация.

В таком случае публикуй именно новые данные.

==================================================
КОНКРЕТИКА — ОБЯЗАТЕЛЬНО
==================================================

Если статья содержит конкретные данные,
ОБЯЗАТЕЛЬНО используй их.

Особенно:

- имена игроков;
- рейтинг OVR;
- позиция;
- характеристики;
- PlayStyles;
- название карты;
- название промо;
- SBC;
- стоимость SBC;
- требования;
- дата выхода;
- дата окончания;
- Objectives;
- Evolution;
- тактики;
- формации;
- изменения геймплея;
- изменения меты;
- данные о рынке.

НИКОГДА не заменяй конкретные данные
общими словами.

ПЛОХО:

"В сеть слили список игроков."

ХОРОШО:

"В сеть слили:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR"

Если в статье есть эти данные,
они ДОЛЖНЫ попасть в пост.

==================================================
SBC
==================================================

Если появился новый SBC,
постарайся показать:

🃏 Игрок / награда
⭐ Рейтинг
📍 Позиция
💰 Стоимость
⏳ Срок
📌 Главное преимущество карты

Не пиши просто:

"В FC 27 появился новый SBC."

Это бесполезно.

==================================================
META
==================================================

Особый приоритет:

🔥 новые мета-тактики;
🔥 новые формации;
🔥 сильные игроки;
🔥 OP-механики;
🔥 эффективные удары;
🔥 эффективные пасы;
🔥 PlayStyles;
🔥 находки про-игроков;
🔥 изменения меты после патча.

Если появляется информация,
которая может помочь игроку выигрывать матчи,
это высокий приоритет.

==================================================
СЛУХИ
==================================================

Если информация не подтверждена официально:

⚠️ СЛУХ

Но даже для слуха нужно показывать
КОНКРЕТИКУ, если она есть.

Не:

"Инсайдеры раскрыли список игроков."

А:

"В сеть утекли:

🟣 Haaland — 91
🟣 Diani — 88
🟣 Upamecano — 88"

==================================================
СТИЛЬ
==================================================

Пост:

30–100 слов.

Короткий.

Информативный.

Без воды.

Человек должен понять новость
за несколько секунд.

Но НЕ сокращай пост настолько,
чтобы исчезли важные факты.

==================================================
НЕ ПИШИ
==================================================

"Готовьте монеты!"

"Не пропустите!"

"Это изменит игру!"

"Топовая карта!"

"Звёзды на подходе!"

"Следите за обновлениями!"

если это просто пустые фразы.

Не придумывай факты.

==================================================
ИСТОЧНИК
==================================================

НЕ показывай источник.

НЕ добавляй ссылки.

НЕ пиши:

"Источник:"
"Подробнее:"
"Читать далее:"

==================================================
ЕСЛИ НЕТ ХОРОШЕЙ НОВОСТИ
==================================================

Ответ:

NO_NEWS

==================================================
ФОРМАТ
==================================================

Верни только готовый Telegram-пост.

После поста обязательно добавь:

TOPIC: короткое название события

Пример:

⚠️ СЛУХ

🔥 Destined for Glory Team 2

В сеть утекли первые карты второй команды:

🟣 Haaland — 91 OVR
🟣 Diani — 88 OVR
🟣 Upamecano — 88 OVR

Также появились данные о возможных апгрейдах.

TOPIC: Destined for Glory Team 2 players leak
"""

    models = [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]

    for model in models:

        for attempt in range(3):

            try:

                print(
                    f"Trying {model}, "
                    f"attempt {attempt + 1}"
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


def is_duplicate(topic, memory):

    new_topic = topic.lower().strip()

    for item in memory:

        old_topic = item.get(
            "topic",
            ""
        ).lower().strip()

        if not old_topic:
            continue

        if old_topic == new_topic:
            return True

    return False


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
        f"Found {len(news)} news items"
    )

    if not news:

        print("No news found.")

        return

    # НОВОЕ:
    # читаем сами статьи
    sources = prepare_sources(news)

    # Отбрасываем материалы,
    # где вообще не удалось получить текст
    useful_sources = [
        source
        for source in sources
        if source["text"]
    ]

    print(
        f"Successfully read "
        f"{len(useful_sources)} articles"
    )

    if not useful_sources:

        print(
            "Could not read any articles."
        )

        return

    result = analyze_news(
        useful_sources,
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
            "No TOPIC received."
        )

        return

    if is_duplicate(
        topic,
        memory
    ):

        print(
            f"Duplicate blocked: {topic}"
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
        f"Published: {topic}"
    )


if __name__ == "__main__":
    main()
